# -*- coding: utf-8 -*-
"""find_datasets.py — 在 HuggingFace / GitHub 上找「含退市票 + 全收益」的数据集

判定合格的三条
  1. 覆盖 2010-2025（或至少 2010-2020）
  2. 含退市标的（ATVI / ETFC / XL / AABA / CERN 任一）
  3. 有调整价（adj close / adjusted / total return / 分红）

用法
  python find_datasets.py search     # 搜候选
  python find_datasets.py inspect    # 查候选的文件清单
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UA = {"User-Agent": "Mozilla/5.0 (research)"}
TERMS = ["stock prices", "sp500", "s&p 500", "ohlcv", "us stocks historical",
         "equity prices", "daily stock", "financial time series", "delisted"]


def get_json(url, headers=None, timeout=30):
    h = dict(UA)
    if headers:
        h.update(headers)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"__error__": "HTTP %d" % e.code}
    except Exception as e:
        return {"__error__": str(e)[:50]}


def hf_search():
    print("=== HuggingFace datasets ===")
    seen = {}
    for t in TERMS:
        d = get_json("https://huggingface.co/api/datasets?search=%s&limit=50"
                     % urllib.parse.quote(t))
        for it in (d if isinstance(d, list) else []):
            i = it.get("id")
            if i:
                seen.setdefault(i, it.get("downloads", 0) or 0)
    for i, n in sorted(seen.items(), key=lambda x: -x[1])[:40]:
        print("  %-58s %s" % (i, n))
    return list(seen)


def hf_files(ds):
    d = get_json("https://huggingface.co/api/datasets/%s" % ds)
    files = [s["rfilename"] for s in (d.get("siblings") or [])]
    return files


def gh_search():
    print("\n=== GitHub repos ===")
    out = []
    for q in ("sp500 historical prices", "stock market dataset csv",
              "delisted stocks data", "us stock adjusted close dataset"):
        d = get_json("https://api.github.com/search/repositories?q=%s&sort=stars&per_page=8"
                     % urllib.parse.quote(q))
        for it in (d.get("items") or []):
            print("  %-52s ★%-6s %s" % (it["full_name"], it["stargazers_count"],
                                        (it.get("description") or "")[:48]))
            out.append(it["full_name"])
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "search"
    if mode == "search":
        ds = hf_search()
        gh_search()
        json.dump({"hf": ds}, open("output/_dataset_candidates.json", "w", encoding="utf-8"))
    elif mode == "inspect":
        cands = json.load(open("output/_dataset_candidates.json", encoding="utf-8"))["hf"]
        for ds in cands[:30]:
            files = hf_files(ds)
            hit = [f for f in files if f.lower().endswith((".csv", ".parquet", ".json", ".jsonl"))]
            if hit:
                print("%-56s %d 个数据文件，例: %s" % (ds, len(hit), hit[:2]))


if __name__ == "__main__":
    main()
