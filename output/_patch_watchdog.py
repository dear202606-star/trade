# -*- coding: utf-8 -*-
"""给看门狗加一条：质量快照有校验失败就告警"""
import io
import yaml

P = '.github/workflows/watchdog.yml'
s = io.open(P, encoding='utf-8').read()

MARK = "# 3b) 质量快照校验失败"
if MARK in s:
    print("已存在，跳过")
else:
    anchor = "          # 3) 数据质量标注是否已消失"
    if anchor not in s:
        anchor = "          # 3) 检查质量快照"
    block = (
        "          # 3b) 质量快照校验失败\n"
        "          try:\n"
        "              _q = json.load(open('docs/data/quality.json', encoding='utf-8'))\n"
        "              _f = _q.get('summary', {}).get('fail', 0)\n"
        "              if _f:\n"
        "                  problems.append('质量快照有 %d 项校验失败' % _f)\n"
        "          except Exception:\n"
        "              pass\n\n"
    )
    if anchor in s:
        s = s.replace(anchor, block + anchor, 1)
        io.open(P, 'w', encoding='utf-8').write(s)
        print("已插入质量告警")
    else:
        print("找不到锚点；当前片段：")
        i = s.find("problems.append")
        print(s[max(0, i - 300):i + 200])

d = yaml.safe_load(io.open(P, encoding='utf-8').read())
steps = [x.get('name', '') for x in list(d['jobs'].values())[0]['steps']]
print("watchdog 步骤:", steps)
