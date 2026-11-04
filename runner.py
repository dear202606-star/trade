# -*- coding: utf-8 -*-
"""步骤包装器：暂存本次运行回执，最终由 publish_pipeline_status 写入在线库。

用法（工作流里）：
    python runner.py monthly_pick.py --workers 10

产出：
    output/pipeline_status_run.json   当前作业的临时回执，不提交 Git
"""
import json
import os
import subprocess
import sys
import time

STATUS = os.path.join("output", "pipeline_status_run.json")
KEEP = 40


def main():
    cmd = sys.argv[1:]
    if not cmd:
        print("用法: python runner.py <脚本> [参数...]", file=sys.stderr)
        return 2

    t0 = time.time()
    r = subprocess.run([sys.executable] + cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
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
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "job": os.environ.get("GITHUB_JOB"),
        "commit": os.environ.get("GITHUB_SHA"),
    }

    status_failed = False
    try:
        data = {"runs": []}
        if os.path.exists(STATUS):
            with open(STATUS, encoding="utf-8") as f:
                data = json.load(f)
        runs = (data.get("runs") or [])[-(KEEP - 1):]
        runs.append(rec)
        data["runs"] = runs
        data["updated"] = rec["ts"]
        os.makedirs(os.path.dirname(STATUS), exist_ok=True)
        with open(STATUS, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"ERROR: 状态写入失败 {type(e).__name__}", file=sys.stderr)
        status_failed = True

    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    print(f"[runner] {cmd[0]} rc={r.returncode} {dur}s")
    return r.returncode or (1 if status_failed else 0)


if __name__ == "__main__":
    sys.exit(main())
