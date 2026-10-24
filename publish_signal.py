# -*- coding: utf-8 -*-
"""publish_signal.py — 把本期信号发到 GitHub Issue（触发邮件/App 通知）

行为：
  - 维护一个带 signal 标签的长驻 Issue「📊 月度信号看板」
  - 首次创建并指派给仓库所有者；之后每次运行追加评论 → 触发通知
"""
import json
import os
import subprocess
import sys

REPO = os.environ.get("GITHUB_REPOSITORY", "")
OWNER = REPO.split("/")[0] if "/" in REPO else ""
LABEL = "signal"
TITLE = "📊 月度信号看板"


def gh(*args, check=True):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"gh {' '.join(args)} 失败: {r.stderr}", file=sys.stderr)
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def quality_line():
    """附带数据质量一行（两个源是否一致、隔离几只）"""
    import os
    if not os.path.exists("output/data_quality.json"):
        return "**数据质量**：本次未做两源核对"
    try:
        d = json.load(open("output/data_quality.json", encoding="utf-8"))
        total, bad = d.get("total") or 0, d.get("bad") or 0
        if not total:
            return "**数据质量**：两源无法核对（Yahoo 不可达）"
        return (f"**数据质量**：两源一致 {total - bad}/{total} "
                f"= {(total - bad) / total * 100:.1f}%｜隔离 {bad} 只")
    except Exception:
        return "**数据质量**：读取失败"


def main():
    if not os.environ.get("GH_TOKEN"):
        print("未设置 GH_TOKEN，跳过通知", file=sys.stderr)
        return
    hist_path = "output/signal_history.json"
    if not os.path.exists(hist_path):
        print("没有 signal_history.json，跳过", file=sys.stderr)
        return
    hist = json.load(open(hist_path, encoding="utf-8"))
    if not hist:
        return
    r = hist[0]
    official = r.get("official") or r.get("picks") or []
    page = f"https://{OWNER}.github.io/{REPO.split('/')[-1]}/"

    gh("label", "create", LABEL, "--description", "策略信号",
       "--color", "FBCA04", "--force", check=False)

    lines = [
        f"## {r['month']} 信号 · {r['trend']}（目标仓位 {r['exposure']*100:.0f}%）",
        "",
        f"> **本月动作**：{r.get('action','—')}",
        "",
        f"### 正式持仓（{r.get('official_month','—')} 锁定，本年度有效）",
        "",
        "| # | 代码 | 公司 | 行业 | 基准涨幅 | 策略内权重 | 占总资产 |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in official:
        lines.append(f"| {p['rank']} | **{p['ticker']}** | {p['name']} | {p['sector']} "
                     f"| +{p['ret12']*100:.0f}% | {p['weight']*100:.0f}% "
                     f"| {p['weight']*r['exposure']*0.30*100:.1f}% |")
    lines += [
        "",
        f"**与上期相比**：{r.get('change','—')}",
        quality_line(),
        f"**大盘**：SPY {r['spy']:,.2f} vs 10 月均线 {r['ma']:,.2f}",
        "",
        f"[网页版]({page}) ｜ [信号看板]({page}signals.html)",
        "",
        "<sub>换仓日为每年 12 月最后一个交易日；其余月份只看开关。自动生成，仅供研究，不构成投资建议。</sub>",
    ]
    body = "\n".join(lines)
    with open("_signal_body.md", "w", encoding="utf-8") as f:
        f.write(body)

    out = gh("issue", "list", "--label", LABEL, "--state", "open",
             "--json", "number,title", "--limit", "5", check=False)
    num = None
    try:
        for it in json.loads(out or "[]"):
            if it.get("title") == TITLE:
                num = it["number"]
                break
    except Exception:
        pass

    if num is None:
        args = ["issue", "create", "--title", TITLE, "--body-file", "_signal_body.md",
                "--label", LABEL]
        if OWNER:
            args += ["--assignee", OWNER]
        print(f"已创建信号 Issue: {gh(*args)}")
    else:
        gh("issue", "comment", str(num), "--body-file", "_signal_body.md")
        print(f"已在 Issue #{num} 追加评论（会触发通知）")

    os.remove("_signal_body.md")


if __name__ == "__main__":
    main()
