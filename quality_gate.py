# -*- coding: utf-8 -*-
"""quality_gate.py — 数据质量门禁：不达标就让 CI 失败并发告警

判定（任一不过即失败，禁止 fail-open）：
  - 必须存在 data_quality.json，且参与核对的标的数 ≥ 100
  - 两源一致率 ≥ 95%
  - 可核对覆盖率（两源至少一源有数据）≥ 98%

设计原则：**无法核对 ≠ 核对通过**。
Yahoo 不可达时不再「跳过放行」，而是明确失败，由人工决定是否临时放行
（临时放行方式：设置环境变量 ALLOW_UNVERIFIED=1，会在日志里留痕）。
"""
import json
import os
import sys

MIN_AGREE = 0.95
MIN_COVER = 0.98
MIN_SAMPLE = 100
PATH = "output/data_quality.json"


def main():
    if not os.path.exists(PATH):
        print("❌ 缺少 data_quality.json，无法证明数据可信，门禁失败")
        return 1

    d = json.load(open(PATH, encoding="utf-8"))
    total = d.get("total") or 0
    bad = d.get("bad") or 0
    stats = d.get("stats") or {}
    fail = stats.get("fail") or 0
    checked = total + fail

    # 无法核对：默认失败（fail-closed），除非显式放行
    if total == 0 or checked < MIN_SAMPLE:
        msg = f"两源核对样本不足：核对 {total} 只 / 缺失 {fail} 只（门槛 ≥{MIN_SAMPLE} 只可核对）"
        if os.environ.get("ALLOW_UNVERIFIED") == "1":
            print("⚠️ " + msg + "；已设 ALLOW_UNVERIFIED=1，本次放行（会记录）")
            return 0
        print("❌ " + msg)
        print("   若确认数据源临时不可用，可设 ALLOW_UNVERIFIED=1 临时放行")
        return 1

    agree = (total - bad) / total
    cover = total / max(checked, 1) if checked else 0

    print(f"两源一致 {total - bad}/{total} = {agree*100:.1f}%（门槛 {MIN_AGREE*100:.0f}%）"
          f"｜ 隔离 {bad} 只 ｜ 不可核对 {fail} 只 ｜ 覆盖率 {cover*100:.1f}%")

    problems = []
    if agree < MIN_AGREE:
        problems.append(f"一致率 {agree*100:.1f}% < {MIN_AGREE*100:.0f}%")
    if cover < MIN_COVER:
        problems.append(f"覆盖率 {cover*100:.1f}% < {MIN_COVER*100:.0f}%")

    if problems:
        print("❌ 数据质量不达标：" + "；".join(problems))
        try:
            with open("output/quality_alert.md", "w", encoding="utf-8") as f:
                f.write("## ⚠️ 数据质量告警\n\n")
                f.write("- " + "\n- ".join(problems) + "\n")
                f.write(f"- 明细见 `output/data_quality.md`\n")
        except Exception:
            pass
        return 1

    print("✅ 数据质量达标")
    return 0


if __name__ == "__main__":
    sys.exit(main())
