# -*- coding: utf-8 -*-
"""sa_limit=0 时跳过 stockanalysis 步骤"""
import io
import yaml

P = '.github/workflows/collect-full.yml'
s = io.open(P, encoding='utf-8').read()

if 'sa_limit != ' in s:
    print("已存在，跳过")
else:
    old_head = "      - name: stockanalysis 补缺（免费，10 年全收益）\n        continue-on-error: true"
    new_head = ("      - name: stockanalysis 补缺（免费，10 年全收益）\n"
                "        if: ${{ github.event.inputs.sa_limit != '0' }}\n"
                "        continue-on-error: true")
    if old_head in s:
        s = s.replace(old_head, new_head, 1)
        # 运行命令加保护
        s = s.replace('        run: python fetch_stockanalysis.py --symbols "${SA_LIMIT:-120}"',
                      '        run: |\n'
                      '          N="${SA_LIMIT:-120}"\n'
                      '          if [ "$N" = "0" ]; then echo "跳过 stockanalysis"; exit 0; fi\n'
                      '          python fetch_stockanalysis.py --symbols "$N"')
        io.open(P, 'w', encoding='utf-8').write(s)
        print("已插入条件与保护")
    else:
        print("找不到锚点")

d = yaml.safe_load(io.open(P, encoding='utf-8').read())
steps = list(d['jobs'].values())[0]['steps']
for st in steps:
    if 'stockanalysis' in (st.get('name') or ''):
        print("step:", st.get('name'), "| if:", st.get('if'))
