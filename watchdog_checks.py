"""Validate the published quality report without accepting missing evidence."""
import datetime as dt
import hashlib
import json
import math
from zoneinfo import ZoneInfo


def timestamp(value):
    if not isinstance(value,(str,dt.datetime)):raise ValueError('时间无效')
    result=value if isinstance(value,dt.datetime) else dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('时间缺少时区')
    return result.astimezone(dt.timezone.utc)


def quote_problems(row, heartbeat, calendar, now, *, closing_only=False):
    """Check the current source session and real worker proof, not history writes."""
    if not isinstance(row,dict) or not isinstance(row.get('payload'),dict):
        raise ValueError('缺少在线报价快照')
    payload=dict(row['payload']);version=payload.pop('version',None)
    sha=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    if sha!=row.get('evidence_sha256') or sha!=version:
        raise ValueError('在线报价内容散列不一致')
    quotes=payload.get('quotes');count=payload.get('count');universe=payload.get('universe')
    if (payload.get('ok') is not True or not isinstance(quotes,dict) or 'SPY' not in quotes
            or type(count) is not int or type(universe) is not int or universe<=0
            or count!=len(quotes) or count<.98*universe):
        raise ValueError('在线报价覆盖或基准无效')
    source=timestamp(row['source_as_of'])
    if source!=timestamp(payload['ts']):raise ValueError('报价源时间与快照不一致')
    state=calendar.state(now);problems=[]
    minimum = state['minimum_source_time']
    if closing_only:
        from trading_window import due_close
        close = due_close(now, calendar.body)
        if not close or payload.get('intraday') is not False:
            problems.append('有效收盘行情缺失')
        else:
            minimum = close['close']
            if str(payload.get('session') or '') < close['session']:
                problems.append('行情停更：最新收盘场次缺失')
    if payload.get('calendar_sha256')!=calendar.sha:
        problems.append('报价日历版本与在线核验版本不一致')
    if source>now+dt.timedelta(minutes=5):problems.append('报价源时间来自未来')
    if source<minimum:problems.append('报价未达到交易所应有时点')
    if payload.get('session')!=source.astimezone(ZoneInfo('America/New_York')).date().isoformat():
        problems.append('报价场次与源时间不一致')
    elif payload.get('previous_session')!=calendar.previous(dt.date.fromisoformat(payload['session'])).isoformat():
        problems.append('报价前一场次与核验日历不一致')
    deadlines=[]
    for symbol,quote in quotes.items():
        if not isinstance(quote,dict) or type(quote.get('p')) not in (int,float) or not math.isfinite(quote['p']) or quote['p']<=0:
            problems.append('成分报价价格无效：'+symbol)
            continue
        try:
            stamp=timestamp(quote.get('as_of'))
            deadline=calendar.source_deadline(stamp)
        except (ValueError,TypeError,RuntimeError):
            problems.append('成分报价时间无效：'+symbol)
            continue
        deadlines.append(deadline)
        if stamp.astimezone(ZoneInfo('America/New_York')).date().isoformat()!=payload.get('session'):
            problems.append('成分报价场次不一致：'+symbol)
        if stamp>now+dt.timedelta(minutes=5):problems.append('成分报价时间来自未来：'+symbol)
        if stamp<minimum:problems.append('成分报价未达到应有时点：'+symbol)
        if symbol=='SPY' and stamp!=source:problems.append('基准成分时间与快照不一致')
    try:
        deadline=timestamp(payload.get('fresh_until'))
        if not deadlines or deadline!=min(deadlines):problems.append('报价有效期与成分核验日历不一致')
    except (ValueError,TypeError):
        problems.append('报价有效期缺失或无效')
    if heartbeat is None:
        problems.append('缺少实际报价刷新回执')
    else:
        if heartbeat.get('metrics',{}).get('stored_evidence_sha256')!=sha:
            problems.append('刷新回执与当前报价版本不一致')
        finished=timestamp(heartbeat['finished_at'])
        if finished>now+dt.timedelta(minutes=5):problems.append('刷新回执时间来自未来')
        if not closing_only and state['refresh_due'] and now>=state['open']+dt.timedelta(minutes=15) and now-finished>dt.timedelta(minutes=15):
            problems.append('报价刷新任务超过 15 分钟未成功')
    return problems


