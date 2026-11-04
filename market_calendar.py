"""Online reviewed NYSE cash-equity sessions; never infer beyond coverage."""
import datetime as dt
import hashlib
import json
from zoneinfo import ZoneInfo

import db_sources

ET = ZoneInfo('America/New_York')
UTC = dt.timezone.utc
SOURCE = 'verified_exchange_calendar'


def fingerprint(calendar):
    return hashlib.sha256(json.dumps(calendar,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class Calendar:
    def __init__(self, body, sha):
        if fingerprint(body)!=sha or body.get('market')!='XNYS':
            raise ValueError('Unverified exchange calendar')
        self.body = body
        self.sha = sha
        self.start = dt.date.fromisoformat(body['coverage_from'])
        self.end = dt.date.fromisoformat(body['coverage_to'])
        self.closed = {dt.date.fromisoformat(d) for d in body['closed']}
        self.early = {dt.date.fromisoformat(d):dt.time.fromisoformat(t) for d,t in body['early_close'].items()}
        self.open_time=dt.time.fromisoformat(body['regular_open'])
        self.close_time=dt.time.fromisoformat(body['regular_close'])
        if body.get('timezone')!='America/New_York' or self.open_time>=self.close_time:
            raise ValueError('Invalid regular session hours')
        if len(self.closed)!=len(body['closed']) or any(not self.open_time<t<self.close_time for t in self.early.values()):
            raise ValueError('Invalid exchange exceptions')
        if self.start>=self.end or self.closed&set(self.early):
            raise ValueError('Conflicting calendar dates')
        if any(d<self.start or d>self.end or d.weekday()>4 for d in self.closed|set(self.early)):
            raise ValueError('Invalid exchange exception date')

    def bounds(self, day):
        if not self.start<=day<=self.end:
            raise RuntimeError('Exchange calendar coverage missing: '+day.isoformat())
        if day.weekday()>4 or day in self.closed:
            return None
        return (dt.datetime.combine(day,self.open_time,ET).astimezone(UTC),
                dt.datetime.combine(day,self.early.get(day,self.close_time),ET).astimezone(UTC))

    def previous(self, day):
        while True:
            day -= dt.timedelta(days=1)
            if self.bounds(day) is not None:
                return day

    def source_deadline(self, stamp):
        if stamp.tzinfo is None:raise ValueError('Source clock requires timezone')
        stamp=stamp.astimezone(UTC)
        day=stamp.astimezone(ET).date()
        bounds=self.bounds(day)
        if bounds is None:raise ValueError('Source date is not an exchange session')
        if stamp<bounds[1]:return stamp+dt.timedelta(minutes=20)
        while True:
            day+=dt.timedelta(days=1)
            following=self.bounds(day)
            if following is not None:return following[0]+dt.timedelta(minutes=20)

    def state(self, now):
        if now.tzinfo is None:
            raise ValueError('Exchange clock requires timezone')
        now = now.astimezone(UTC)
        today = now.astimezone(ET).date()
        current = self.bounds(today)
        if current and now>=current[0]:
            opened,closed = current
            previous = self.previous(today)
            previous_close = self.bounds(previous)[1]
            # Allow the documented delay budget at opening and settlement.
            minimum = previous_close if now<opened+dt.timedelta(minutes=20) else min(now-dt.timedelta(minutes=20),closed)
            return {'session':today,'previous_session':previous,'open':opened,'close':closed,
                    'intraday':now<closed,'refresh_due':opened<=now<=closed+dt.timedelta(minutes=20),
                    'minimum_source_time':minimum}
        previous = self.previous(today)
        previous_close = self.bounds(previous)[1]
        return {'session':previous,'previous_session':self.previous(previous),
                'open':None,'close':previous_close,'intraday':False,'refresh_due':False,
                'minimum_source_time':previous_close}


def load():
    connection = db_sources.db()
    try:
        with connection.cursor() as cursor:
            cursor.execute('select calendar,evidence_sha256 from v_market_calendar')
            rows = cursor.fetchall()
        if len(rows)!=1:
            raise RuntimeError('No unique reviewed online exchange calendar')
        return Calendar(*rows[0])
    finally:
        connection.close()
