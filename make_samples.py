# -*- coding: utf-8 -*-
"""生成一批示例文件到 sample/，方便首次启动时直接体验各类文件的查看与编辑。

运行：py -3.13 make_samples.py
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent / "sample"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def main() -> None:
    (BASE / "子目录").mkdir(parents=True, exist_ok=True)

    (BASE / "欢迎.txt").write_text(
        "欢迎使用 文件管家 FileStudio\n"
        "==========================\n"
        "这是一个可以直接编辑的文本文件，改完后点「保存」或按 Ctrl+S。\n"
        "服务监听 0.0.0.0:1111，局域网里的手机 / 其他电脑也能访问。\n",
        encoding="utf-8",
    )
    (BASE / "待办.md").write_text(
        "# 待办清单\n\n- [x] 选择存储空间\n- [x] 自定义背景\n- [ ] 试试图片缩放\n- [ ] 预览 Word / Excel / PPT\n",
        encoding="utf-8",
    )
    (BASE / "示例.json").write_text(
        json.dumps({"应用": "FileStudio", "端口": 1111, "功能": ["编辑", "预览", "背景", "上传"]},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (BASE / "示例.csv").write_text("姓名,部门,分数\n小明,技术,95\n小红,设计,88\n", encoding="utf-8-sig")
    (BASE / "子目录" / "说明.txt").write_text("这里是一个子目录，可以在左侧目录树里展开。\n", encoding="utf-8")

    # 图片：PNG / GIF / WEBP / TIFF / ICO
    im = Image.new("RGB", (640, 360), (28, 34, 48))
    d = ImageDraw.Draw(im)
    for i in range(0, 640, 8):
        c = (int(40 + i * 0.18), int(70 + i * 0.12), int(160 + i * 0.08))
        d.line([(i, 0), (i, 360)], fill=c, width=8)
    d.ellipse((420, 40, 580, 200), fill=(255, 196, 92))
    d.rectangle((60, 230, 360, 300), fill=(91, 140, 255))
    d.text((80, 255), "FileStudio  Image  Preview", fill=(255, 255, 255))
    im.save(BASE / "渐变示例.png")
    im.save(BASE / "渐变示例.tif")
    im.save(BASE / "渐变示例.webp")
    im.resize((64, 64)).save(BASE / "图标.ico")
    try:
        frames = [Image.new("RGB", (200, 120), c) for c in [(220, 80, 80), (80, 180, 120), (80, 120, 220)]]
        frames[0].save(BASE / "动图.gif", save_all=True, append_images=frames[1:], duration=400, loop=0)
    except Exception:
        pass

    # DOCX
    paras = "".join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in ["项目说明", "这是用标准库解析出来的 Word 正文。", "只读预览，不会破坏原文件。"])
    with zipfile.ZipFile(BASE / "项目说明.docx", "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="{W_NS}"><w:body>{paras}</w:body></w:document>')

    # XLSX
    shared = (f'<?xml version="1.0"?><sst xmlns="{S_NS}"><si><t>姓名</t></si><si><t>部门</t></si>'
              '<si><t>分数</t></si><si><t>小明</t></si><si><t>小红</t></si></sst>')
    sheet = (f'<?xml version="1.0"?><worksheet xmlns="{S_NS}"><sheetData>'
             '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>'
             '<row r="2"><c r="A2" t="s"><v>3</v></c><c r="B2" t="inlineStr"><is><t>技术</t></is></c><c r="C2"><v>95</v></c></row>'
             '<row r="3"><c r="A3" t="s"><v>4</v></c><c r="B3" t="inlineStr"><is><t>设计</t></is></c><c r="C3"><v>88</v></c></row>'
             '</sheetData></worksheet>')
    wb = f'<?xml version="1.0"?><workbook xmlns="{S_NS}"><sheets><sheet name="成绩表" sheetId="1"/></sheets></workbook>'
    with zipfile.ZipFile(BASE / "成绩表.xlsx", "w") as z:
        z.writestr("xl/sharedStrings.xml", shared)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/workbook.xml", wb)

    # PPTX
    slide = (f'<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="{A_NS}"><p:cSld><p:spTree>'
             '<a:p><a:r><a:t>FileStudio 演示文稿</a:t></a:r></a:p>'
             '<a:p><a:r><a:t>浏览器里直接预览 PPT 文本</a:t></a:r></a:p>'
             '</p:spTree></p:cSld></p:sld>')
    with zipfile.ZipFile(BASE / "演示文稿.pptx", "w") as z:
        z.writestr("ppt/slides/slide1.xml", slide)

    # PDF（pypdf 可生成空白页，用于测试浏览器内置阅读器）
    try:
        from pypdf import PdfWriter
        w = PdfWriter()
        w.add_blank_page(width=420, height=595)
        with (BASE / "空白示例.pdf").open("wb") as f:
            w.write(f)
    except Exception:
        pass

    # ZIP
    with zipfile.ZipFile(BASE / "打包示例.zip", "w") as z:
        z.writestr("readme.txt", "压缩包内容列表示例")
        z.writestr("data/a.json", '{"ok": true}')

    (BASE / "未知格式.bin").write_bytes(bytes(range(256)) * 4)
    print(f"示例文件已生成：{BASE}")


if __name__ == "__main__":
    main()
