"""Real rollback probes for active and flag-based selection locks."""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import db_select


def run():
    connection = db_select.db()
    connection.autocommit = False
    results = []
    try:
        with connection.cursor() as cursor:
            cursor.execute("""select p.proname,pg_get_functiondef(p.oid) from pg_trigger t
                              join pg_proc p on p.oid=t.tgfoid
                              where t.tgrelid='series_pick'::regclass and t.tgname='trg_guard_pick'""")
            name, definition = cursor.fetchone()
            if name != 'guard_frozen_pick':
                raise RuntimeError('unexpected active trigger binding')
            cursor.execute("""select p.proname,pg_get_functiondef(p.oid) from pg_trigger t
                              join pg_proc p on p.oid=t.tgfoid
                              where t.tgrelid='series_pick'::regclass and t.tgname='trg_guard_pick_basis'""")
            basis_name, basis_definition = cursor.fetchone()
            if basis_name!='guard_pick_basis_evidence':
                raise RuntimeError('unexpected basis evidence trigger binding')
            cursor.execute("select relkind,pg_get_viewdef(oid) from pg_class where oid='locked_symbols'::regclass")
            kind, legacy_view = cursor.fetchone()
            if kind!='v' or 'FROM data_lock' not in legacy_view or "'symbol_month'" not in legacy_view:
                raise RuntimeError('unexpected legacy lock compatibility binding')
            cursor.execute('''select k.symbol,k.source,k.basis from series_pick k where not k.frozen
                and not exists(select 1 from locked_symbols l where l.symbol=k.symbol)
                and not exists(select 1 from data_lock l where l.symbol=k.symbol and l.status='locked'
                  and not exists(select 1 from data_lock x where x.supersedes_id=l.id and x.status='void'))
                order by k.symbol limit 1''')
            symbol, source, basis = cursor.fetchone()
            changed_basis = 'price_only' if basis == 'total_return' else 'total_return'

            def reject(label, sql, args):
                cursor.execute('savepoint attempt')
                try:
                    cursor.execute(sql,args)
                except Exception as exc:
                    if getattr(exc,'pgcode',None)!='P0001':
                        raise
                    cursor.execute('rollback to savepoint attempt')
                    cursor.execute('release savepoint attempt')
                    results.append({'probe':label,'result':'rejected'})
                else:
                    raise AssertionError(label+' unexpectedly allowed')

            cursor.execute('savepoint scenario')
            cursor.execute('update series_pick set reason=reason where symbol=%s',(symbol,))
            if cursor.rowcount!=1:
                raise AssertionError('unlocked metadata probe did not update one row')
            results.append({'probe':'unlocked metadata update','result':'allowed then rolled back'})
            cursor.execute('rollback to savepoint scenario')
            reject('unlocked wrong basis','update series_pick set basis=%s where symbol=%s',(changed_basis,symbol))
            reject('unobserved source','update series_pick set source=%s where symbol=%s',('__UNKNOWN_SOURCE__',symbol))
            upsert = '''insert into series_pick(symbol,source,basis) values (%s,%s,%s)
                        on conflict(symbol) do update set basis=excluded.basis'''
            reject('wrong basis insert/upsert',upsert,(symbol,source,changed_basis))
            cursor.execute(upsert,(symbol,source,basis))
            if cursor.rowcount!=1:
                raise AssertionError('valid unlocked upsert did not update one row')
            results.append({'probe':'unlocked valid upsert','result':'allowed then rolled back'})
            cursor.execute('rollback to savepoint scenario')

            cursor.execute('update series_pick set frozen=true where symbol=%s',(symbol,))
            reject('frozen basis','update series_pick set basis=%s where symbol=%s',(changed_basis,symbol))
            reject('frozen delete','delete from series_pick where symbol=%s',(symbol,))
            reject('frozen rename','update series_pick set symbol=%s where symbol=%s',('__LOCK_PROBE__',symbol))
            reject('frozen thaw','update series_pick set frozen=false where symbol=%s',(symbol,))
            cursor.execute('rollback to savepoint scenario')

            cursor.execute('''select k.symbol,k.basis from series_pick k join data_lock l using(symbol)
                where l.status='locked' and not exists(select 1 from data_lock x
                  where x.supersedes_id=l.id and x.status='void') order by k.symbol limit 1''')
            active, active_basis = cursor.fetchone()
            opposite = 'price_only' if active_basis=='total_return' else 'total_return'
            reject('active lock basis','update series_pick set basis=%s where symbol=%s',(opposite,active))
            reject('active lock delete','delete from series_pick where symbol=%s',(active,))
            reject('active lock rename','update series_pick set symbol=%s where symbol=%s',('__LOCK_PROBE__',active))
            reject('active lock unchanged metadata','update series_pick set reason=reason where symbol=%s',(active,))
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute('select basis,frozen from series_pick where symbol=%s',(symbol,))
            if cursor.fetchone()!=(basis,False):
                raise AssertionError('probe metadata was not rolled back')
            cursor.execute('select count(*) from locked_symbols where symbol=%s',(symbol,))
            if cursor.fetchone()[0]:
                raise AssertionError('probe legacy lock was not rolled back')
    finally:
        connection.rollback()
        connection.close()
    result={'verified_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'active_guard_sha256':hashlib.sha256(definition.encode()).hexdigest(),
            'basis_evidence_guard_sha256':hashlib.sha256(basis_definition.encode()).hexdigest(),
            'legacy_lock_view_sha256':hashlib.sha256(legacy_view.encode()).hexdigest(),
            'legacy_lock_compatibility':'locked_symbols is an active symbol_month data_lock view',
            'probes':results,'all_probe_changes_rolled_back':True}
    (BASE/'output/selection_guard_verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS:',len(results),'real guard probes; no probe changes retained')


if __name__=='__main__':
    run()
