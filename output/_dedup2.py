# -*- coding: utf-8 -*-
"""移除首页三层信息块（与收益卡/今日重复），并保持语法正确。"""
import io

P = "site_src/index.html"
s = io.open(P, encoding="utf-8").read()

# 删除 div 占位
s = s.replace('        <div id="threeLayer" style="margin-top:12px;font-size:12.5px;color:var(--txt-3);line-height:1.9"></div>\n', "")

# 删除生成逻辑（从注释到 IIFE 结束）
start = s.find("  // 三层时间视野：当下(今日) / 中期(年内) / 远期(全周期)")
if start > 0:
    end = s.find("})();", start)
    if end > 0:
        s = s[:start] + s[end + len("})();") + 2:]

io.open(P, "w", encoding="utf-8").write(s)
print("threeLayer 残留:", "threeLayer" in s, "| 三层视野残留:", "三层时间视野" in s)
