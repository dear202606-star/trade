#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成微信/社交转发用的 og:image（1200x630 PNG）。

设计对齐站点视觉：深墨绿主视觉 + 青绿/金色点缀 + 抽象上升曲线，
体现「量化策略、趋势与纪律」的调性。不写任何会过期的实时数字，
转发卡片长期有效。可重复运行覆盖输出。
"""
import math
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1200, 630
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_src", "assets", "og-card.png")

FONT_DIR = r"C:\Windows\Fonts"
F_BOLD = os.path.join(FONT_DIR, "msyhbd.ttc")   # 微软雅黑粗
F_REG = os.path.join(FONT_DIR, "msyh.ttc")      # 微软雅黑


def font(path, size):
    return ImageFont.truetype(path, size)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def vgradient(img, top, bottom):
    d = ImageDraw.Draw(img)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=lerp(top, bottom, y / (H - 1)))


def main():
    img = Image.new("RGB", (W, H), (10, 30, 26))
    # 深墨绿对角渐变
    vgradient(img, (14, 46, 39), (9, 28, 24))
    d = ImageDraw.Draw(img, "RGBA")

    # 背景柔光斑（左上青绿、右下深绿），先画到单独层再合成
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-180, -220, 620, 380], fill=(0, 166, 130, 42))
    gd.ellipse([760, 380, 1360, 900], fill=(0, 120, 96, 36))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    img = Image.alpha_composite(img.convert("RGBA"), glow)
    d = ImageDraw.Draw(img, "RGBA")

    # 顶部金色细线
    d.rectangle([0, 0, W, 6], fill=(242, 196, 107, 255))
    # 左侧竖向强调条
    d.rounded_rectangle([72, 150, 80, 268], radius=4, fill=(0, 200, 160, 255))

    # 品牌字标
    d.text((104, 74), "LEADER", font=font(F_BOLD, 40), fill=(245, 250, 246, 255))
    tw = d.textlength("LEADER", font=font(F_BOLD, 40))
    d.text((104 + tw + 4, 74), "/", font=font(F_REG, 40), fill=(120, 200, 170, 255))
    tw2 = d.textlength("LEADER / ", font=font(F_BOLD, 40))
    d.text((104 + tw2, 74), "QUANT", font=font(F_BOLD, 40), fill=(0, 214, 168, 255))
    d.ellipse([104 + tw2 + d.textlength("QUANT", font=font(F_BOLD, 40)) + 12, 92,
               104 + tw2 + d.textlength("QUANT", font=font(F_BOLD, 40)) + 22, 102],
              fill=(242, 196, 107, 255))

    # 主标语
    d.text((104, 196), "趋势与纪律", font=font(F_BOLD, 92), fill=(244, 252, 247, 255))
    # 副标
    d.text((108, 322), "数据驱动的美股年度策略  ·  趋势过滤  ·  严格风控",
           font=font(F_REG, 27), fill=(168, 205, 188, 255))

    # 中部分隔线
    d.line([(104, 392), (660, 392)], fill=(120, 180, 156, 90), width=2)

    # 三个关键词标签
    tags = [("年度持仓", (0, 190, 150)), ("量化选股", (0, 170, 145)), ("长期复利", (150, 130, 60))]
    x = 104
    f_tag = font(F_REG, 24)
    for text, col in tags:
        w = int(d.textlength(text, font=f_tag))
        d.rounded_rectangle([x, 424, x + w + 40, 476], radius=26,
                            fill=(*col, 42), outline=(*col, 130), width=1)
        d.text((x + 20, 434), text, font=f_tag, fill=(210, 240, 226, 255))
        x += w + 56

    # 底部域名
    d.text((104, 556), "dear202606-star.github.io/trade", font=font(F_REG, 22),
           fill=(130, 170, 152, 220))

    # 右侧抽象上升曲线 + 网格（象征净值曲线）
    gx0, gy0, gw, gh = 760, 150, 380, 300
    grid_col = (140, 200, 176, 26)
    for i in range(1, 5):
        yy = gy0 + gh * i / 5
        d.line([(gx0, yy), (gx0 + gw, yy)], fill=grid_col, width=1)
    for i in range(1, 6):
        xx = gx0 + gw * i / 6
        d.line([(xx, gy0), (xx, gy0 + gh)], fill=grid_col, width=1)

    # 生成一条带波动的上升曲线
    pts = []
    n = 60
    for i in range(n):
        t = i / (n - 1)
        base = gy0 + gh - (gh * 0.82) * (t ** 1.5)
        wave = math.sin(t * math.pi * 6) * 16 * (1 - t * 0.5)
        pts.append((gx0 + gw * t, base + wave))

    # 曲线下方填充
    poly = pts + [(gx0 + gw, gy0 + gh), (gx0, gy0 + gh)]
    fill_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill_layer)
    fd.polygon(poly, fill=(0, 200, 158, 46))
    img = Image.alpha_composite(img, fill_layer)
    d = ImageDraw.Draw(img, "RGBA")

    # 曲线本体（青绿发光两层）
    d.line(pts, fill=(0, 220, 172, 70), width=12, joint="curve")
    d.line(pts, fill=(110, 245, 200, 255), width=4, joint="curve")
    # 终点光点
    ex, ey = pts[-1]
    d.ellipse([ex - 10, ey - 10, ex + 10, ey + 10], fill=(242, 196, 107, 90))
    d.ellipse([ex - 5, ey - 5, ex + 5, ey + 5], fill=(255, 236, 180, 255))

    # 角标：STRATEGY 2026
    d.text((gx0 + 2, gy0 - 34), "STRATEGY · LIVE", font=font(F_REG, 20),
           fill=(120, 200, 170, 200))

    img.convert("RGB").save(OUT, "PNG", optimize=True)
    print("saved:", OUT, img.size)


if __name__ == "__main__":
    main()
