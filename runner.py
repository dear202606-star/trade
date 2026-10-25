# -*- coding: utf-8 -*-
"""runner.py — 步骤包装器：记录每一步的退出码与错误尾部到仓库

用法（工作流里）：
    python runner.py monthly_pick.py --workers 10

产出：
    output/pipeline_status.json   最近若干次步骤执行结果（会被提交，便于事后排查）
"""
import json
import os
import subprocess
import sys
import time

STATUS = os.path.join("output", "pipeline_status.json")
KEEP = 40


def main():
    cmd = sys.argv[1:]
    if not cmd:
        print("用法: python runner.py <脚本> [参数...]", file=sys.stderr)
        return 2

    t0 = time.time()
    r = subprocess.run([sys.executable] + cmd, capture_output=True, text=True)
    dur = round(time.time() - t0, 1)

    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    tail = (out[-1200:] + ("\n[stderr]\n" + err[-1200:] if err else "")).strip()

    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "step": cmd[0],
        "rc": r.returncode,
        "dur": dur,
        "ok": r.returncode == 0,
        "tail": tail,
    }

    try:
        data = {"runs": []}
        if os.path.exists(STATUS):
            try:
                data = json.load(open(STATUS, encoding="utf-8"))
            except Exception:
                pass
        runs = (data.get("runs") or [])[-(KEEP - 1):]
        runs.append(rec)
        data["runs"] = runs
        data["updated"] = rec["ts"]
        os.makedirs(os.path.dirname(STATUS), exist_ok=True)
        with open(STATUS, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"WARN: 状态写入失败 {e}", file=sys.stderr)

    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    print(f"[runner] {cmd[0]} rc={r.returncode} {dur}s")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
