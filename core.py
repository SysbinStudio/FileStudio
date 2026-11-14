"""FileStudio 核心逻辑层。

这一层不依赖 tkinter，只做文件识别、读取、写入、预览提取与配置管理，
方便单独测试和复用。
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import struct
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

APP_NAME = "FileStudio"

# ---------------------------------------------------------------- 文件类型

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".ini", ".cfg", ".conf",
    ".py", ".pyw", ".js", ".jsx", ".ts", ".tsx", ".json", ".jsonc", ".json5",
    ".xml", ".html", ".htm", ".css", ".scss", ".less", ".vue", ".svelte",
    ".yaml", ".yml", ".toml", ".csv", ".tsv", ".sql", ".bat", ".cmd", ".ps1",
    ".sh", ".bash", ".zsh", ".c", ".h", ".cpp", ".hpp", ".cs", ".java",
    ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".lua", ".pl", ".r",
    ".svg", ".gitignore", ".env", ".properties", ".tex", ".diff", ".patch",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".jpe", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico", ".ppm", ".pgm"}
OFFICE_EXTS = {".docx", ".xlsx", ".pptx"}
PDF_EXTS = {".pdf"}
ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"}

CATEGORY_LABELS = {
    "text": "文本 / 代码（可编辑）",
    "image": "图片（可查看）",
    "office": "Office 文档（只读预览）",
    "pdf": "PDF 文档（只读预览）",
    "archive": "压缩包（内容列表）",
    "audio": "音频",
    "video": "视频",
    "binary": "二进制 / 未知格式",
    "folder": "文件夹",
}

# 超过这个大小的文本文件不自动载入编辑器
MAX_TEXT_BYTES = 12 * 1024 * 1024
# 十六进制预览读取的字节数
HEX_PREVIEW_BYTES = 64 * 1024


def detect(path: Path) -> str:
    """返回文件类别。"""
    if path.is_dir():
        return "folder"
    ext = path.suffix.lower()
    if ext in TEXT_EXTS or (not ext and path.name.lower() in {
        "makefile", "dockerfile", "readme", "license", ".gitignore", ".env"
    }):
        return "text"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    # 没有扩展名的小文件，尝试当作文本
    if not ext:
        try:
            head = path.open("rb").read(2048)
            if head and b"\x00" not in head:
                head.decode("utf-8")
                return "text"
        except (OSError, UnicodeDecodeError):
            pass
    return "binary"


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024.0
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} PB"


def fmt_time(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return "-"


# ---------------------------------------------------------------- 文本读写

ENCODINGS = ("UTF-8", "UTF-8-SIG", "GBK", "UTF-16", "Big5", "Latin-1")


def sniff_encoding(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return "UTF-8-SIG"
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return "UTF-16"
    try:
        data.decode("utf-8")
        return "UTF-8"
    except UnicodeDecodeError:
        pass
    for enc in ("gbk", "big5"):
        try:
            data.decode(enc)
            return enc.upper()
        except UnicodeDecodeError:
            pass
    return "Latin-1"


def read_text(path: Path, encoding: str | None = None) -> tuple[str, str, bool]:
    """返回 (文本, 实际编码, 是否因过大而截断)。"""
    with path.open("rb") as f:
        data = f.read(MAX_TEXT_BYTES + 1)
    truncated = len(data) > MAX_TEXT_BYTES
    data = data[:MAX_TEXT_BYTES]
    enc = encoding or sniff_encoding(data)
    try:
        text = data.decode(enc, errors="replace")
    except (LookupError, UnicodeDecodeError):
        enc = "Latin-1"
        text = data.decode(enc, errors="replace")
    # 去掉 BOM 字符（UTF-8-SIG 解码时已去掉，UTF-16 已处理）
    return text, enc, truncated


def save_text(path: Path, text: str, encoding: str = "UTF-8") -> None:
    """原子写入，避免写一半损坏原文件。"""
    data = text.encode(encoding, errors="replace")
    tmp = path.with_name(path.name + ".filestudio.tmp")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------- 文档预览

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def extract_office_text(path: Path) -> str:
    """用标准库解析 docx/xlsx/pptx 的文字内容（只读）。"""
    ext = path.suffix.lower()
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if ext == ".docx":
                return _docx_text(z, names)
            if ext == ".xlsx":
                return _xlsx_text(z, names)
            if ext == ".pptx":
                return _pptx_text(z, names)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError, ValueError) as exc:
        return f"无法解析此文档：{exc}\n\n文件可能已损坏，或不是真正的 Office Open XML 格式。"
    return "不支持的文档类型。"


def _docx_text(z: zipfile.ZipFile, names: list[str]) -> str:
    target = "word/document.xml"
    if target not in names:
        return "（文档中没有正文内容）"
    root = ET.fromstring(z.read(target))
    lines = []
    for para in root.iter(W_NS + "p"):
        txt = "".join(t.text or "" for t in para.iter(W_NS + "t"))
        lines.append(txt)
    body = "\n".join(lines).strip()
    return body or "（文档正文为空，可能只包含图片或图形）"


def _xlsx_text(z: zipfile.ZipFile, names: list[str]) -> str:
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.iter(S_NS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(S_NS + "t")))

    sheet_names: list[str] = []
    if "xl/workbook.xml" in names:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        for sh in wb.iter(S_NS + "sheet"):
            sheet_names.append(sh.get("name", "工作表"))

    def sheet_key(n: str) -> int:
        m = re.search(r"sheet(\d+)\.xml$", n)
        return int(m.group(1)) if m else 0

    sheets = sorted(
        (n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
        key=sheet_key,
    )
    if not sheets:
        return "（工作簿中没有工作表）"

    out: list[str] = []
    for idx, sheet_path in enumerate(sheets[:8]):
        title = sheet_names[idx] if idx < len(sheet_names) else f"工作表 {idx + 1}"
        out.append(f"═══ {title} ═══")
        root = ET.fromstring(z.read(sheet_path))
        row_count = 0
        for row in root.iter(S_NS + "row"):
            cells: list[str] = []
            for c in row.iter(S_NS + "c"):
                v = c.find(S_NS + "v")
                ctype = c.get("t")
                if ctype == "s" and v is not None and v.text:
                    try:
                        value = shared[int(v.text)]
                    except (ValueError, IndexError):
                        value = v.text
                elif ctype == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(S_NS + "t"))
                else:
                    value = v.text if v is not None and v.text else ""
                cells.append(value.replace("\t", " ").replace("\n", " "))
                if len(cells) >= 32:
                    break
            out.append("\t".join(cells))
            row_count += 1
            if row_count >= 200:
                out.append("… （仅预览前 200 行）")
                break
        out.append("")
    if len(sheets) > 8:
        out.append(f"… 还有 {len(sheets) - 8} 个工作表未预览")
    return "\n".join(out).strip()


def _pptx_text(z: zipfile.ZipFile, names: list[str]) -> str:
    def slide_key(n: str) -> int:
        m = re.search(r"slide(\d+)\.xml$", n)
        return int(m.group(1)) if m else 0

    slides = sorted(
        (n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
        key=slide_key,
    )
    if not slides:
        return "（演示文稿中没有幻灯片）"
    out = [f"共 {len(slides)} 张幻灯片\n"]
    for i, slide in enumerate(slides[:50], 1):
        root = ET.fromstring(z.read(slide))
        lines = []
        for para in root.iter(A_NS + "p"):
            txt = "".join(t.text or "" for t in para.iter(A_NS + "t"))
            if txt.strip():
                lines.append(txt)
        out.append(f"─── 第 {i} 页 ───")
        out.extend(lines or ["（本页无文字）"])
        out.append("")
    return "\n".join(out).strip()


def extract_pdf_text(path: Path, max_pages: int = 30) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "未安装 pypdf，无法提取 PDF 文本。\n可在命令行执行：py -3.13 -m pip install pypdf"
    try:
        reader = PdfReader(str(path))
        total = len(reader.pages)
        out = [f"共 {total} 页\n"]
        for i, page in enumerate(reader.pages[:max_pages], 1):
            text = (page.extract_text() or "").strip()
            out.append(f"─── 第 {i} 页 ───")
            out.append(text or "（本页无可提取文字，可能是扫描图片）")
            out.append("")
        if total > max_pages:
            out.append(f"… 仅预览前 {max_pages} 页")
        return "\n".join(out).strip()
    except Exception as exc:  # noqa: BLE001 - PDF 结构千奇百怪，统一降级
        return f"无法解析 PDF：{exc}"


def extract_archive_text(path: Path, limit: int = 300) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".zip":
            with zipfile.ZipFile(path) as z:
                infos = z.infolist()
                out = [f"ZIP 压缩包，共 {len(infos)} 个条目\n"]
                for info in infos[:limit]:
                    kind = "目录" if info.is_dir() else "文件"
                    out.append(f"  [{kind}]  {info.filename}    {human_size(info.file_size)}")
        elif ext in {".tar", ".gz", ".tgz", ".bz2", ".xz"}:
            with tarfile.open(path, "r:*") as t:
                members = t.getmembers()
                out = [f"TAR 压缩包，共 {len(members)} 个条目\n"]
                for m in members[:limit]:
                    kind = "目录" if m.isdir() else "文件"
                    out.append(f"  [{kind}]  {m.name}    {human_size(m.size)}")
        else:
            return f"{ext.upper()} 格式暂不支持列出内容，仅显示文件信息。\n（可从右键菜单用系统默认程序打开）"
        if len(out) > limit + 1:
            out.append(f"… 仅显示前 {limit} 个条目")
        return "\n".join(out)
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as exc:
        return f"无法读取压缩包：{exc}"


def hex_dump(data: bytes, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hexpart = " ".join(f"{b:02X}" for b in chunk).ljust(width * 3 - 1)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:08X}   {hexpart}   |{asciipart}|")
    return "\n".join(lines)


def file_info(path: Path) -> str:
    try:
        st = path.stat()
    except OSError as exc:
        return f"无法读取文件信息：{exc}"
    category = detect(path)
    mime = mimetypes.guess_type(path.name)[0] or "未知"
    try:
        with path.open("rb") as f:
            head = f.read(16)
    except OSError:
        head = b""
    magic = head.hex(" ") if head else "-"
    lines = [
        "文件信息",
        "═" * 46,
        f"名称      {path.name}",
        f"路径      {path}",
        f"类别      {CATEGORY_LABELS.get(category, category)}",
        f"扩展名    {path.suffix or '（无）'}",
        f"MIME      {mime}",
        f"大小      {human_size(st.st_size)}  ({st.st_size:,} 字节)",
        f"修改时间  {fmt_time(st.st_mtime)}",
        f"创建时间  {fmt_time(st.st_ctime)}",
        f"文件头    {magic}",
    ]
    if head and st.st_size <= 32 * 1024 * 1024:
        try:
            lines.append(f"MD5       {hashlib.md5(path.read_bytes()).hexdigest()}")
        except OSError:
            pass
    return "\n".join(lines)


# ---------------------------------------------------------------- 目录与搜索

SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".svn", ".hg", ".venv", "venv",
    "$RECYCLE.BIN", "System Volume Information", "Windows", "AppData",
}


def list_dir(path: Path) -> list[Path]:
    try:
        entries = list(path.iterdir())
    except (OSError, PermissionError):
        return []
    entries.sort(key=lambda p: (not p.is_dir(), p.name.casefold()))
    return entries


def sub_dirs(path: Path) -> list[Path]:
    try:
        dirs = [p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except (OSError, PermissionError):
        return []
    dirs.sort(key=lambda p: p.name.casefold())
    return dirs


def search_files(root: Path, keyword: str, limit: int = 400) -> list[Path]:
    """按文件名递归搜索，忽略常见依赖/系统目录。"""
    q = keyword.casefold().strip()
    if not q:
        return []
    found: list[Path] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
            for name in filenames:
                if q in name.casefold():
                    found.append(Path(dirpath) / name)
                    if len(found) >= limit:
                        return found
    except (OSError, PermissionError):
        pass
    return found


def is_within(root: Path, target: Path) -> bool:
    """安全检查：target 是否位于 root 之内，防止越界操作。"""
    try:
        return Path(target).resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError, RuntimeError):
        return False


def unique_path(path: Path) -> Path:
    """若文件已存在，生成 name (1).ext 形式的可用路径。"""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for i in range(1, 10000):
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    return parent / f"{stem} ({datetime.now():%H%M%S}){suffix}"


# ---------------------------------------------------------------- 配置

DEFAULT_CONFIG = {
    "root": "",
    "theme": "dark",
    "bg": "#1b1e26",
    "panel": "#252a35",
    "fg": "#e7ebf3",
    "accent": "#5b8cff",
    "select": "#3a4a6b",
    "bg_image": "",
    "bg_mode": "fit",
    "bg_fade": 0.45,
    "font_size": 13,
    "port": 1111,
    "auto_open": True,
    "admin_user": "admin",
    "admin_hash": "",
    "guest_enabled": True,
    "session_hours": 12,
}

DEFAULT_PASSWORD = "Axiao852"

THEMES = {
    "dark": {"bg": "#1b1e26", "panel": "#252a35", "fg": "#e7ebf3", "accent": "#5b8cff", "select": "#3a4a6b"},
    "light": {"bg": "#f2f4f8", "panel": "#ffffff", "fg": "#20242c", "accent": "#2f6fe0", "select": "#d6e4ff"},
    "eye": {"bg": "#e9f0e4", "panel": "#f6faf2", "fg": "#2c3529", "accent": "#4e8a54", "select": "#c9dfc2"},
    "warm": {"bg": "#2a2320", "panel": "#352c27", "fg": "#f2e8dd", "accent": "#e0914f", "select": "#5d4433"},
}


# ---------------------------------------------------------------- 凭据

def hash_password(password: str, salt: str | None = None) -> str:
    """PBKDF2-SHA256 加盐哈希，返回 "salt$hash"，配置文件里不保存明文密码。"""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), 120_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split("$", 1)
        _, actual = hash_password(password, salt).split("$", 1)
        return secrets.compare_digest(actual, expected)
    except (AttributeError, TypeError, ValueError):
        return False


def public_config(cfg: dict) -> dict:
    """下发给浏览器的配置，剔除密码哈希等敏感字段。"""
    return {k: v for k, v in cfg.items()
            if k not in {"admin_hash", "admin_user", "token"}}


def app_dir() -> Path:
    """统一把配置 / 背景 / 日志放在用户主目录。

    Windows Store 版 Python 与打包后的原生 exe 对 APPDATA 的处理可能不一致，
    用 Path.home() 可以保证两种运行方式共用同一份配置与访问口令。
    """
    d = Path.home() / ".filestudio"
    try:
        d.mkdir(parents=True, exist_ok=True)
        legacy = Path(os.environ.get("APPDATA") or "") / APP_NAME / "config.json"
        target = d / "config.json"
        if not target.exists() and legacy.is_file():
            try:
                shutil.copy2(legacy, target)
            except OSError:
                pass
    except OSError:
        d = Path.home()
    return d


CONFIG_PATH = app_dir() / "config.json"


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.exists():
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update({k: v for k, v in saved.items() if k in DEFAULT_CONFIG})
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(
            json.dumps({k: cfg.get(k, v) for k, v in DEFAULT_CONFIG.items()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def image_meta(path: Path) -> str:
    """用 Pillow 读取图片元信息；失败返回空串。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return f"{im.format or '?'}  ·  {im.width} × {im.height}  ·  {im.mode}"
    except Exception:
        return ""


