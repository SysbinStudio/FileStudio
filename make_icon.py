# -*- coding: utf-8 -*-
"""生成 FileStudio 的窗口图标（需要 Pillow）。"""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "icon.ico"


def main() -> None:
    im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((6, 6, 250, 250), radius=54, fill=(27, 30, 38, 255))
    d.rounded_rectangle((26, 26, 230, 230), radius=40, fill=(37, 42, 53, 255))
    # 文件夹
    d.rounded_rectangle((44, 96, 212, 196), radius=16, fill=(91, 140, 255, 255))
    d.rounded_rectangle((44, 72, 128, 112), radius=12, fill=(91, 140, 255, 255))
    d.rounded_rectangle((58, 110, 198, 182), radius=10, fill=(255, 255, 255, 235))
    # 菱形标记
    d.polygon([(128, 112), (160, 144), (128, 176), (96, 144)], fill=(91, 140, 255, 255))
    d.polygon([(128, 126), (146, 144), (128, 162), (110, 144)], fill=(37, 42, 53, 255))
    im.save(OUT, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("图标已生成：", OUT)


if __name__ == "__main__":
    main()
