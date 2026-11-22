# -*- coding: utf-8 -*-
"""退市身份在全收益补采集合里的缺席裁决：SQL 判据与结案台账写入的唯一实现。

两个采集器（fill_yahoo_gaps / fetch_stockanalysis）最终写同一张
source_absence_acceptance，而这张表的价值全在于「什么才算权威缺席」这条
判据不许走偏：判据分叉一次，台账就退化成一句日志，事后无从区分「供应商
明确回答没有这个代码」和「我们懒得再看第二眼」。

口径（用户 2026-11-21 / 2026-11-22 拍板）
  · 只有 members.end_date 已过期（库内当前时钟确认退市）才允许结案；
  · 供应商权威 Not Found（404/400）→ 落台账，不再重打；
  · 源回的数据全部晚于该身份的最后交易日（代码被重新上市占用）→ 本轮按
    缺席处理，但不落台账：证据每轮重取一次，成本是每天几只票的几秒。
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
    return all(str(m) > end_month for m in months)


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
