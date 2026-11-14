# -*- coding: utf-8 -*-
"""FileStudio 无界面自测：验证文件识别、编码、文档提取、路径安全等核心逻辑。

运行：py -3.13 selftest.py
"""
from __future__ import annotations

import base64
import json
import tempfile
import zipfile
from pathlib import Path

import core

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"

passed = 0


def check(label: str, cond: bool) -> None:
    global passed
    if not cond:
        raise AssertionError("FAIL: " + label)
    passed += 1
    print("  OK  " + label)


def make_docx(p: Path, words: list[str]) -> None:
    paras = "".join(f"<w:p><w:r><w:t>{w}</w:t></w:r></w:p>" for w in words)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{paras}</w:body></w:document>'
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", xml)


def make_xlsx(p: Path) -> None:
    shared = f'<?xml version="1.0"?><sst xmlns="{S}"><si><t>姓名</t></si><si><t>分数</t></si><si><t>小明</t></si></sst>'
    sheet = (f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData>'
             '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
             '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>95</v></c></row>'
             '</sheetData></worksheet>')
    wb = f'<?xml version="1.0"?><workbook xmlns="{S}"><sheets><sheet name="成绩表" sheetId="1"/></sheets></workbook>'
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("xl/sharedStrings.xml", shared)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/workbook.xml", wb)


def make_pptx(p: Path) -> None:
    slide = (f'<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="{A}"><p:cSld><p:spTree>'
             f'<a:p><a:r><a:t>第一页标题</a:t></a:r></a:p>'
             f'</p:spTree></p:cSld></p:sld>')
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("ppt/slides/slide1.xml", slide)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "workspace"
        (root / "子目录").mkdir(parents=True)

        # 文本与编码
        txt = root / "笔记.txt"
        txt.write_text("你好 FileStudio\n第二行", encoding="utf-8")
        check("TXT 识别为可编辑文本", core.detect(txt) == "text")
        text, enc, truncated = core.read_text(txt)
        check("UTF-8 文本读取", "你好" in text and enc == "UTF-8" and not truncated)
        core.save_text(txt, "改过的内容", "UTF-8")
        check("文本保存生效", txt.read_text(encoding="utf-8") == "改过的内容")

        gbk = root / "gbk.txt"
        gbk.write_bytes("中文GBK编码".encode("gbk"))
        check("GBK 自动识别", core.read_text(gbk)[1] == "GBK")

        # 图片
        img = root / "示例.png"
        try:
            from PIL import Image
            Image.new("RGB", (24, 16), (200, 60, 60)).save(img)
            check("PNG 识别为图片", core.detect(img) == "image")
            check("图片元信息读取", "24 × 16" in core.image_meta(img))
        except ImportError:
            print("  SKIP 图片（未安装 Pillow）")

        # Office 文档
        docx, xlsx, pptx = root / "报告.docx", root / "表格.xlsx", root / "演示.pptx"
        make_docx(docx, ["标题", "正文内容"])
        make_xlsx(xlsx)
        make_pptx(pptx)
        check("DOCX 识别", core.detect(docx) == "office")
        check("DOCX 文本提取", "标题" in core.extract_office_text(docx) and "正文内容" in core.extract_office_text(docx))
        check("XLSX 文本提取", "成绩表" in core.extract_office_text(xlsx) and "小明" in core.extract_office_text(xlsx))
        check("PPTX 文本提取", "第一页标题" in core.extract_office_text(pptx))

        # 压缩包与二进制
        zp = root / "打包.zip"
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("a.txt", "hello")
        check("ZIP 内容列表", "a.txt" in core.extract_archive_text(zp))
        binary = root / "未知.bin"
        binary.write_bytes(bytes(range(64)))
        check("二进制识别", core.detect(binary) == "binary")
        check("十六进制预览", "00 01 02" in core.hex_dump(binary.read_bytes()[:16]))

        # 路径安全与搜索
        check("根目录内路径通过", core.is_within(root, root / "子目录" / "x.txt"))
        check("越界路径被拒绝", not core.is_within(root, Path(tempfile.gettempdir()) / "other.txt"))
        check("递归搜索文件名", any(p.name == "笔记.txt" for p in core.search_files(root, "笔记")))
        check("同名文件生成唯一路径", core.unique_path(txt).name != txt.name)
        check("文件名清理", core.safe_name('../a:b*?.txt') == "a_b__.txt")
        check("目录列表排序（目录在前）", core.list_dir(root)[0].is_dir())
        check("盘符/快捷位置可用", len(core.list_drives()) >= 1)

        # 配置读写（重定向到临时文件，不动真实配置）
        original = core.CONFIG_PATH
        core.CONFIG_PATH = Path(td) / "config.json"
        try:
            cfg = core.load_config()
            check("默认端口为 1111", cfg["port"] == 1111)
            cfg["bg"] = "#abcdef"
            core.save_config(cfg)
            check("配置持久化", core.load_config()["bg"] == "#abcdef")
        finally:
            core.CONFIG_PATH = original

        # Base64 上传解码路径（服务端使用同样规则）
        check("Base64 编解码", base64.b64decode(base64.b64encode(b"abc")) == b"abc")
        check("JSON 中文序列化", "中文" in json.dumps({"a": "中文"}, ensure_ascii=False))

    print(f"\n全部通过：{passed} 项")


if __name__ == "__main__":
    main()
