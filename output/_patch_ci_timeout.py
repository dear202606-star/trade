# -*- coding: utf-8 -*-
"""给 A 口径步骤加超时与容错，避免 CI 卡死导致部署被跳过。"""
import io
import yaml

P = '.github/workflows/quotes.yml'
s = io.open(P, encoding='utf-8').read()

for name, minutes in (('更新信号（同月增量，秒级）', 6), ('计算策略净值', 5), ('生成明年预览', 4)):
    anchor = '      - name: %s' % name
    idx = s.find(anchor)
    if idx < 0:
        print('找不到', name)
        continue
    # 找该步骤的 run 行，往前插 timeout-minutes（若已有则跳过）
    end = s.find('\n      - name:', idx + 10)
    block = s[idx:end if end > 0 else len(s)]
    if 'timeout-minutes' in block:
        print(name, '已有超时')
        continue
    # 在 name 行后插入
    nl = s.find('\n', idx)
    s = s[:nl + 1] + '        timeout-minutes: %d\n        continue-on-error: true\n' % minutes + s[nl + 1:]
    print(name, '已加超时 %d 分钟 + 容错' % minutes)

io.open(P, 'w', encoding='utf-8').write(s)
d = yaml.safe_load(io.open(P, encoding='utf-8').read())
job = list(d['jobs'].values())[0]
for st in job['steps']:
    if st.get('timeout-minutes'):
        print('  %-28s timeout=%s continue=%s' % (st.get('name', '')[:28], st['timeout-minutes'],
                                                  st.get('continue-on-error')))
