# -*- coding: utf-8 -*-
"""publish_signal.py — 把本期信号发到 GitHub Issue（触发邮件/App 通知）

用法：在 Actions 里运行，环境变量 GH_TOKEN 由 github.token 注入。
行为：
  - 找带 signal 标签的未关闭 Issue，作为「信号看板」长期更新
  - 不存在则创建（指派给仓库所有者）
  - 每次运行在该 Issue 下追加一条评论 → 触发通知
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

    # 确保标签存在
    gh("label", "create", LABEL, "--description", "策略信号",
       "--color", "FBCA04", "--force", check=False)

    lines = [
        f"## {r['month']} 信号 · {r['trend']}（仓位 {r['exposure']*100:.0f}%）",
        "",
        "| # | 代码 | 公司 | 行业 | 12月涨幅 | 建议权重 |",
        "|---|---|---|---|---|---|",
    ]
    for p in r["picks"]:
        lines.append(f"| {p['rank']} | **{p['ticker']}** | {p['name']} | {p['sector']} "
                     f"| +{p['ret12']*100:.1f}% | {p['weight']*r['exposure']*100:.1f}% |")
    lines += [
        "",
        f"**变化**：{r.get('change','—')}",
        f"**大盘**：SPY {r['spy']:,.2f} vs 10 月均线 {r['ma']:,.2f}",
        "",
        f"[查看网页看板](https://{OWNER}.github.io/{REPO.split('/')[-1]}/signals.html) ｜ "
        f"[策略说明](https://{OWNER}.github.io/{REPO.split('/')[-1]}/)",
        "",
        "<sub>自动生成，仅供研究，不构成投资建议。</sub>",
    ]
    body = "\n".join(lines)
    with open("_signal_body.md", "w", encoding="utf-8") as f:
        f.write(body)

    # 找现有 Issue
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
        url = gh(*args)
        print(f"已创建信号 Issue: {url}")
    else:
        gh("issue", "comment", str(num), "--body-file", "_signal_body.md")
        print(f"已在 Issue #{num} 追加评论（会触发通知）")

    os.remove("_signal_body.md")


if __name__ == "__main__":
    main()
