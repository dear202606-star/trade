"""Invert same-vintage Yahoo adjusted closes using their complete cash/split events.

A is dividend-and-split adjusted; d is a dividend in that same split-adjusted
share unit. With D_after known, D_before = D_after/(1+D_after*d/A_before).
Nominal P = A/D times the product of later split ratios. Never apply events to
a different snapshot's prices. The last quote must be the snapshot's latest
quote, where D=1; collectors must validate that source-vintage boundary.
"""
import datetime as dt
import math


def restore(rows):
    parsed, previous, last = [], [], None
    dates = set()
    for row in rows:
        day = dt.date.fromisoformat(row['Date'][:10]).isoformat()
        if day in dates or (parsed and day <= parsed[-1]['date']):
            raise ValueError('duplicate or unsorted date')
        dates.add(day)
        split, dividend = float(row['Stock Splits']), float(row['Dividends'])
        if not math.isfinite(split) or split < 0 or not math.isfinite(dividend) or dividend < 0:
            raise ValueError('invalid corporate action')
        close = None if row['Close'] == '' else float(row['Close'])
        if close is None:
            if not (split or dividend) or any(row.get(k) not in ('', None) for k in ('Open','High','Low','Volume')):
                raise ValueError('missing close without a valid action-only row')
        elif not math.isfinite(close) or close <= 0:
            raise ValueError('invalid adjusted close')
        previous.append(last)
        parsed.append({'date':day,'adjusted':close,'split':split,'dividend':dividend})
        if close is not None:
            last=close
    if not parsed or parsed[-1]['adjusted'] is None:
        raise ValueError('snapshot lacks a terminal quote')
    dividend_factor, later_splits = 1.0, 1.0
    output = []
    for i in range(len(parsed)-1,-1,-1):
        row = parsed[i]
        if row['adjusted'] is not None:
            nominal = row['adjusted']/dividend_factor*later_splits
            if not math.isfinite(nominal) or nominal <= 0:
                raise ValueError('invalid reconstructed price')
            output.append({'date':row['date'],'close':nominal,
                           'adjusted':row['adjusted']})
        if row['split']:
            later_splits *= row['split']
        if row['dividend']:
            # An ex-dividend event on/before the first observed quote changes
            # no earlier supplied prices. The current quote excludes its own
            # dividend already; do not reject the usable subsequent history.
            if previous[i] is not None:
                dividend_factor /= 1+dividend_factor*row['dividend']/previous[i]
    return list(reversed(output))