# ---------------------------------------------------------------- 运行环境

def resource_dir() -> Path:
    """源码运行时返回项目目录；PyInstaller 打包后返回解包临时目录。"""
    import sys
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent


def web_dir() -> Path:
    return resource_dir() / "web"


def list_drives() -> list[dict]:
    """Windows 盘符 / POSIX 根目录，供目录选择器使用。"""
    import string
    out: list[dict] = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            d = Path(f"{letter}:\\")
            try:
                if d.exists():
                    out.append({"name": f"本地磁盘 ({letter}:)", "path": str(d)})
            except OSError:
                continue
    else:
        out.append({"name": "根目录 /", "path": "/"})
    home = Path.home()
    shortcuts = [
        ("主目录", home),
        ("桌面", home / "Desktop"),
        ("文档", home / "Documents"),
        ("下载", home / "Downloads"),
    ]
    for label, p in shortcuts:
        try:
            if p.is_dir():
                out.append({"name": label, "path": str(p)})
        except OSError:
            continue
    return out


def safe_name(name: str) -> str:
    """清理文件名里的非法字符与路径穿越。"""
    name = Path(str(name)).name
    cleaned = "".join("_" if ch in '/\\:*?"<>|' or ord(ch) < 32 else ch for ch in name)
    cleaned = cleaned.strip(" .")
    return cleaned or "untitled"


