"""Bounded public security-directory collection; ambiguous symbols stay ambiguous."""
import datetime as dt
import hashlib
import json
import math
import sys
from pathlib import Path

import requests

BASE=Path(__file__).resolve().parents[1]
CACHE=BASE/'output/_eastmoney_probe/universe'
URL='https://push2.eastmoney.com/api/qt/clist/get'


def run():
    CACHE.mkdir(parents=True,exist_ok=True)
    records={};total=None;page=1;proof=[]
    with requests.Session() as session:
        while total is None or page<=math.ceil(total/100):
            if page>200:raise RuntimeError('directory exceeds page bound')
            path=CACHE/('%03d.json'%page)
            if path.exists() and dt.datetime.now().timestamp()-path.stat().st_mtime<86400:
                raw=path.read_bytes()
            else:
                response=session.get(URL,params={'pn':page,'pz':100,'po':1,'np':1,'fltt':2,'invt':2,
                    'fid':'f12','fs':'m:105,m:106,m:107','fields':'f12,f13,f14'},timeout=20)
                response.raise_for_status()  # A rejection/429 stops the batch; never rotate clients.
                raw=response.content
                if len(raw)>1024**2:raise RuntimeError('directory page exceeds byte bound')
                path.write_bytes(raw)
            payload=json.loads(raw);data=payload.get('data') or {}
            if payload.get('rc')!=0 or not isinstance(data.get('total'),int) or not isinstance(data.get('diff'),list):
                raise RuntimeError('unexpected directory schema')
            if total is None:total=data['total']
            if data['total']!=total or not 0<total<=20000:
                raise RuntimeError('directory changed or exceeds security bound')
            entries=data['diff']
            if len(entries)!=min(100,total-(page-1)*100):
                raise RuntimeError('incomplete directory page')
            for row in entries:
                key=(row['f13'],row['f12'])
                if row['f13'] not in (105,106,107) or key in records:
                    raise RuntimeError('unexpected market or duplicate directory key')
                records[key]={'code':row['f12'],'market':row['f13'],'name':row['f14']}
            proof.append(hashlib.sha256(raw).hexdigest())
            if page%20==0:print('directory pages',page,'securities',len(records),flush=True)
            page+=1
    if len(records)!=total:raise RuntimeError('incomplete directory')
    result={'source_url':URL,'collected_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'total':total,'pages':page-1,'page_sha256':proof,'records':sorted(records.values(),key=lambda r:(r['code'],r['market']))}
    (BASE/'output/eastmoney_security_directory.json').write_text(json.dumps(result,separators=(',',':'))+'\n')
    print('Complete directory:',total,'securities',flush=True)


if __name__=='__main__':run()
