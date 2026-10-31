# -*- coding: utf-8 -*-
"""db_sources.py — 数据源注册表（在线库里的独立表，供采集调度读取）

表 source_registry 是「唯一调度真相」：
  · 采集脚本（relay_source）按 priority 顺序取源，不再硬编码
  · 每次实测后更新 status / tested / last_ok
  · 免费源全部保留（含已排除但可能恢复的），付费源标记 excluded_paid（用户明确不考虑付费）

用法
  python db_sources.py --init      # 建表 + 灌入全部免费源
  python db_sources.py --list      # 打印调度顺序
  python db_sources.py --set nordic --status ok --tested "..."   # 更新某源状态
"""
import argparse
import io
import json
import os
import sys

import psycopg2
import psycopg2.extras

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
REF = "fnvahsyrzmmxwovbsqpc"
HOST = "aws-0-ap-northeast-1.pooler.supabase.com"

DDL = """
create table if not exists source_registry (
  id           serial primary key,
  source_id    text unique not null,
  display      text,
  kind         text,                 -- price | membership | sector | validation
  priority     int,                  -- 数字越小越先调度（NULL = 不参与调度）
  basis        text,                 -- total_return | price_only | n/a
  delisted     text,                 -- yes | no | unknown
  depth        text,                 -- 最早年份
  frequency    text,                 -- daily | monthly
  quota_per_day int,                 -- NULL = 无限制
  free         boolean default true,
  status       text default 'untested',  -- ok | blocked | untested | excluded_paid | dead
  role         text,                 -- history_primary | coverage | bridge | check | membership | sector
  access       text,                 -- 接口/路径
  tested       text,                 -- 最近一次实测结论
  last_ok      timestamptz,
  note         text,
  updated_at   timestamptz default now()
);
alter table source_registry enable row level security;
"""

# 全部免费源（按调度优先级）。付费源保留在册但 status=excluded_paid、priority=NULL
SEED = [
    # source_id, display, kind, priority, basis, delisted, depth, freq, quota, free, status, role, access, tested
    ("alphavantage", "Alpha Vantage", "price", 1, "total_return", "yes", "1999", "monthly", 25, True, "ok",
     "history_primary", "REST TIME_SERIES_MONTHLY_ADJUSTED",
     "NVDA2021 +125.5% / DUK2012-07 -2.02% ✓；含 ATVI/ETFC/CERN/XL/TWTR；25 次/天"),
    ("eodhd", "EODHD", "price", 2, "total_return", "yes(paid)", "13 个月(免费)", "monthly", 20, True, "ok",
     "recent_check", "REST /api/eod/{S}.US?period=m",
     "免费层实测仅近 13 个月；抓出 SNDK 142.75%、MPC 3.16% 偏差"),
    ("sina", "新浪财经（现有底座）", "price", 3, "price_only", "yes", "1999", "daily", None, True, "ok",
     "bridge", "本地缓存 static/bars_monthly.json + 实时抓取",
     "840 只含退市；复权口径混乱，已本地修复 583 只/2077 次；连续跳变 0.019%"),
    ("hf_ayushkhaire", "HF AYUSHKHAIRE 日更", "price", 4, "price_only", "no", "2010", "daily", None, True, "ok",
     "coverage", "HTTPS CSV {首字母}/{TICKER}.csv（时间戳为 Unix 秒）",
     "17,232 票；NVDA2021 +125.1% ✓；无退市、无分红"),
    ("hf_amirtrader", "HF AmirTrader(Yahoo)", "price", 5, "total_return", "no", "2019", "daily", None, True, "ok",
     "check", "HTTPS parquet data/daily/{TICKER}.parquet",
     "2019-10 起全复权；NVDA2021 +125.5% ✓（与 AV 一致）"),
    ("marketparquet_free", "MarketParquet 免费层", "price", 6, "price_only", "yes", "近 365 天", "daily", None, True, "ok",
     "recent_bulk", "HTTPS parquet /api/data/download/stock_daily/{YYYY-MM-DD}.parquet",
     "实测可取 2026-10-28 单日 232KB parquet；含退市票；仅拆股复权"),
    ("nasdaq_api", "Nasdaq 官方 API", "price", 7, "price_only", "no", "约 10 年", "daily", None, True, "ok",
     "spot_check", "REST api.nasdaq.com/api/quote/{S}/historical",
     "实测可用（AAPL 2514 条）；无退市；10 年深度"),
    ("stockanalysis", "stockanalysis.com", "price", 8, "total_return", "no", "12 个月", "daily", None, True, "ok",
     "spot_check", "REST /api/symbol/s/{t}/history", "实测返回 12 条"),
    ("fja05680", "fja05680/sp500 成分", "membership", 1, "n/a", "n/a", "1996", "irregular", None, True, "ok",
     "membership", "GitHub raw CSV（已冻结 static/members_frozen.csv）",
     "1263 行，含进出日期；已用于口径 B"),
    ("coil_trade", "coil.trade PIT S&P500", "membership", 2, "n/a", "n/a", "1976", "irregular", None, True, "untested",
     "membership_check", "网页", "未验证"),
    ("chinobing", "chinobing/historical_sp500_constituents", "membership", 3, "n/a", "n/a", "每日快照", "daily", None, True, "untested",
     "membership_check", "GitHub", "未验证"),
    ("datahub_gics", "datahub s-and-p-500-companies", "sector", 1, "n/a", "no", "当前", "irregular", None, True, "ok",
     "sector", "GitHub raw CSV（已冻结 sector_frozen.json）", "513/869 只；无 PIT"),
    ("sec_edgar", "SEC EDGAR（公司行动）", "corporate_action", 1, "n/a", "yes", "1994", "daily", None, True, "untested",
     "actions", "data.sec.gov / 8-K + XBRL companyfacts", "待验证：可用于校验拆股/分红"),
    ("openassetpricing", "OpenAssetPricing", "validation", 1, "returns", "yes", "至 2024-12", "monthly", None, True, "untested",
     "academic_check", "pip openassetpricing", "CRSP 口径 Mom12m，用作方向锚点"),
    ("quantconnect", "QuantConnect LEAN", "validation", 2, "total_return", "yes", "1998", "daily", None, True, "untested",
     "independent_replication", "平台内回测", "数据不可导出，仅作独立复现"),
    ("stooq", "Stooq", "price", None, "price_only", "unknown", "1990s", "daily", None, True, "blocked",
     "excluded_blocked", "CSV /q/d/l/", "JS 挑战 + Access denied（Playwright 也失败）"),
    ("yahoo", "Yahoo Finance", "price", None, "total_return", "partial", "1962", "daily", None, True, "blocked",
     "excluded_blocked", "v8/chart, v7/download", "本机与 CI 均 403"),
    ("wsj", "WSJ CSV", "price", None, "price_only", "unknown", "近年", "daily", None, True, "blocked",
     "excluded_blocked", "historical-prices/download", "401"),
    ("macrotrends", "Macrotrends", "price", None, "total_return", "unknown", "多年", "daily", None, True, "blocked",
     "excluded_blocked", "stock_price_history.php", "403"),
    ("kaggle", "Kaggle", "bulk", None, "unknown", "unknown", "—", "—", None, True, "dead",
     "excluded_dead", "kaggle API", "token 有效，但目标数据集已下架；无替代"),
    ("bigquery", "BigQuery 公开数据集", "price", None, "unknown", "no", "—", "—", None, True, "dead",
     "excluded_dead", "—", "无美股 EOD 公开集"),
    ("dolthub", "DoltHub prices", "price", None, "unknown", "no", "—", "—", None, True, "dead",
     "excluded_dead", "—", "数据库已下线 404"),
    # 付费源：保留在册，但 priority=NULL（用户明确不考虑付费）
    ("marketparquet_paid", "MarketParquet 完整包", "price", None, "price_only", "yes", "2000", "daily", None,
     False, "excluded_paid", "excluded_paid", "$79 买断", "暂不考虑付费"),
    ("tiingo", "Tiingo", "price", None, "total_return", "unknown", "1962", "daily", 500, False,
     "excluded_paid", "excluded_paid", "免费 500 symbols/月", "额度太小，暂不启用"),
    ("sharadar", "Sharadar SEP", "price", None, "total_return", "yes", "1998", "daily", None, False,
     "excluded_paid", "excluded_paid", "订阅制", "暂不考虑付费"),
    ("norgate", "Norgate Data", "price", None, "total_return", "yes", "1990", "daily", None, False,
     "excluded_paid", "excluded_paid", "$630/年", "暂不考虑付费"),
]


