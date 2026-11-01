# -*- coding: utf-8 -*-
"""生成一个本地只读表查看器（不部署、不进站点），方便直接看库里的数据。"""
import io
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cfg = json.load(io.open(os.path.join(BASE, "static", "cloud.json"), encoding="utf-8"))

TABLES = [
    ("series_selected", "月度行情（每票单源，最终用这份）", "symbol,month,adj_close,raw_close,source,basis", "symbol,month", 300),
    ("prices_monthly", "月度行情（多源并存，原始采集）", "symbol,month,adj_close,source,basis,fetched_at", "symbol,month", 200),
    ("locked_symbols", "已锁定标的（430 只）", "symbol,locked_at,months,hash", "symbol", 200),
    ("members", "标普 500 成分区间", "symbol,start_date,end_date", "symbol,start_date", 200),
    ("sectors", "行业分类", "symbol,sector", "symbol", 200),
    ("validations", "校验结论", "check_name,status,detail,ran_at", "ran_at.desc", 100),
    ("datasets", "数据集版本", "id,name,status,symbols,months,input_hash,created_at", "id.desc", 20),
    ("source_registry", "数据源注册表", "source_id,status,basis,priority,tested", "source_id", 60),
    ("fetch_runs", "采集台账", "source,requested,ok,failed,started_at", "id.desc", 30),
    ("ingest_staging", "CI 写库暂存区", "id,symbol,month,source,promoted,reject_reason", "id.desc", 50),
]

html = ["""<!doctype html><meta charset="utf-8">
<title>在线库表查看器（只读）</title>
<style>
 body{background:#0b1020;color:#e8eaf2;font:14px/1.6 system-ui,"Segoe UI",sans-serif;margin:0;padding:24px}
 h1{font-size:20px;margin:0 0 6px} .sub{color:#8b93a7;margin-bottom:20px}
 section{background:#121a2e;border:1px solid #1e2942;border-radius:12px;padding:16px;margin-bottom:18px}
 h2{font-size:15px;margin:0 0 4px;color:#F2C46B} .meta{color:#8b93a7;font-size:12px;margin-bottom:10px}
 table{border-collapse:collapse;width:100%;font-size:12.5px}
 th,td{border-bottom:1px solid #1e2942;padding:5px 8px;text-align:left;white-space:nowrap}
 th{color:#9aa4bb;font-weight:600;position:sticky;top:0;background:#121a2e}
 tr:hover td{background:#16203a}
 .err{color:#ff7b7b}
</style>
<h1>在线库表查看器</h1>
<div class="sub">数据源：Supabase / Postgres（只读，anon key）｜ 每次打开实时拉取</div>
"""]

for name, desc, cols, order, limit in TABLES:
    html.append('<section><h2>%s</h2><div class="meta">%s · %s</div>'
                '<div id="t_%s">加载中…</div></section>' % (name, name, desc, name))

script = """
<script>
const URL = %s, KEY = %s;
const TABLES = %s;
async function load(t){
  const [name, desc, cols, order, limit] = t;
  const el = document.getElementById('t_'+name);
  try{
    const u = URL + '/rest/v1/' + name + '?select=' + cols + '&order=' + order + '&limit=' + limit;
    const r = await fetch(u, {headers:{apikey:KEY, Authorization:'Bearer '+KEY}});
    if(!r.ok){ el.innerHTML = '<span class="err">HTTP '+r.status+'</span>'; return; }
    const rows = await r.json();
    if(!rows.length){ el.innerHTML = '<span class="err">无数据</span>'; return; }
    const keys = Object.keys(rows[0]);
    let h = '<table><thead><tr>' + keys.map(k=>'<th>'+k+'</th>').join('') + '</tr></thead><tbody>';
    for(const row of rows){
      h += '<tr>' + keys.map(k=>{
        let v = row[k];
        if(v===null) v='—';
        else if(typeof v==='number') v = Math.abs(v)>=1000 ? v.toFixed(2) : String(v);
        else v = String(v).slice(0,40);
        return '<td>'+v+'</td>';
      }).join('') + '</tr>';
    }
    h += '</tbody></table><div class="meta">显示 '+rows.length+' 行</div>';
    el.innerHTML = h;
  }catch(e){ el.innerHTML = '<span class="err">'+e+'</span>'; }
}
TABLES.forEach(load);
</script>
""" % (json.dumps(cfg["url"]), json.dumps(cfg["key"]), json.dumps(TABLES))

html.append(script)
out = os.path.join(BASE, "output", "db_viewer.html")
io.open(out, "w", encoding="utf-8").write("".join(html))
print("已生成:", out)