def to_trash(path: Path) -> tuple[bool, str]:
    """删除到回收站；失败时返回 (False, 原因)，由调用方决定是否永久删除。

    Windows 上优先走 PowerShell 的 VisualBasic FileSystem（系统原生回收站 API），
    send2trash 作为备选，其它平台直接用 send2trash。
    """
    if os.name == "nt":
        try:
            import base64
            import subprocess
            b64 = base64.b64encode(str(path).encode("utf-16-le")).decode("ascii")
            script = (
                "Add-Type -AssemblyName Microsoft.VisualBasic; "
                f"$p = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{b64}')); "
                "$uio = [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs; "
                "$rec = [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin; "
                "if ([IO.Directory]::Exists($p)) { "
                "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p, $uio, $rec) } "
                "else { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, $uio, $rec) }"
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=40,
            )
            if r.returncode == 0 and not path.exists():
                return True, "已移入回收站"
            detail = (r.stderr or r.stdout).decode("utf-8", errors="ignore").strip()[:180]
            return False, f"回收站操作失败：{detail or '未知原因'}"
        except Exception as exc:  # noqa: BLE001
            return False, f"回收站接口异常：{exc}"
    try:
        from send2trash import send2trash
        send2trash(str(path))
        return True, "已移入回收站"
    except Exception as exc:  # noqa: BLE001
        return False, f"回收站不可用：{exc}"


def permanent_delete(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()

