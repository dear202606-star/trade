# -*- coding: utf-8 -*-
"""freeze_check.py — 刷新冻结快照后，检查口径 B 的历史是否被改写。

用法（在 refresh-fixtures.yml 内）：
    python freeze_check.py
退出码：0 无漂移；1 有漂移（并写出 output/baseline_drift.json）
"""
import json
import os
import sys
import sys as _sys
try:                                    # Windows cp936 下避免 emoji 打印崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
BL = os.path.join(BASE, "output", "baseline_b.json")
DRIFT = os.path.join(BASE, "output", "baseline_drift.json")


def main():
    if not os.path.exists(BL):
        print("基线不存在，跳过（首次运行）")
        return 0

    # 用当前快照重算（不写 baseline：variant_b 在无 BASELINE_UPDATE 时只比对）
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(BASE, "variant_b.py")],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       cwd=BASE)
    out = (r.stdout or "") + (r.stderr or "")
    print(out[-1500:])
    if r.returncode == 2:
        lines = [l.strip() for l in out.splitlines() if "→" in l or "累计:" in l]
        with open(DRIFT, "w", encoding="utf-8") as f:
            json.dump({"detected_at": __import__("time").strftime(
                "%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
                "drift": lines}, f, ensure_ascii=False, indent=2)
        print("✗ 历史漂移 %d 项，已写 output/baseline_drift.json" % len(lines))
        return 1
    if r.returncode != 0:
        print("✗ variant_b 执行失败（rc=%d）" % r.returncode)
        return 1
    print("✓ 历史无漂移")
    return 0


if __name__ == "__main__":
    sys.exit(main())
