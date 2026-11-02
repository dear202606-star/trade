"""Live regression of the promotion RPC, using synthetic rows rolled back.

Requires an idle queue. Briefly locks staging to keep production inserts out of
the test transaction. Never changes real prices, source picks, or lock records.
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_sources


def run():
    connection = db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("set local lock_timeout='5s'")
            cursor.execute('lock table ingest_staging in share row exclusive mode')
            cursor.execute('select count(*) from ingest_staging where not promoted and reject_reason is null')
            if cursor.fetchone()[0]:
                raise RuntimeError('Queue is not idle; no fixture rows were inserted')
            symbols = ['__BATCH_QUEUE_TEST__', '__BATCH_WAIT_TEST__', '__BATCH_BAD_TEST__']
            cursor.execute('select count(*) from prices_monthly where symbol=any(%s)', (symbols,))
            assert cursor.fetchone()[0] == 0
            cursor.execute("""insert into ingest_staging(symbol,month,adj_close,raw_close,source,quality,basis)
                select '__BATCH_QUEUE_TEST__',to_char(month,'YYYY-MM'),100,100,
                       'kaggle_yahoo2020','ok','total_return'
                from generate_series('2010-01-01'::date,'2011-12-01'::date,interval '1 month') month""")
            cursor.execute("""insert into ingest_staging(symbol,month,adj_close,raw_close,source,quality,basis)
                values ('__BATCH_WAIT_TEST__','2010-01',100,100,'kaggle_yahoo2020','ok','total_return'),
                       ('__BATCH_BAD_TEST__','2010-01','NaN',100,'kaggle_yahoo2020','ok','total_return')""")
            cursor.execute('select inserted from promote_staging(1)')
            assert cursor.fetchone()[0] == 1
            cursor.execute("""select count(*) filter(where promoted),count(*) filter(where not promoted)
                              from ingest_staging where symbol='__BATCH_QUEUE_TEST__'""")
            assert cursor.fetchone() == (1, 23), 'Overflow was marked promoted'
            cursor.execute("select promoted,reject_reason from ingest_staging where symbol='__BATCH_WAIT_TEST__'")
            assert cursor.fetchone() == (False, None), 'New symbol below 24 months was consumed'
            cursor.execute("select promoted,reject_reason from ingest_staging where symbol='__BATCH_BAD_TEST__'")
            assert cursor.fetchone() == (False, 'bad_price'), 'NaN was not rejected'
            cursor.execute('select inserted from promote_staging(1)')
            assert cursor.fetchone()[0] == 1
            cursor.execute("select count(*) from ingest_staging where symbol='__BATCH_QUEUE_TEST__' and promoted")
            assert cursor.fetchone()[0] == 2, 'Retry did not process the next pending row'
            print('PASS: limit overflow remains pending, next call resumes, <24-month symbols wait, NaN rejected')
    finally:
        connection.rollback()
        connection.close()
    with db_sources.db() as connection, connection.cursor() as cursor:
        cursor.execute('select count(*) from prices_monthly where symbol=any(%s)', (symbols,))
        assert cursor.fetchone()[0] == 0
        cursor.execute('select count(*) from ingest_staging where symbol=any(%s)', (symbols,))
        assert cursor.fetchone()[0] == 0
    print('PASS: all synthetic price/staging rows rolled back')


if __name__ == '__main__':
    run()