def history_problems(report, now, *, frozen=False):
    """Frozen history may stay unchanged; validate content and annual coverage."""
    if not isinstance(report,dict) or not isinstance(report.get('years'),list) or not report['years']:
        raise ValueError('历史产物缺少年度记录')
    years=[r.get('year') for r in report['years'] if isinstance(r,dict)]
    if len(years)!=len(report['years']) or any(type(y) is not int for y in years) or len(set(years))!=len(years):
        raise ValueError('历史年度记录无效或重复')
    problems=[]
    if sorted(years)!=list(range(min(years),max(years)+1)):problems.append('历史产物缺少区间内年度')
    if not frozen and max(years)<now.year-1:problems.append('历史产物缺少新增的已结束年度')
    generated=report.get('generated_at')
    if generated and timestamp(generated)>now+dt.timedelta(minutes=5):
        problems.append('历史生成时间来自未来')
    return problems


def published_dependency_problems(equity, preview, quotes, now=None):
    """A healthy quote worker does not prove dependent calculations advanced."""
    if not isinstance(quotes,dict) or not quotes.get('version') or not quotes.get('session'):
        raise ValueError('缺少在线报价版本或场次')
    problems=[]
    now=now or dt.datetime.now(dt.timezone.utc)
    def current(payload):
        if not isinstance(payload,dict) or not payload.get('quote_version'):return False
        if payload['quote_version']==quotes['version']:return True
        try:
            source=timestamp(payload.get('source_as_of') or payload.get('as_of_ts'))
            deadline=timestamp(payload.get('fresh_until'))
            return source<=timestamp(quotes.get('ts')) and source<deadline and now<deadline
        except (ValueError,TypeError):return False
    settled=str((equity or {}).get('last_session','')).replace('-','')
    allowed={str(quotes.get(k,'')).replace('-','') for k in ('session','previous_session')}
    if not settled or settled not in allowed or not current(equity):
        problems.append('收益基准落后于可衔接报价场次')
    if isinstance(equity,dict) and equity.get('market_advice_error'):
        problems.append('大盘建议缺少已核验的月末价格')
    if (not isinstance(preview,dict) or not preview.get('reference_version')
            or not current(preview)
            or preview.get('session')!=quotes['session']
            or not isinstance(preview.get('top'),list) or len(preview['top'])!=3):
        problems.append('次年预览未跟随当前报价版本完成核验')
    return problems


def quality_problems(report):
    if not isinstance(report, dict):
        raise ValueError('质量报告不是对象')
    summary = report.get('summary')
    if not isinstance(summary, dict):
        raise ValueError('质量报告缺少 summary')
    failures = summary.get('fail')
    if type(failures) is not int or failures < 0:
        raise ValueError('质量报告缺少有效的失败计数')
    degraded = report.get('degraded')
    if type(degraded) is not bool:
        raise ValueError('质量报告缺少有效的降级状态')
    checks = report.get('checks')
    if not isinstance(checks, list) or not checks:
        raise ValueError('质量报告缺少检查明细')
    statuses = []
    for check in checks:
        if not isinstance(check, dict) or check.get('status') not in ('pass', 'warn', 'fail'):
            raise ValueError('质量报告检查状态无效')
        statuses.append(check['status'])
    if statuses.count('fail') != failures:
        raise ValueError('质量报告失败计数与明细不一致')
    problems = []
    if failures:
        problems.append('线上质量报告有 %d 项校验失败' % failures)
    if degraded:
        problems.append('数据仍为降级态：%s' % report.get('reason', '未提供原因'))
    return problems
