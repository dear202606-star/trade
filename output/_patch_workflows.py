# -*- coding: utf-8 -*-
"""给建站工作流注入数据库口令步骤 + variant_b 的 env。"""
import io

STEP = '''
      - name: 写入数据库口令
        env:
          SUPABASE_DB_PASSWORD: ${{ secrets.SUPABASE_DB_PASSWORD }}
        run: |
          mkdir -p output
          if [ -n "$SUPABASE_DB_PASSWORD" ]; then
            printf '%s' "$SUPABASE_DB_PASSWORD" > output/_supabase_dbpass.txt
          else
            echo "::warning::SUPABASE_DB_PASSWORD 未配置，将退回缓存/旧数据"
          fi
'''

for p in ('.github/workflows/quotes.yml', '.github/workflows/monthly-pick.yml'):
    s = io.open(p, encoding='utf-8').read()
    if '写入数据库口令' in s:
        print(p, '已有口令步骤，跳过')
    else:
        anchor = '      - name: 安装依赖'
        idx = s.find(anchor)
        if idx < 0:
            print(p, '找不到锚点')
            continue
        nxt = s.find('\n      - name:', idx + 10)
        if nxt < 0:
            nxt = len(s)
        s = s[:nxt] + STEP + s[nxt:]
        print(p, '已插入口令步骤')
    if 'BASELINE_MODE: observe' not in s:
        s = s.replace('        run: python runner.py variant_b.py',
                      '        env:\n'
                      '          SUPABASE_DB_PASSWORD: ${{ secrets.SUPABASE_DB_PASSWORD }}\n'
                      '          BASELINE_MODE: observe\n'
                      '        run: python runner.py variant_b.py')
        print(p, '已给 variant_b 注入 env')
    io.open(p, 'w', encoding='utf-8').write(s)

import yaml
for p in ('.github/workflows/quotes.yml', '.github/workflows/monthly-pick.yml'):
    d = yaml.safe_load(io.open(p, encoding='utf-8').read())
    job = list(d['jobs'].values())[0]
    names = [x.get('name', '') for x in job['steps']]
    print(p, '| 步骤', len(names), '| 含口令', any('数据库口令' in n for n in names))
