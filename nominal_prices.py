"""As-traded screening prices. Never substitute adjusted/unknown close values."""
import math

import db_select

_PRICES = None


def load_all():
    global _PRICES
    if _PRICES is not None:
        return _PRICES
    connection = None
    try:
        connection = db_select.db()
        connection.autocommit = False
        result = {}
        with connection.cursor(name='nominal_observations') as cursor:
            cursor.itersize = 2000
            cursor.execute('select symbol,month,close from v_nominal_monthly')
            for symbol, month, close in cursor:
                result[(symbol, month)] = float(close)
    except Exception:
        import db_rest
        rows = db_rest.get('v_nominal_monthly', {
            'select': 'symbol,month,close', 'order': 'symbol,month'})
        result = {(r['symbol'], r['month']): float(r['close']) for r in rows}
    finally:
        if connection is not None:
            connection.close()
    if not result or any(not math.isfinite(v) or v <= 0 for v in result.values()):
        raise RuntimeError('名义股价库为空或包含非法价格，禁止改用复权价筛选')
    _PRICES = result
    return result


def price(symbol, month):
    value = load_all().get((symbol, month))
    if value is None:
        raise RuntimeError('%s %s 缺少已核验的当时实际股价，不能完成筛选' % (symbol, month))
    return value
