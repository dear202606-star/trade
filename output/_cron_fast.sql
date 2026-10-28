-- 加快数据更新：美股盘中每 15 分钟，其余时段每小时
select cron.unschedule('trade-dispatch') where exists (select 1 from cron.job where jobname = 'trade-dispatch');
select cron.unschedule('trade-dispatch-market') where exists (select 1 from cron.job where jobname = 'trade-dispatch-market');

-- 盘中（UTC 13:30–21:00 = 北京 21:30–05:00）：每 15 分钟
select cron.schedule('trade-dispatch-market', '*/15 13-21 * * 1-5', 'select public.dispatch_trade_pipeline();');

-- 其余时间：每 2 小时（保证休市日也有兜底刷新）
select cron.schedule('trade-dispatch', '20 */2 * * *', 'select public.dispatch_trade_pipeline();');

-- 结果确认
select jobname, schedule, active from cron.job order by jobname;
