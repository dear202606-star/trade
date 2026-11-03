# -*- coding: utf-8 -*-
"""staging_write.py — 用公开 anon key 把采集结果写进暂存表（CI 无 secret 也能入库）

链路：CI 采集 → REST 写 ingest_staging → 库内 pg_cron 每 10 分钟 promote_staging()
      → 校验后合并进 prices_monthly（正式表仍受锁与触发器保护）

用法（作为库）
  import staging_write
  staging_write.push(rows)      # rows = [(symbol, month, adj, raw, source, quality, basis)]
"""
import io
import json
import os
import sys

import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(BASE, "static", "cloud.json")
BATCH = 500


def _cfg():
    d = json.load(io.open(CFG, encoding="utf-8"))
    return d["url"].rstrip("/"), d["key"]


def push(rows, batch=BATCH):
    """rows: [(symbol, month, adj, raw, source, quality, basis)] → 返回成功写入行数"""
    if not rows:
        return 0
    url, key = _cfg()
    ok = 0
    for i in range(0, len(rows), batch):
        part = rows[i:i + batch]
        payload = [{"symbol": r[0], "month": r[1], "adj_close": r[2], "raw_close": r[3],
                    "source": r[4], "quality": r[5], "basis": r[6]} for r in part]
        req = urllib.request.Request(
            url + "/rest/v1/ingest_staging", method="POST",
            data=json.dumps(payload).encode(),
            headers={"apikey": key, "Authorization": "Bearer " + key,
                     "Content-Type": "application/json", "Prefer": "return=minimal"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    if r.status in (200, 201, 204):
                        ok += len(part)
                        break
            except Exception as e:
                if attempt == 2:
                    print("⚠ 暂存写入失败 %d 行：%s" % (len(part), str(e)[:80]), flush=True)
                else:
                    import time
                    time.sleep(3 * (attempt + 1))
    return ok


def push_from_bars(symbol, bars, source="stockanalysis", basis="total_return", quality="ok"):
    rows = [(symbol, m, a, r, source, quality, basis) for m, (a, r) in bars.items()]
    return push(rows)


if __name__ == "__main__":
    n = push([("ZZPROBE", "2026-09", 1.0, 1.0, "probe", "ok", "total_return")])
    print("写入", n, "行")