def db():
    pw = io.open(os.path.join(BASE, "output", "_supabase_dbpass.txt"), encoding="utf-8").read().strip()
    return psycopg2.connect(host=HOST, port=6543, user="postgres." + REF, password=pw,
                            dbname="postgres", connect_timeout=20, sslmode="require")


def init():
    with db() as c, c.cursor() as cur:
        cur.execute(DDL)
        for row in SEED:
            (sid, disp, kind, pri, basis, delisted, depth, freq, quota, free, status, role, access, tested) = row
            cur.execute("""insert into source_registry
                (source_id, display, kind, priority, basis, delisted, depth, frequency,
                 quota_per_day, free, status, role, access, tested, last_ok)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        case when %s='ok' then now() else null end)
                on conflict (source_id) do update set
                  display=excluded.display, kind=excluded.kind, priority=excluded.priority,
                  basis=excluded.basis, delisted=excluded.delisted, depth=excluded.depth,
                  frequency=excluded.frequency, quota_per_day=excluded.quota_per_day,
                  free=excluded.free, status=excluded.status, role=excluded.role,
                  access=excluded.access, tested=excluded.tested, updated_at=now()""",
                        (sid, disp, kind, pri, basis, delisted, depth, freq, quota, free,
                         status, role, access, tested, status))
    print("✅ source_registry 已灌入 %d 个源" % len(SEED))


def listing():
    with db() as c, c.cursor() as cur:
        cur.execute("""select priority, source_id, display, basis, status, role, quota_per_day
                       from source_registry order by priority nulls last, source_id""")
        print("%-4s %-20s %-26s %-12s %-14s %s" % ("序", "source_id", "名称", "口径", "状态", "额度"))
        for p, sid, disp, basis, status, role, q in cur.fetchall():
            print("%-4s %-20s %-26s %-12s %-14s %s" % (
                p if p else "-", sid, (disp or "")[:24], basis or "-", status, q or "∞"))


def set_status(sid, status=None, tested=None):
    with db() as c, c.cursor() as cur:
        cur.execute("""update source_registry set
                         status = coalesce(%s, status),
                         tested = coalesce(%s, tested),
                         last_ok = case when %s = 'ok' then now() else last_ok end,
                         updated_at = now()
                       where source_id = %s""", (status, tested, status, sid))
        print("已更新 %s → %s（影响 %d 行）" % (sid, status, cur.rowcount))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--set", default="")
    ap.add_argument("--status", default=None)
    ap.add_argument("--tested", default=None)
    a = ap.parse_args()
    if a.init:
        init()
    if a.list:
        listing()
    if a.set:
        set_status(a.set, a.status, a.tested)
