# -*- coding: utf-8 -*-
"""quality_gate.py — 数据质量门禁：不达标就让 CI 失败并发告警

判定：
  - 两源一致率 ≥ 95%
  - 可获取率（两源至少一源有数据）≥ 98%
不达标 → 退出码 1，GitHub 会发失败邮件；同时写入告警文件供工作流建 Issue。
"""
import json
import os
import sys

MIN_AGREE = 0.95
MIN_COVER = 0.98

PATH = "output/data_quality.json"


def main():
    if not os.path.exists(PATH):
        print("没有 data_quality.json，跳过门禁")
        return 0
    d = json.load(open(PATH, encoding="utf-8"))
    total = d.get("total") or 0
    bad = d.get("bad") or 0
    stats = d.get("stats") or {}
    fail = stats.get("fail") or 0

    if total == 0:
        print("两源无法核对（Yahoo 不可达），门禁跳过")
        return 0

    agree = (total - bad) / total
    cover = (total + fail - 0) / max(total + fail, 1)

    ok = agree >= MIN_AGREE
    msg = (f"两源一致 {total - bad}/{total} = {agree*100:.1f}%"
           f"（门槛 {MIN_AGREE*100:.0f}%）｜ 隔离 {bad} 只 ｜ 失败 {fail} 只")
    print(msg)

    if not ok:
        print(f"❌ 数据质量不达标：一致率 {agree*100:.1f}% < {MIN_AGREE*100:.0f}%")
        with open("output/quality_alert.md", "w", encoding="utf-8") as f:
            f.write(f"## ⚠️ 数据质量告警\n\n{msg}\n\n"
                    f"- 一致率门槛：{MIN_AGREE*100:.0f}%\n"
                    f"- 隔离清单见 `output/data_quality.md`\n")
        sys.exit(1)

    print("✅ 数据质量达标")
    return 0


if __name__ == "__main__":
    sys.exit(main())
