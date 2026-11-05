"""Exercise the production fingerprint SQL in temporary PostgreSQL objects only."""
import json
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import db_prices
import db_sources


def run():
    connection=db_sources.db()
    checks=[]
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local statement_timeout='20s'")
            cursor.execute('''create temporary table series_selected(
                symbol text,month text,adj_close numeric,raw_close numeric,source text,basis text)''')
            cursor.execute("""insert into pg_temp.series_selected values
                ('A','2025-12',10,11,'reviewed','total_return'),
                ('B','2025-12',20,null,'reviewed','total_return')""")
            before=db_prices.selected_stamp(cursor)
            mutations={
                'equal_sum_price_swap':"update pg_temp.series_selected set adj_close=30-adj_close",
                'raw_only':"update pg_temp.series_selected set raw_close=12 where symbol='A'",
                'missing_raw_to_adjusted':"update pg_temp.series_selected set raw_close=adj_close where symbol='B'",
                'source_only':"update pg_temp.series_selected set source='other' where symbol='A'",
                'basis_only':"update pg_temp.series_selected set basis='price_only' where symbol='A'",
                'identity_only':"update pg_temp.series_selected set symbol='C' where symbol='A'",
                'month_only':"update pg_temp.series_selected set month='2025-11' where symbol='A'",
                'below_old_cent_rounding':"update pg_temp.series_selected set adj_close=adj_close+.00001 where symbol='A'",
            }
            for name,sql in mutations.items():
                cursor.execute('savepoint fingerprint_probe');cursor.execute(sql)
                after=db_prices.selected_stamp(cursor)
                if before==after:raise RuntimeError('Undetected selected input mutation: '+name)
                checks.append(name);cursor.execute('rollback to savepoint fingerprint_probe')
            cursor.execute('create temporary table reordered as select * from pg_temp.series_selected order by symbol desc')
            cursor.execute('delete from pg_temp.series_selected')
            cursor.execute('insert into pg_temp.series_selected select * from reordered')
            if db_prices.selected_stamp(cursor)!=before:raise RuntimeError('Fingerprint depends on physical row order')
            checks.append('physical_order_stable')
        connection.rollback()
    finally:connection.rollback();connection.close()
    result={'temporary_objects_only':True,'public_data_changed':False,'passed_checks':checks}
    (BASE/'output/selected_fingerprint_verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':run()
