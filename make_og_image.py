#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成社交转发 og 图：横版 og-card.png + 方形 og-icon.png。

og-icon.png（600x600）专治微信转发卡片：微信把 og:image 裁成右侧正方形小图，
横版 1200x630 被裁后只剩中间一截曲线、文字全丢，缩到 ~80px 根本看不清。
方图主体占满中心、线条粗、对比强，小尺寸下也能一眼认出品牌。

设计对齐站点视觉：深墨绿 + 青绿/金色，抽象上升曲线（趋势）。不写实时数字。
可重复运行覆盖输出。
"""
import math
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(BASE, "site_src", "assets")
OUT_WIDE = os.path.join(ASSETS, "og-card.png")
OUT_SQUARE = os.path.join(ASSETS, "og-icon.png")

FONT_DIR = r"C:\Windows\Fonts"
F_BOLD = os.path.join(FONT_DIR, "msyhbd.ttc")
F_REG = os.path.join(FONT_DIR, "msyh.ttc")


def font(path, size):
    return ImageFont.truetype(path, size)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def vgradient(size, top, bottom):
    w, h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=(*lerp(top, bottom, y / (h - 1)), 255))
    return img


def glow_overlay(size, blobs):
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for box, color in blobs:
        gd.ellipse(box, fill=color)
    return glow.filter(ImageFilter.GaussianBlur(size[0] // 14))


def rising_curve(d, x0, y0, x1, y1, color_main, color_glow, width):
    pts = []
    n = 60
    for i in range(n):
        t = i / (n - 1)
        base = y1 - (y1 - y0) * (t ** 1.55)
        wave = math.sin(t * math.pi * 5.5) * (y1 - y0) * 0.055 * (1 - t * 0.4)
        pts.append((x0 + (x1 - x0) * t, base + wave))
    d.line(pts, fill=color_glow, width=width + 8, joint="curve")
    d.line(pts, fill=color_main, width=width, joint="curve")
    ex, ey = pts[-1]
    return ex, ey


def build_wide():
    W, H = 1200, 630
    img = vgradient((W, H), (14, 46, 39), (9, 28, 24))
    img = Image.alpha_composite(img, glow_overlay((W, H), [
        ([-180, -220, 620, 380], (0, 166, 130, 42)),
        ([760, 380, 1360, 900], (0, 120, 96, 36)),
    ]))
    d = ImageDraw.Draw(img, "RGBA")

    d.rectangle([0, 0, W, 6], fill=(242, 196, 107, 255))
    d.rounded_rectangle([72, 150, 80, 268], radius=4, fill=(0, 200, 160, 255))

    d.text((104, 74), "LEADER", font=font(F_BOLD, 40), fill=(245, 250, 246, 255))
    f40 = font(F_BOLD, 40)
    tw = d.textlength("LEADER / ", font=f40)
    d.text((104 + d.textlength("LEADER", font=f40) + 4, 74), "/", font=font(F_REG, 40), fill=(120, 200, 170, 255))
    d.text((104 + tw, 74), "QUANT", font=f40, fill=(0, 214, 168, 255))
    d.ellipse([104 + tw + d.textlength("QUANT", font=f40) + 12, 92,
               104 + tw + d.textlength("QUANT", font=f40) + 22, 102], fill=(242, 196, 107, 255))

    d.text((104, 196), "趋势与纪律", font=font(F_BOLD, 92), fill=(244, 252, 247, 255))
    d.text((108, 322), "数据驱动的美股年度策略  ·  趋势过滤  ·  严格风控",
           font=font(F_REG, 27), fill=(168, 205, 188, 255))
    d.line([(104, 392), (660, 392)], fill=(120, 180, 156, 90), width=2)

    tags = [("年度持仓", (0, 190, 150)), ("量化选股", (0, 170, 145)), ("长期复利", (150, 130, 60))]
    x, f_tag = 104, font(F_REG, 24)
    for text, col in tags:
        w = int(d.textlength(text, font=f_tag))
        d.rounded_rectangle([x, 424, x + w + 40, 476], radius=26, fill=(*col, 42), outline=(*col, 130), width=1)
        d.text((x + 20, 434), text, font=f_tag, fill=(210, 240, 226, 255))
        x += w + 56

    d.text((104, 556), "dear202606-star.github.io/trade", font=font(F_REG, 22), fill=(130, 170, 152, 220))

    gx0, gy0, gw, gh = 760, 150, 380, 300
    for i in range(1, 5):
        yy = gy0 + gh * i / 5
        d.line([(gx0, yy), (gx0 + gw, yy)], fill=(140, 200, 176, 26), width=1)
    for i in range(1, 6):
        xx = gx0 + gw * i / 6
        d.line([(xx, gy0), (xx, gy0 + gh)], fill=(140, 200, 176, 26), width=1)
    fill_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill_layer)
    fd.polygon([(gx0, gy0 + gh), (gx0 + gw, gy0 + gh)], fill=(0, 200, 158, 40))
    ex, ey = rising_curve(d, gx0, gy0 + gh - 30, gx0 + gw, gy0 + 30,
                          (110, 245, 200, 255), (0, 220, 172, 70), 4)
    d.ellipse([ex - 10, ey - 10, ex + 10, ey + 10], fill=(242, 196, 107, 90))
    d.ellipse([ex - 5, ey - 5, ex + 5, ey + 5], fill=(255, 236, 180, 255))
    d.text((gx0 + 2, gy0 - 34), "STRATEGY · LIVE", font=font(F_REG, 20), fill=(120, 200, 170, 200))

    img.convert("RGB").save(OUT_WIDE, "PNG", optimize=True)
    print("saved:", OUT_WIDE, (W, H))


def build_square():
    """微信转发缩略图专用：600x600，主体居中放大，缩到 80px 仍可辨认。"""
    S = 600
    img = vgradient((S, S), (16, 52, 43), (8, 26, 22))
    img = Image.alpha_composite(img, glow_overlay((S, S), [
        ([-80, -60, 420, 380], (0, 170, 134, 40)),
        ([300, 320, 720, 700], (0, 120, 96, 34)),
    ]))
    d = ImageDraw.Draw(img, "RGBA")

    # 顶部金线 + 四角圆角由微信卡片自己裁，这里只做满幅底
    d.rectangle([0, 0, S, 8], fill=(242, 196, 107, 255))

    # 中央主体：粗壮上升曲线 + 箭头终点，占画面中部大面积
    x0, x1 = 92, 520
    y0, y1 = 430, 150
    # 面积填充
    fill_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill_layer)
    pts = []
    n = 60
    for i in range(n):
        t = i / (n - 1)
        base = y1 + (y0 - y1) * t  # 从左上到右下? 调整：左低右高
        base = y0 - (y0 - y1) * (t ** 1.5)
        wave = math.sin(t * math.pi * 5) * 26 * (1 - t * 0.35)
        pts.append((x0 + (x1 - x0) * t, base + wave))
    fd.polygon(pts + [(x1, y0 + 20), (x0, y0 + 20)], fill=(0, 200, 158, 46))
    img = Image.alpha_composite(img, fill_layer)
    d = ImageDraw.Draw(img, "RGBA")
    d.line(pts, fill=(0, 220, 172, 80), width=22, joint="curve")
    d.line(pts, fill=(120, 250, 205, 255), width=10, joint="curve")

    # 终点金色光点（大，缩略可见）
    ex, ey = pts[-1]
    d.ellipse([ex - 20, ey - 20, ex + 20, ey + 20], fill=(242, 196, 107, 100))
    d.ellipse([ex - 11, ey - 11, ex + 11, ey + 11], fill=(255, 236, 180, 255))

    # 顶部大字标（缩略图里也要能认出）
    d.text((S / 2, 58), "LEADER", font=font(F_BOLD, 44), fill=(245, 250, 246, 255), anchor="mm")
    d.text((S / 2, 108), "QUANT", font=font(F_BOLD, 44), fill=(0, 220, 175, 255), anchor="mm")

    # 底部标语（中等字号，缩略时可辨）
    d.text((S / 2, 520), "趋势与纪律", font=font(F_BOLD, 40), fill=(244, 252, 247, 255), anchor="mm")

    img.convert("RGB").save(OUT_SQUARE, "PNG", optimize=True)
    print("saved:", OUT_SQUARE, (S, S))


if __name__ == "__main__":
    build_wide()
    build_square()
