# -*- coding: utf-8 -*-
"""给 selftest 步骤注入 BASELINE_MODE=observe（迁移期不阻断部署）"""
import io
import yaml

P = '.github/workflows/quotes.yml'
s = io.open(P, encoding='utf-8').read()
marker = 'BASELINE_MODE: observe'
anchor = '      - name: 部署前自检\n        run: python runner.py selftest.py'
if anchor in s:
    s = s.replace(anchor,
                  '      - name: 部署前自检\n'
                  '        env:\n'
                  '          BASELINE_MODE: observe\n'
                  '        run: python runner.py selftest.py')
    io.open(P, 'w', encoding='utf-8').write(s)
    print('已注入 observe')
elif marker in s and '部署前自检' in s:
    print('已存在')
else:
    print('未找到锚点，当前自检段：')
    i = s.find('部署前自检')
    print(s[i-80:i+160] if i > 0 else '无')

d = yaml.safe_load(io.open(P, encoding='utf-8').read())
job = list(d['jobs'].values())[0]
for st in job['steps']:
    if '自检' in st.get('name', ''):
        print('自检 env:', st.get('env'))
