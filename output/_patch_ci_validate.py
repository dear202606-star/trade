# -*- coding: utf-8 -*-
"""把数据校验与锁定就绪检查接进建站工作流（审计 P1：selftest 无 DB 校验、lock_stage 0 引用）"""
import io
import yaml

STEP = '''
      - name: 数据校验入库（validations）
        continue-on-error: true
        env:
          SUPABASE_DB_PASSWORD: ${{ secrets.SUPABASE_DB_PASSWORD }}
        run: python db_validate.py

      - name: 锁定就绪度
        continue-on-error: true
        env:
          SUPABASE_DB_PASSWORD: ${{ secrets.SUPABASE_DB_PASSWORD }}
        run: python lock_stage.py --check 2010,2025
'''

for p in ('.github/workflows/quotes.yml', '.github/workflows/monthly-pick.yml'):
    s = io.open(p, encoding='utf-8').read()
    if '数据校验入库' in s:
        print(p, '已存在，跳过')
        continue
    anchor = '      - name: 构建站点'
    idx = s.find(anchor)
    if idx < 0:
        print(p, '找不到「构建站点」')
        continue
    s = s[:idx] + STEP.lstrip('\n') + '\n' + s[idx:]
    io.open(p, 'w', encoding='utf-8').write(s)
    print(p, '已插入校验步骤')

for p in ('.github/workflows/quotes.yml', '.github/workflows/monthly-pick.yml'):
    d = yaml.safe_load(io.open(p, encoding='utf-8').read())
    job = list(d['jobs'].values())[0]
    names = [x.get('name', '') for x in job['steps']]
    print(p, '步骤', len(names), '| 含校验:', any('校验入库' in n for n in names))
