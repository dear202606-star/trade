# -*- coding: utf-8 -*-
"""退市身份在全收益补采集合里的缺席裁决：SQL 判据与结案台账写入的唯一实现。

两个采集器（fill_yahoo_gaps / fetch_stockanalysis）最终写同一张
source_absence_acceptance，而这张表的价值全在于「什么才算权威缺席」这条
判据不许走偏：判据分叉一次，台账就退化成一句日志，事后无从区分「供应商
明确回答没有这个代码」和「我们懒得再看第二眼」。

口径（用户 2026-11-21 / 2026-11-22 拍板）
  · 只有 members.end_date 已过期（库内当前时钟确认退市）才允许结案；
  · 供应商权威 Not Found（404/400）→ 落台账，不再重打；
  · 源给不出窗口内的全收益、但 series_selected 已把该窗口铺满 → 同样落台账
    （provider_no_window_history）：结案不改任何已复核过的数字，缺的只是口径升级；
    窗口里真有缺月的不在此列——结案等于永久放弃一段本可以补上的历史。
  · 源回的数据全部晚于该身份的最后交易日（代码被重新上市占用）→ 本轮按
    缺席处理，但不落台账：证据每轮重取一次，成本是每天几只票的几秒。
    「全部晚于」也接受源自述的 meta.firstTradeDate：2026-11-22 深夜取证 TGNA，
    机群答话时回的是 200 + 空 timestamp + firstTradeDate=2026-03，一根都没回，
    而这正是「历史属于另一个发行主体」最硬的那种说法（沉默不算，见该函数）。
  · 需要窗口（2012-01..2025-12）内成员月份不足 12 个月的，本来就不该进
    补采集合：任何源都给不出它 12 个月的窗口内历史，重试是纯粹的浪费。
"""

WINDOW_START = '2012-01'
WINDOW_END = '2025-12'
MIN_MEMBERSHIP_MONTHS = 12


def _month(expr):
    """把日期列取成 'YYYY-MM'。用 cast+substr 而不是 extract，sqlite 夹具才跑得动。"""
    return "substr(cast(%s as text),1,7)" % expr


def months_in_window(start_expr, end_expr):
    """所需窗口内的成员月份数（可为负，调用方与 12 比较即可，不必夹 0）。"""
    first = "case when %s < '%s' then '%s' else %s end" % (
        _month(start_expr), WINDOW_START, WINDOW_START, _month(start_expr))
    last = "case when %s > '%s' then '%s' else %s end" % (
        _month(end_expr), WINDOW_END, WINDOW_END, _month(end_expr))
    return ("((cast(substr(%s,1,4) as integer) * 12 + cast(substr(%s,6,2) as integer))"
            " - (cast(substr(%s,1,4) as integer) * 12 + cast(substr(%s,6,2) as integer)) + 1)"
            % (last, last, first, first))


def _index(month):
    return int(month[:4]) * 12 + int(month[5:7])


def belongs_to_later_identity(months, membership_end):
    """源回到的月份是否全部晚于该身份的最后交易日。

    2026-11-22 实测：APC(2019-08 退市) 回 [2026-02..2026-11]、CSRA(2018-04)
    回 [2026-08..2026-11]、SPLS(2017-09) 回 [2026-01..2026-11]——代码已被重新
    上市占用，本标的那段历史在源里不存在，换任何通道、重试任何次数都不会
    多出一根。这和「通道只回了最近几根」是两件事：后者仍在自身生命周期
    内有月份，属于真故障，必须继续报红。
    """
    if not months or not membership_end:
        return False
    end_month = str(membership_end)[:7]
    # 每个月份都得先是个月份：None 走字符串比较是 'None' > '2017-06'，为真。
    # 这条判据负责「源里的历史属于另一个发行主体」，沉默永远不能被读成晚于。
    def _later(month):
        text = str(month or '')
        return (len(text) == 7 and text[:4].isdigit() and text[4] == '-'
                and text[5:].isdigit() and text > end_month)
    return all(_later(m) for m in months)


