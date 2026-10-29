# -*- coding: utf-8 -*-
"""修复专家委员会 S1 / S2：让「文档规则」与「净值实现」完全一致。

S1：回测与净值从未执行「跌破 10 月均线减半」→ 规则文本改为「满仓持有」，
    减半降级为「可选风控（未纳入回测与净值）」。
S2：equity.py 不再用敞口缩放净值（此前用当前敞口回填历史）。
    敞口只作为「建议仓位」展示，不参与净值计算。
"""
import io

# ---------- S2：equity.py 移除敞口缩放 ----------
p = "equity.py"
s = io.open(p, encoding="utf-8").read()
old = """        # 口径说明：历史段一律满仓；敞口只作用于「最新一日之后」。
        # （此前用当前敞口回填全部历史，导致开关翻转时历史收益被改写）
        if d == days[-1]:
            s_val = s_val * exposure + (1 - exposure)"""
new = """        # 净值口径 = 满仓持有（与回测一致）。敞口只作「建议仓位」展示，
        # 不参与净值计算，避免开关翻转时历史收益被改写。"""
if old in s:
    s = s.replace(old, new)
    io.open(p, "w", encoding="utf-8").write(s)
    print("S2 已修：净值不再按敞口缩放")
else:
    print("S2 未匹配，请检查")

# ---------- S1：规则文本与实现一致 ----------
p = "site_src/strategy.html"
s = io.open(p, encoding="utf-8").read()
old_rule = ("标普 500 跌破 10 月均线时实盘仓位减半"
            "<span style=\"color:var(--txt-3)\">（回测按满仓口径计算，未叠加该开关）</span>。")
new_rule = ("满仓持有；标普 500 跌破 10 月均线时的"
            "<b>减半</b>属于可选风控，<b>未纳入</b>回测与净值。")
if old_rule in s:
    s = s.replace(old_rule, new_rule)
    io.open(p, "w", encoding="utf-8").write(s)
    print("S1 已修：规则文本与净值口径一致")
else:
    print("S1 未匹配")

# ---------- S3：锁定留痕 ----------
import json
import os
lk = "output/official_locks.json"
if os.path.exists(lk):
    d = json.load(open(lk, encoding="utf-8"))
    for k, v in d.items():
        v.setdefault("version", 1)
        v.setdefault("note", "首次按「排名日时点成分股」口径锁定")
    json.dump(d, open(lk, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("S3 已补版本与说明:", {k: (v.get("version"), v.get("locked_at")) for k, v in d.items()})
