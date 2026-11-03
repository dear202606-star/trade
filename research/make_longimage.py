# -*- coding: utf-8 -*-
"""make_longimage.py — 把 HTML 整页渲染成一张长图（自动裁掉底部空白）

用法： python make_longimage.py <html> <out.png> [宽度] [缩放]
"""
import os
import subprocess
import sys
import tempfile

from PIL import Image
import numpy as np

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def shot(html, out, width=1200, scale=2, height=18000):
    uri = "file:///" + os.path.abspath(html).replace("\\", "/")
    fd, raw = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
           f"--force-device-scale-factor={scale}",
           f"--window-size={width},{height}",
           "--virtual-time-budget=12000",
           f"--screenshot={raw}", uri]
    subprocess.run(cmd, capture_output=True, timeout=300)
    if not os.path.exists(raw):
        raise RuntimeError("截图失败")
    im = Image.open(raw).convert("RGB")
    a = np.asarray(im)
    bg = a[2, 2].astype(int)
    diff = np.abs(a.astype(int) - bg).sum(axis=2)
    rows = np.where(diff.max(axis=1) > 12)[0]
    cut = min(a.shape[0], int(rows.max()) + 60) if len(rows) else a.shape[0]
    im.crop((0, 0, a.shape[1], cut)).save(out, optimize=True)
    try: os.remove(raw)
    except Exception: pass
    return Image.open(out).size


if __name__ == "__main__":
    src = sys.argv[1]
    dst = sys.argv[2]
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1200
    s = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    print(dst, shot(src, dst, w, s))