def delisted_end(conn, symbol):
    """确认退市时返回 members.end_date，否则 None。

    现役标的与「库里没有成员记录」都返回 None：两者的共同点是结案不成立。
    退市判据只写在这一处，两个采集器共用，否则「什么才算退市」会随副本漂移。
    """
    with conn.cursor() as cur:
        cur.execute("select max(end_date) from members where symbol=%s "
                    "group by symbol having max(end_date) < current_date", (symbol,))
        row = cur.fetchone()
    return row[0] if row else None


def close_absence(conn, symbol, source, http_status, runner, basis='total_return'):
    """供应商权威 Not Found → 按 price_only 结案；返回是否真的结案。

    退市不成立（现役、或库内没有成员记录）一律拒绝：结案等于把它永久逐出
    全收益补采。台账是 append-only 的（sql/127），成员身份被复核改期后
    targets() 会自己重新纳入，无需撤销。
    """
    end_date = delisted_end(conn, symbol)
    if not end_date:
        return False
    with conn.cursor() as cur:
        cur.execute("""insert into source_absence_acceptance
                       (symbol, source, basis, reason, http_status, membership_end, runner)
                       values (%s, %s, %s, 'provider_not_found', %s, %s, %s)
                       on conflict do nothing""",
                    (symbol, source, basis, http_status, end_date, runner))
    return True


def window_months(conn, symbol):
    """(已有月份, 应有月份)：窗口 ∩ 该身份的在册区间，数 series_selected 的实际月份。

    「应有」用 months_in_window 同一套月份算术，两边共用一个 WINDOW_END，
    免得窗口口径在两个函数里各漂移一次。
    """
    with conn.cursor() as cur:
        cur.execute("select min(start_date), max(end_date) from members where symbol=%s",
                    (symbol,))
        start, end = cur.fetchone()
    if not start or not end:
        return 0, 0
    first = max(WINDOW_START, str(start)[:7])
    last = min(WINDOW_END, str(end)[:7])
    need = _index(last) - _index(first) + 1
    if need <= 0:
        return 0, 0
    with conn.cursor() as cur:
        cur.execute("select count(distinct month) from series_selected "
                    "where symbol=%s and month between %s and %s", (symbol, first, last))
        have = cur.fetchone()[0]
    return have, need


def close_covered_absence(conn, symbol, source, runner, basis='total_return'):
    """源给不出窗口内的全收益，但选中序列已把这段窗口铺满 → 按 price_only 结案。

    2026-11-22 现场：补采集合 43 只全部已退市，其中 41 只在窗口内一个月都不缺，
    缺的只是口径升级；Yahoo 对它们要么权威 Not Found，要么只回重新上市后的
    最近几根，要么整群通道回空。CA/CTRA/FI/EQR 就这样每晚计一次「真故障」，
    一道永远红着的门禁和永远绿着一样没有信息量。

    与 close_absence 的区别只在取证强度：这里不要求供应商回答过「没有这个代码」，
    而是要求库内证据已经完整——所以通道故障也造不成损失（一个数都不会因此少算），
    而真有缺口的（LIFE 窗口内 0 个月、TGNA 66 个月只有 25 个）一律拒绝结案，
    照旧报红。现役票同样拒绝：退市判据仍由 delisted_end 单独把住。
    """
    end_date = delisted_end(conn, symbol)
    if not end_date:
        return False
    have, need = window_months(conn, symbol)
    if need <= 0 or have < need:
        return False
    with conn.cursor() as cur:
        cur.execute("""insert into source_absence_acceptance
                       (symbol, source, basis, reason, http_status, membership_end, runner)
                       values (%s, %s, %s, 'provider_no_window_history', 200, %s, %s)
                       on conflict do nothing""",
                    (symbol, source, basis, end_date, runner))
    return True
