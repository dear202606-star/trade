"""Online identity boundaries for historically reused or merged stock codes."""
import datetime as dt
import hashlib
import json
from pathlib import Path

import db_select

SYMBOLS = {'IR', 'JCI'}
_REVIEWS = None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load():
    global _REVIEWS
    if _REVIEWS is not None:
        return _REVIEWS
    connection = db_select.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""select subject,metrics from sys_event where kind='validation'
                and source='historical_identity_review' and status='pass' order by subject,id""")
            rows = cursor.fetchall()
            cursor.execute('select symbol,source from series_pick where symbol=any(%s)', (sorted(SYMBOLS),))
            sources = dict(cursor.fetchall())
    finally:
        connection.close()
    reviews = {}
    for subject, metrics in rows:
        proof = metrics.get('proof', {})
        symbol = proof.get('symbol')
        if (symbol not in SYMBOLS or symbol in reviews or subject != symbol+':lineage-v1'
                or metrics.get('evidence_sha256') != digest(proof)
                or proof.get('selected_source') != sources.get(symbol)
                or proof.get('full_history_accepted') is not False):
            raise RuntimeError('Historical identity review is missing, conflicting or stale')
        dt.date.fromisoformat(proof['comparison_from']+'-01')
        reviews[symbol] = dict(proof, evidence_sha256=metrics['evidence_sha256'])
    if set(reviews) != SYMBOLS:
        raise RuntimeError('Required historical identity reviews are missing')
    _REVIEWS = reviews
    return reviews


def input_sha():
    return digest({'reviews':load(),'guard_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


def comparison(symbol):
    return load()[symbol] if symbol in SYMBOLS else None


def require_rank(symbol, month, base_month=None):
    if symbol == 'TT' and base_month is not None:
        boundary = load()['IR']['comparison_from']
        if base_month < boundary <= month:
            raise RuntimeError('TT transition window requires actual TT and distributed IR ownership evidence')
    review = comparison(symbol)
    if review and month < review['comparison_from']:
        raise RuntimeError(symbol+' '+month+' historical index equity differs from selected price lineage')
    if review and base_month is not None and base_month < review['comparison_from'] <= month:
        raise RuntimeError(symbol+' identity transition window requires actual ownership and cash-flow evidence')


def require_holding(symbol, entry_month, end_month=None):
    if symbol == 'TT' and end_month is not None:
        boundary = load()['IR']['comparison_from']
        if entry_month < boundary <= end_month:
            raise RuntimeError('TT original-equity holding path requires independent TT and IR evidence')
    review = comparison(symbol)
    if review and entry_month < review['comparison_from']:
        raise RuntimeError(symbol+' '+entry_month+' original-equity holding path requires independent evidence')


def require_membership(symbols, year):
    if 'IR' in symbols:
        transition_year = int(comparison('IR')['new_ir_index_admission'][:4])
        if year == transition_year:
            raise RuntimeError('IR '+str(year)+' contains two index securities; old IR→TT continuity and new IR admission require identity-aware membership')


def require_complete_history():
    unresolved = sorted(s for s,p in load().items() if p.get('full_history_accepted') is not True)
    if unresolved:
        raise RuntimeError('Full historical generation has unresolved equity identities: '+','.join(unresolved))
