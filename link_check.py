# -*- coding: utf-8 -*-
"""link_check.py — 全站链接巡检：抓出所有 404 / 断链

从一个站点根出发，抓取所有页面，解析 href/src，逐个请求，报告非 200 的。
用法：python link_check.py [base_url]
"""
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else
        "https://dear202606-star.github.io/trade/").rstrip("/") + "/"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Cache-Control": "no-store"}
CB = "?cb=" + str(int(time.time()))

PAGES = ["", "portfolio.html", "strategy.html", "signals.html", "report.html"]

LINK_RE = re.compile(r'(?:href|src)\s*=\s*["\']([^"\'#?]+)[^"\']*["\']', re.I)


def normalize(url):
    if url.startswith(("mailto:", "javascript:", "data:", "tel:")):
        return None
    if url.startswith("http") and "dear202606-star.github.io" not in url:
        return None
    return url.split("#")[0]


def main():
    bad, checked = [], set()
    for pg in PAGES:
        try:
            r = requests.get(BASE + pg + CB, headers=H, timeout=30)
            r.encoding = "utf-8"
        except Exception as e:
            bad.append((pg or "index", "请求失败 " + str(e)[:50]))
            continue
        if r.status_code != 200:
            bad.append((pg or "index", "页面 %d" % r.status_code))
            continue
        for raw in LINK_RE.findall(r.text):
            u = normalize(raw)
            if not u or u.startswith(("http://", "https://")) and BASE not in u:
                continue
            full = urljoin(BASE + pg, u)
            if full in checked:
                continue
            checked.add(full)
            try:
                rr = requests.get(full + CB, headers=H, timeout=25, stream=True)
                if rr.status_code != 200:
                    bad.append((pg or "index", "%s → %d" % (u, rr.status_code)))
            except Exception as e:
                bad.append((pg or "index", "%s → %s" % (u, str(e)[:40])))

    print("=== 全站链接巡检 · %s ===" % BASE)
    print("检查页面 %d 个，链接 %d 条" % (len(PAGES), len(checked)))
    if bad:
        for pg, msg in bad:
            print("  ❌ %-16s %s" % (pg, msg))
        print("发现 %d 处问题" % len(bad))
        return 1
    print("  ✅ 全部链接可访问")
    return 0


if __name__ == "__main__":
    sys.exit(main())
