# -*- coding: utf-8 -*-
"""把数据质量提示接成数据驱动（审计 P0-2：混合口径零提示、静态文案永不消失）"""
import io
import re

# 1) index.html：把硬编码的「数据修复中」换成带 id 的元素
p = "site_src/index.html"
s = io.open(p, encoding="utf-8").read()
old = '<div class="foot">数据修复中 · 历史收益仅供参考</div>'
new = '<div class="foot" id="dataQuality" hidden></div>'
if old in s:
    s = s.replace(old, new)
    io.open(p, "w", encoding="utf-8").write(s)
    print("index.html: 静态文案已改为数据驱动元素")
else:
    print("index.html: 未找到静态文案（可能已改）")

# 2) page.js：拉取 quality.json 并渲染
p = "site_src/assets/page.js"
s = io.open(p, encoding="utf-8").read()
if "renderQuality" not in s:
    hook = '''
/* ---------- 数据质量提示（来自 data/quality.json） ---------- */
function renderQuality() {
  var el = document.getElementById("dataQuality");
  if (!el) return;
  fetch(DATA_BASE + "quality.json?t=" + Date.now(), { cache: "no-store" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (q) {
      if (!q) { el.hidden = true; return; }
      var s = q.summary || {};
      if (q.degraded) {
        el.hidden = false;
        el.className = "foot warn";
        el.textContent = "数据校验中 · " + (q.reason || "口径尚未统一") +
          " ｜ 检查 " + (s.pass || 0) + " 项通过" + (s.fail ? "、" + s.fail + " 项失败" : "");
      } else {
        el.hidden = false;
        el.className = "foot ok";
        el.textContent = "数据已校验 · 全收益口径 100% ｜ " + (s.pass || 0) + " 项检查通过";
      }
    })
    .catch(function () { el.hidden = true; });
}
'''
    # 插到文件末尾（在 IIFE 之外也行，只要函数可被调用）
    s = s.rstrip() + "\n" + hook
    # 在 boot 成功后调用
    if "renderQuality()" not in s.split(hook)[0]:
        s = s.replace("startAutoRefresh();", "startAutoRefresh();\n      renderQuality();", 1)
    io.open(p, "w", encoding="utf-8").write(s)
    print("page.js: 已接入 renderQuality")
else:
    print("page.js: 已存在")

# 3) CSS：warn / ok 两种状态
p = "site_src/assets/page.css"
s = io.open(p, encoding="utf-8").read()
if ".foot.warn" not in s:
    s = s.rstrip() + '''

/* 数据质量提示（降级黄 / 通过绿，贴合红涨绿跌主色系） */
.foot.warn { color: #F2C46B; }
.foot.ok   { color: #6FCF97; }
'''
    io.open(p, "w", encoding="utf-8").write(s)
    print("page.css: 已加状态样式")
else:
    print("page.css: 已存在")

# 校验
for f in ("site_src/index.html", "site_src/assets/page.js", "site_src/assets/page.css"):
    txt = io.open(f, encoding="utf-8").read()
    print(f, "| 含 quality:", "quality.json" in txt or "dataQuality" in txt)
