# -*- coding: utf-8 -*-
"""FileStudio —— 局域网 HTTP 文件管理服务。

启动后浏览器打开即可使用：文件浏览 / 编辑 / 图片查看 / 自定义背景。
管理员登录后拥有完整读写权限；游客无需密码，仅可只读预览。
监听 0.0.0.0:1111（可用 --host / --port 调整）。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import core

try:
    from PIL import Image, ImageOps
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import pypdf  # noqa: F401
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

STATE: dict = {"root": Path.home(), "cfg": {}, "port": 1111, "host": "0.0.0.0"}
MAX_BODY = 120 * 1024 * 1024
MAX_UPLOAD = 60 * 1024 * 1024

# ---------------------------------------------------------------- 会话与权限

CTX = threading.local()                 # 每请求的临时上下文（IP / 角色 / 待写入 Cookie）
SESSIONS: dict[str, dict] = {}          # sid -> {role, exp}
SESSION_LOCK = threading.Lock()
LOGIN_FAILS: dict[str, list] = {}       # ip -> [连续失败次数, 解禁时间]
COOKIE_NAME = "fs_sid"
MAX_FAILS = 5
BLOCK_SECONDS = 60


def session_ttl() -> int:
    try:
        hours = float(STATE["cfg"].get("session_hours") or 12)
    except (TypeError, ValueError):
        hours = 12
    return int(min(168, max(1, hours)) * 3600)


def new_session(role: str = "admin") -> str:
    sid = secrets.token_urlsafe(32)
    now = time.time()
    with SESSION_LOCK:
        for key in [k for k, v in SESSIONS.items() if v["exp"] < now]:
            SESSIONS.pop(key, None)
        SESSIONS[sid] = {"role": role, "exp": now + session_ttl()}
    return sid


def session_role(sid: str | None) -> str | None:
    if not sid:
        return None
    with SESSION_LOCK:
        rec = SESSIONS.get(sid)
        if not rec:
            return None
        if rec["exp"] < time.time():
            SESSIONS.pop(sid, None)
            return None
        return rec["role"]


def drop_session(sid: str | None) -> None:
    if sid:
        with SESSION_LOCK:
            SESSIONS.pop(sid, None)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, need_login: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.need_login = need_login


@dataclass
class Raw:
    data: bytes
    ctype: str
    headers: dict = field(default_factory=dict)


@dataclass
class FileResponse:
    path: Path
    ctype: str
    headers: dict = field(default_factory=dict)


def log_error(text: str) -> None:
    try:
        p = core.app_dir() / "error.log"
        with p.open("a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}\n")
    except OSError:
        pass


def mime_of(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


# ---------------------------------------------------------------- 公共校验

def need_root() -> Path:
    root = STATE["root"]
    if not root.is_dir():
        raise ApiError("当前存储空间不可用，请重新选择目录", 400)
    return root


def inside(path_str: str | None, must_exist: bool = False) -> Path:
    if not path_str:
        raise ApiError("缺少 path 参数", 400)
    p = Path(path_str)
    if not core.is_within(need_root(), p):
        raise ApiError("路径超出当前存储空间范围，操作被拒绝", 403)
    if must_exist and not p.exists():
        raise ApiError(f"文件不存在：{p.name}", 404)
    return p


def describe(p: Path) -> dict:
    try:
        st = p.stat()
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, 0
    is_dir = p.is_dir()
    cat = core.detect(p)
    return {
        "name": p.name,
        "path": str(p),
        "is_dir": is_dir,
        "ext": p.suffix.lower(),
        "category": cat,
        "label": "文件夹" if is_dir else core.CATEGORY_LABELS.get(cat, "文件"),
        "short_label": "文件夹" if is_dir else (p.suffix.lower().lstrip(".").upper() or "文件"),
        "size": size,
        "size_h": "" if is_dir else core.human_size(size),
        "mtime": core.fmt_time(mtime),
        "mtime_raw": mtime,
        "editable": cat == "text",
    }


# ---------------------------------------------------------------- API

def api_state(q: dict, body: dict) -> dict:
    cfg = STATE["cfg"]
    role = getattr(CTX, "role", "guest")
    guest_enabled = bool(STATE.get("guest_enabled", cfg.get("guest_enabled", True)))
    return {
        "root": str(STATE["root"]),
        "root_exists": STATE["root"].is_dir(),
        "home": str(Path.home()),
        "drives": core.list_drives() if getattr(CTX, "role", "guest") == "admin" else [],
        "config": core.public_config(cfg),
        "port": STATE["port"],
        "role": role,
        "guest_enabled": guest_enabled,
        "login_required": role == "none",
        "session_hours": cfg.get("session_hours", 12),
        "version": "1.1",
        "caps": {"pillow": HAS_PIL, "pypdf": HAS_PYPDF},
    }


def api_login(q: dict, body: dict) -> dict:
    """账号密码登录，成功后签发会话并写入 HttpOnly Cookie。"""
    ip = getattr(CTX, "ip", "?")
    now = time.time()
    fails, blocked_until = LOGIN_FAILS.get(ip, [0, 0.0])
    if blocked_until > now:
        raise ApiError(f"失败次数过多，请 {int(blocked_until - now) + 1} 秒后再试", 429)
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    cfg = STATE["cfg"]
    expected_user = str(cfg.get("admin_user") or "admin")
    user_ok = secrets.compare_digest(username, expected_user)
    pass_ok = core.verify_password(password, str(cfg.get("admin_hash") or ""))
    if not (user_ok and pass_ok):
        fails += 1
        LOGIN_FAILS[ip] = [0, now + BLOCK_SECONDS] if fails >= MAX_FAILS else [fails, 0.0]
        raise ApiError("账号或密码不正确", 401)
    LOGIN_FAILS.pop(ip, None)
    sid = new_session("admin")
    CTX.new_sid = sid
    CTX.role = "admin"
    return {"sid": sid, "role": "admin", "username": username,
            "session_hours": cfg.get("session_hours", 12)}


def api_logout(q: dict, body: dict) -> dict:
    drop_session(getattr(CTX, "sid", ""))
    CTX.clear_sid = True
    CTX.role = "guest" if STATE.get("guest_enabled", True) else "none"
    return {"logged_out": True}


def api_guest(q: dict, body: dict) -> dict:
    """游客入口：不需要凭据，服务端只把它当成一个显式的只读身份确认。"""
    if not STATE.get("guest_enabled", True):
        raise ApiError("管理员已关闭游客预览，请登录", 403, need_login=True)
    CTX.role = "guest"
    return {"role": "guest", "readonly": True}


def api_browse(q: dict, body: dict) -> dict:
    """目录选择器：可浏览任意本机目录（只读目录名），不受当前存储空间限制。"""
    raw = q.get("path", "")
    if not raw:
        return {"path": "", "parent": "", "items": [], "drives": core.list_drives()}
    p = Path(raw)
    if not p.is_dir():
        raise ApiError("目录不存在或无法访问", 404)
    try:
        dirs = core.sub_dirs(p)
    except PermissionError:
        raise ApiError("没有权限访问该目录", 403)
    parent = str(p.parent) if p.parent != p else ""
    return {
        "path": str(p),
        "parent": parent,
        "items": [{"name": d.name, "path": str(d)} for d in dirs],
        "drives": core.list_drives(),
    }


def api_set_root(q: dict, body: dict) -> dict:
    raw = body.get("path", "")
    p = Path(raw)
    if not p.is_dir():
        raise ApiError(f"目录不存在或无法访问：{raw}", 400)
    STATE["root"] = p.resolve()
    STATE["cfg"]["root"] = str(STATE["root"])
    core.save_config(STATE["cfg"])
    return {"root": str(STATE["root"])}


def api_config(q: dict, body: dict) -> dict:
    allowed = {"theme", "bg", "panel", "fg", "accent", "select",
               "bg_image", "bg_mode", "bg_fade", "font_size", "port", "auto_open"}
    cfg = STATE["cfg"]
    for key, value in body.items():
        if key not in allowed:
            continue
        if key in {"bg", "panel", "fg", "accent", "select"}:
            if not (isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value)):
                continue
        elif key == "bg_fade":
            try:
                value = min(0.95, max(0.0, float(value)))
            except (TypeError, ValueError):
                continue
        elif key in {"font_size", "port"}:
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if key == "font_size":
                value = min(24, max(10, value))
            else:
                value = min(65535, max(1, value))
        elif key == "bg_mode" and value not in {"fit", "stretch", "center", "tile"}:
            continue
        elif key == "auto_open":
            value = bool(value)
        elif key == "theme" and not isinstance(value, str):
            continue
        cfg[key] = value
    core.save_config(cfg)
    return {"config": core.public_config(cfg)}


def api_list(q: dict, body: dict) -> dict:
    p = inside(q.get("path") or str(STATE["root"]), must_exist=True)
    if not p.is_dir():
        raise ApiError("这不是一个目录", 400)
    items = [describe(x) for x in core.list_dir(p)]
    root = need_root()
    parent = str(p.parent) if p != root and core.is_within(root, p.parent) else ""
    return {"path": str(p), "parent": parent, "items": items, "count": len(items)}


def api_tree(q: dict, body: dict) -> dict:
    p = inside(q.get("path"), must_exist=True)
    if not p.is_dir():
        raise ApiError("这不是一个目录", 400)
    dirs = core.sub_dirs(p)
    return {"path": str(p), "items": [
        {"name": d.name, "path": str(d), "has_children": bool(core.sub_dirs(d))}
        for d in dirs
    ]}


def api_search(q: dict, body: dict) -> dict:
    keyword = q.get("q", "").strip()
    if not keyword:
        return {"items": [], "keyword": ""}
    results = core.search_files(need_root(), keyword)
    return {"items": [describe(x) for x in results], "keyword": keyword,
            "truncated": len(results) >= 400}


def api_file(q: dict, body: dict) -> dict:
    p = inside(q.get("path"), must_exist=True)
    if p.is_dir():
        raise ApiError("请选择一个文件", 400)
    cat = core.detect(p)
    res = describe(p)
    res.update({
        "info": core.file_info(p),
        "editable": cat == "text",
        "content": "",
        "encoding": "",
        "truncated": False,
        "image_meta": "",
        "image_url": "",
        "pdf_url": "",
    })
    enc_arg = q.get("encoding") or ""
    if cat == "text":
        text, enc, truncated = core.read_text(p, enc_arg or None)
        res.update(content=text, encoding=enc, truncated=truncated)
    elif cat == "image":
        res.update(image_meta=core.image_meta(p),
                   image_url="/api/image?path=" + quote(str(p), safe=""))
    elif cat == "office":
        res["content"] = "── 文档内容（只读预览，编辑不会破坏原文件结构） ──\n\n" + core.extract_office_text(p)
    elif cat == "pdf":
        res["pdf_url"] = "/api/raw?path=" + quote(str(p), safe="")
        res["content"] = core.extract_pdf_text(p)
    elif cat == "archive":
        res["content"] = core.extract_archive_text(p)
    else:
        with p.open("rb") as f:
            head = f.read(core.HEX_PREVIEW_BYTES)
        res["content"] = ("此格式不提供直接编辑，以下是前 64 KB 的十六进制 / ASCII 预览。\n"
                          "可用「下载」或「用系统默认程序打开」处理。\n\n" + core.hex_dump(head))
    return res


def api_save(q: dict, body: dict) -> dict:
    p = inside(body.get("path"), must_exist=True)
    mtime = body.get("mtime")
    if mtime:
        try:
            if abs(p.stat().st_mtime - float(mtime)) > 0.002:
                raise ApiError("文件已在外部被修改，请先重新载入再保存，避免覆盖他人改动", 409)
        except (TypeError, ValueError):
            pass
    enc = body.get("encoding") or "UTF-8"
    core.save_text(p, body.get("content", ""), enc)
    return {"path": str(p), "mtime": p.stat().st_mtime, "size": p.stat().st_size}


def api_saveas(q: dict, body: dict) -> dict:
    target_raw = body.get("target", "")
    if not target_raw:
        raise ApiError("请输入目标文件名或路径", 400)
    t = Path(target_raw)
    if not t.is_absolute():
        base = inside(body.get("dir") or str(STATE["root"]), must_exist=True)
        t = base / core.safe_name(target_raw)
    if not core.is_within(need_root(), t):
        raise ApiError("目标路径必须在当前存储空间内", 403)
    if t.is_dir():
        raise ApiError("目标是一个文件夹", 400)
    if t.exists() and not body.get("overwrite"):
        raise ApiError("目标文件已存在，请勾选「覆盖」或换一个名称", 409)
    t.parent.mkdir(parents=True, exist_ok=True)
    core.save_text(t, body.get("content", ""), body.get("encoding") or "UTF-8")
    return {"path": str(t), "name": t.name}


def api_create(q: dict, body: dict) -> dict:
    d = inside(body.get("dir") or str(STATE["root"]), must_exist=True)
    if not d.is_dir():
        raise ApiError("目标不是文件夹", 400)
    name = core.safe_name(body.get("name", ""))
    t = d / name
    if t.exists():
        raise ApiError("同名文件 / 文件夹已存在", 409)
    if body.get("kind") == "folder":
        t.mkdir()
    else:
        t.touch()
    return {"path": str(t), "name": name}


def api_rename(q: dict, body: dict) -> dict:
    p = inside(body.get("path"), must_exist=True)
    name = core.safe_name(body.get("name", ""))
    t = p.parent / name
    if t.exists():
        raise ApiError("同名文件 / 文件夹已存在", 409)
    p.rename(t)
    return {"path": str(t), "name": name}


def api_delete(q: dict, body: dict) -> dict:
    p = inside(body.get("path"), must_exist=True)
    if p == need_root():
        raise ApiError("不能删除存储空间根目录", 400)
    permanent = bool(body.get("permanent"))
    if permanent:
        core.permanent_delete(p)
        return {"deleted": str(p), "message": "已永久删除"}
    ok, msg = core.to_trash(p)
    if not ok:
        raise ApiError(msg + "；若确认要永久删除，请在弹窗中选择「永久删除」", 409)
    return {"deleted": str(p), "message": msg}


def decode_upload(body: dict) -> tuple[str, bytes]:
    name = core.safe_name(body.get("name", "upload.bin"))
    data = body.get("data", "")
    if "," in data[:100]:
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data, validate=False)
    except Exception as exc:
        raise ApiError(f"文件数据解码失败：{exc}", 400)
    if len(raw) > MAX_UPLOAD:
        raise ApiError("单个文件超过 60 MB，请直接复制到存储空间目录", 413)
    return name, raw


def api_upload(q: dict, body: dict) -> dict:
    d = inside(body.get("dir") or str(STATE["root"]), must_exist=True)
    name, raw = decode_upload(body)
    t = core.unique_path(d / name)
    t.write_bytes(raw)
    return {"path": str(t), "name": t.name, "size": len(raw)}


def api_upload_bg(q: dict, body: dict) -> dict:
    name, raw = decode_upload(body)
    folder = core.app_dir() / "backgrounds"
    folder.mkdir(parents=True, exist_ok=True)
    t = folder / name
    t.write_bytes(raw)
    STATE["cfg"]["bg_image"] = str(t)
    core.save_config(STATE["cfg"])
    return {"path": str(t), "url": "/api/bg?t=" + str(int(datetime.now().timestamp()))}


def api_raw(q: dict, body: dict) -> Raw | FileResponse:
    p = inside(q.get("path"), must_exist=True)
    headers = {"Content-Disposition": f"inline; filename*=UTF-8''{quote(p.name)}"}
    return FileResponse(p, mime_of(p), headers)


def api_download(q: dict, body: dict) -> Raw | FileResponse:
    p = inside(q.get("path"), must_exist=True)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(p.name)}",
        "Cache-Control": "no-store",
    }
    return FileResponse(p, "application/octet-stream", headers)


def api_image(q: dict, body: dict) -> Raw | FileResponse:
    p = inside(q.get("path"), must_exist=True)
    ext = p.suffix.lower()
    # 动图 / 矢量图直接交给浏览器，保留动画
    if ext in {".gif", ".webp", ".svg", ".avif", ".apng"}:
        return FileResponse(p, mime_of(p), {"Cache-Control": "max-age=60"})
    if HAS_PIL:
        try:
            with Image.open(p) as src:
                im = ImageOps.exif_transpose(src)
                im.load()
                if max(im.size) > 6000:
                    im.thumbnail((6000, 6000), Image.Resampling.LANCZOS)
                if im.mode not in {"RGB", "RGBA", "L", "LA"}:
                    im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
                buf = io.BytesIO()
                im.save(buf, "PNG")
                return Raw(buf.getvalue(), "image/png", {"Cache-Control": "max-age=60"})
        except Exception as exc:
            log_error(f"图片转换失败 {p}: {exc}")
    return FileResponse(p, mime_of(p), {"Cache-Control": "max-age=60"})


def api_bg(q: dict, body: dict) -> Raw | FileResponse:
    raw = STATE["cfg"].get("bg_image") or ""
    p = Path(raw)
    if not raw or not p.is_file():
        raise ApiError("尚未设置背景图片", 404)
    if HAS_PIL:
        try:
            with Image.open(p) as src:
                im = ImageOps.exif_transpose(src).convert("RGB")
                im.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=85)
                return Raw(buf.getvalue(), "image/jpeg", {"Cache-Control": "max-age=30"})
        except Exception as exc:
            log_error(f"背景图转换失败 {p}: {exc}")
    return FileResponse(p, mime_of(p), {"Cache-Control": "max-age=30"})


def api_open_system(q: dict, body: dict) -> dict:
    p = inside(body.get("path"), must_exist=True)
    try:
        if sys.platform == "win32":
            os.startfile(str(p))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except Exception as exc:
        raise ApiError(f"无法打开：{exc}", 500)
    return {"opened": str(p)}


def api_open_explorer(q: dict, body: dict) -> dict:
    raw = body.get("path") or str(STATE["root"])
    p = Path(raw)
    if not core.is_within(need_root(), p):
        raise ApiError("路径超出当前存储空间范围", 403)
    if not p.exists():
        p = p.parent
    try:
        if sys.platform == "win32":
            if p.is_dir():
                subprocess.Popen(["explorer", str(p)])
            else:
                subprocess.Popen(["explorer", "/select,", str(p)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])
    except Exception as exc:
        raise ApiError(f"无法打开资源管理器：{exc}", 500)
    return {"opened": str(p)}


GET_ROUTES = {
    "/api/state": api_state,
    "/api/browse": api_browse,
    "/api/list": api_list,
    "/api/tree": api_tree,
    "/api/search": api_search,
    "/api/file": api_file,
    "/api/raw": api_raw,
    "/api/download": api_download,
    "/api/image": api_image,
    "/api/bg": api_bg,
}

POST_ROUTES = {
    "/api/login": api_login,
    "/api/logout": api_logout,
    "/api/guest": api_guest,
    "/api/root": api_set_root,
    "/api/config": api_config,
    "/api/save": api_save,
    "/api/saveas": api_saveas,
    "/api/create": api_create,
    "/api/rename": api_rename,
    "/api/delete": api_delete,
    "/api/upload": api_upload,
    "/api/upload-bg": api_upload_bg,
    "/api/open-system": api_open_system,
    "/api/open-explorer": api_open_explorer,
}

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}

# 无需登录即可调用的接口
PUBLIC_ROUTES = {"/api/login", "/api/logout", "/api/guest", "/api/state"}
# 游客可调用的只读接口（浏览、预览、下载、背景）
GUEST_GET_ROUTES = {
    "/api/list", "/api/tree", "/api/search", "/api/file",
    "/api/raw", "/api/image", "/api/download", "/api/bg",
}
# 仅管理员：目录选择器会遍历任意本机目录，且所有 POST 都是写操作 / 会启动本机程序
ADMIN_ONLY_GET_ROUTES = {"/api/browse"}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "FileStudio/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        try:
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))
        except Exception:
            pass

    def _check_origin(self) -> None:
        """同源校验：允许任意局域网地址访问，但阻止第三方站点伪造请求。"""
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and host:
            origin_host = urlparse(origin).netloc
            if origin_host and origin_host != host:
                raise ApiError("跨站请求已被拒绝", 403)

    def _sid_from_request(self, url) -> str:
        """会话 ID 可来自请求头、HttpOnly Cookie 或 URL 参数（供 <img>/<iframe> 使用）。"""
        given = self.headers.get("X-FileStudio-Session", "").strip()
        if given:
            return given
        raw = self.headers.get("Cookie", "")
        if raw:
            try:
                jar = SimpleCookie()
                jar.load(raw)
                item = jar.get(COOKIE_NAME)
                if item and item.value:
                    return item.value
            except Exception:  # noqa: BLE001
                pass
        return parse_qs(url.query, keep_blank_values=True).get("sid", [""])[0]

    def _check_auth(self, url, method: str) -> None:
        """管理员 = 完整读写；游客 = 只读 GET；关闭游客后必须登录。"""
        path = url.path
        sid = self._sid_from_request(url)
        CTX.sid = sid
        CTX.ip = self.client_address[0] if self.client_address else "?"
        role = session_role(sid)
        guest_enabled = bool(STATE.get("guest_enabled", True))
        CTX.role = "admin" if role == "admin" else ("guest" if guest_enabled else "none")

        if path in PUBLIC_ROUTES:
            return
        if CTX.role == "none":
            raise ApiError("请先登录管理员账号", 401, need_login=True)
        if method == "POST":
            if CTX.role != "admin":
                raise ApiError("游客仅可预览文件，请登录管理员后再执行此操作", 403, need_login=True)
            return
        if path in ADMIN_ONLY_GET_ROUTES and CTX.role != "admin":
            raise ApiError("游客不能使用目录选择器，请登录管理员", 403, need_login=True)
        if path not in GUEST_GET_ROUTES and path not in ADMIN_ONLY_GET_ROUTES:
            raise ApiError(f"未知接口 {path}", 404)

    def send_bytes(self, code: int, data: bytes, ctype: str, headers: dict | None = None) -> None:
        extra = dict(headers or {})
        extra.setdefault("Cache-Control", "no-store")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        if getattr(CTX, "new_sid", None):
            self.send_header("Set-Cookie", f"{COOKIE_NAME}={CTX.new_sid}; Path=/; HttpOnly; "
                                         f"SameSite=Lax; Max-Age={session_ttl()}")
        if getattr(CTX, "clear_sid", False):
            self.send_header("Set-Cookie", f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_file(self, resp: FileResponse) -> None:
        size = resp.path.stat().st_size
        extra = dict(resp.headers)
        extra.setdefault("Cache-Control", "no-store")
        self.send_response(200)
        self.send_header("Content-Type", resp.ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        with resp.path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile, length=1024 * 256)

    def send_json(self, obj: dict, code: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_bytes(code, data, "application/json; charset=utf-8")

    def read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            raise ApiError("请求体过大", 413)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError("请求体不是合法 JSON", 400)
        if not isinstance(obj, dict):
            raise ApiError("请求体必须是 JSON 对象", 400)
        return obj

    def do_GET(self) -> None:
        self.handle_request("GET")

    def do_POST(self) -> None:
        self.handle_request("POST")

    def do_HEAD(self) -> None:
        self.handle_request("GET")

    def handle_request(self, method: str) -> None:
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        for key in ("sid", "ip", "role", "new_sid", "clear_sid"):
            if hasattr(CTX, key):
                delattr(CTX, key)
        try:
            # 仅本机可调用，用于无控制台 exe 优雅停止服务（无需关闭黑框）
            if url.path == "/api/shutdown":
                peer = self.client_address[0] if self.client_address else ""
                if peer not in ("127.0.0.1", "::1", "localhost"):
                    return self.send_json({"ok": False, "error": "仅本机可停止服务"}, 403)
                self.send_json({"ok": True, "msg": "服务已停止"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self._check_origin()
            if method == "GET" and url.path in STATIC_FILES:
                name, ctype = STATIC_FILES[url.path]
                path = core.web_dir() / name
                if not path.is_file():
                    raise ApiError(f"缺少前端资源 {name}，请检查打包是否包含 web 目录", 500)
                return self.send_bytes(200, path.read_bytes(), ctype,
                                       {"Cache-Control": "no-cache"})
            if url.path.startswith("/api/"):
                self._check_auth(url, method)
                routes = GET_ROUTES if method == "GET" else POST_ROUTES
                fn = routes.get(url.path)
                if fn is None:
                    raise ApiError(f"未知接口 {url.path}", 404)
                body = self.read_body() if method == "POST" else {}
                result = fn(query, body)
                if isinstance(result, Raw):
                    return self.send_bytes(200, result.data, result.ctype, result.headers)
                if isinstance(result, FileResponse):
                    return self.send_file(result)
                return self.send_json({"ok": True, **result})
            raise ApiError("页面不存在", 404)
        except ApiError as exc:
            try:
                self.send_json({"ok": False, "error": str(exc),
                                "need_login": exc.need_login}, exc.status)
            except (BrokenPipeError, ConnectionResetError):
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            text = traceback.format_exc()
            log_error(text)
            try:
                self.send_json({"ok": False, "error": f"服务端错误：{exc}"}, 500)
            except (BrokenPipeError, ConnectionResetError):
                pass


def pick_port(host: str, start: int) -> int:
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"找不到可用端口（从 {start} 起尝试了 30 个）")


def lan_ips() -> list[str]:
    """列出本机可用于局域网访问的 IPv4 地址。"""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    if not ips:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                if not ip.startswith("127."):
                    ips.add(ip)
        except OSError:
            pass
    return sorted(ips)


class _Tee:
    """把输出同时写到多个流（控制台 + 日志文件），任一流为 None 时自动忽略。"""

    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
            except Exception:
                pass
        # 立即落盘：避免块缓冲导致日志/控制台看不到内容（尤其无控制台模式）
        self.flush()

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


def setup_console() -> None:
    """统一 UTF-8 输出 + 日志落盘。

    - 有控制台（源码 / --console 打包）：正常打印到窗口。
    - 无控制台（--noconsole 打包，双击 exe）：sys.stdout 为 None，
      此时不再打印，仅把启动信息写入日志文件，避免出现黑框。
    """
    # 中文 Windows 默认编码为 GBK；日志与控制台统一用 GBK（而非 UTF-8），
    # 这样无控制台（关闭黑框）模式下落盘的 filestudio.log 用记事本打开中文不乱码。
    _ENC = "gbk" if sys.platform == "win32" else "utf-8"
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(936)
        except Exception:
            pass
    # 控制台流（无窗口时为 None）
    out, err = sys.stdout, sys.stderr
    for stream in (out, err):
        if stream is not None:
            try:
                stream.reconfigure(encoding=_ENC, errors="replace")
            except (AttributeError, OSError, ValueError):
                pass
    # 日志文件：无论是否有窗口都写一份，方便排查；用 GBK 编码，且每次启动覆盖，
    # 避免旧 UTF-8 日志与新 GBK 日志混排导致记事本打开时中文乱码。
    try:
        log_path = core.app_dir() / "filestudio.log"
        logf = open(log_path, "w", encoding=_ENC)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        logf.write(f"\n[{ts}] ===== FileStudio 启动 =====\n")
        logf.flush()
    except Exception:
        logf = None
    # 无论是否有真实控制台，都把输出汇入日志文件（无控制台时占位流会被 _Tee 自动忽略）。
    # 这样 main() 里的 print 启动横幅一定能落盘到 filestudio.log。
    sys.stdout = _Tee(out, logf)
    sys.stderr = _Tee(err, logf)


def main() -> None:
    setup_console()
    parser = argparse.ArgumentParser(description="FileStudio 局域网文件管理服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0（局域网可访问）")
    parser.add_argument("--port", type=int, help="监听端口，默认 1111")
    parser.add_argument("--strict-port", action="store_true", help="端口被占用时直接报错，不自动顺延")
    parser.add_argument("--root", help="启动时使用的存储空间目录")
    parser.add_argument("--user", help="管理员账号，默认 admin")
    parser.add_argument("--password", help="重设管理员密码并写入配置（默认 Axiao852）")
    parser.add_argument("--no-guest", action="store_true", help="关闭游客预览，所有访问都必须登录")
    parser.add_argument("--hide-password", action="store_true", help="启动窗口不显示管理员密码")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    cfg = core.load_config()
    STATE["cfg"] = cfg
    cfg["admin_user"] = (args.user or cfg.get("admin_user") or "admin").strip() or "admin"
    if args.password:
        cfg["admin_hash"] = core.hash_password(args.password)
    elif not cfg.get("admin_hash"):
        cfg["admin_hash"] = core.hash_password(core.DEFAULT_PASSWORD)
    STATE["guest_enabled"] = (not args.no_guest) and bool(cfg.get("guest_enabled", True))
    core.save_config(cfg)

    root = Path(args.root or cfg.get("root") or Path.home())
    STATE["root"] = root.resolve() if root.is_dir() else Path.home()

    host = args.host or "0.0.0.0"
    STATE["host"] = host
    wanted = args.port or int(cfg.get("port") or 1111)
    port = pick_port(host, wanted)
    if port != wanted and args.strict_port:
        raise SystemExit(f"端口 {wanted} 已被占用（加 --port 指定其他端口）")
    STATE["port"] = port

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    # 本机自动打开的链接携带一次性会话，双击 exe 即已是管理员；局域网链接不含该密钥
    boot_sid = new_session("admin")
    url = f"http://127.0.0.1:{port}/?sid={boot_sid}"

    print("=" * 74)
    print("  文件管家 FileStudio  已启动（监听 0.0.0.0，局域网可访问）")
    print(f"  本机（自动登录）： {url}")
    for ip in lan_ips():
        print(f"  局域网：           http://{ip}:{port}/   （打开后选择管理员登录 / 游客预览）")
    print(f"  管理员账号：{cfg['admin_user']}")
    if args.hide_password:
        print("  管理员密码：已隐藏（忘记时可用 --password 新密码 重设）")
    elif args.password:
        print(f"  管理员密码：{args.password}   （本次已写入配置）")
    else:
        print(f"  管理员密码：{core.DEFAULT_PASSWORD}   （可用 --password 新密码 修改）")
    print("  游客预览：" + ("已启用 · 免登录，仅可读（浏览 / 预览 / 下载）"
                          if STATE["guest_enabled"] else "已关闭 · 必须登录（--no-guest）"))
    print(f"  存储空间：{STATE['root']}")
    print(f"  配置文件：{core.CONFIG_PATH}")
    print(f"  监听地址：{host}:{port}" + ("（指定端口被占用，已顺延）" if port != wanted else ""))
    print(f"  图片增强：{'Pillow 已启用' if HAS_PIL else '未安装 Pillow'}"
          f"   PDF 文本：{'pypdf 已启用' if HAS_PYPDF else '未安装 pypdf'}")
    print("  会话有效期 %s 小时；含 sid 的本机链接等同于管理员身份，请勿转发给他人" % cfg.get("session_hours", 12))
    print("  停止服务：界面右上角「设置 → 退出服务」，或在任务管理器结束 FileStudio.exe")
    print("=" * 74, flush=True)

    if not args.no_browser and cfg.get("auto_open", True):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
