-- 出口流量调度收敛：消除 GitHub Actions 与 pg_cron 的双触发
--
-- 实测（2026-10-28 计费周期）：行情快照被触发 1139 次/10 天。根因是
--   · GitHub quotes.yml schedule  "*/5 13-21 * * 1-5"
--   · pg_cron trade-dispatch-market "*/5 13-21 * * 1-5"  → 完全相同
--   · pg_cron trade-dispatch        "20 */2 * * *"       → 每 2 小时再拉一次
-- 三个调度都通过 dispatch_trade_pipeline() 发 GitHub workflow_dispatch，
-- 于是同一份行情被重复采集，且每次都把 live-strategy（workflow_run）
-- 连锁拉起。该周期 Egress 19.928 / 5 GB，Free 档被 Fair Use 限制。
--
-- 决策：调度权统一收归 GitHub Actions（有日志、可版本化、可预算门禁），
-- 停掉两个重复的 pg_cron dispatch 作业；promote_staging 是纯库内数据
-- 合并（不发起网络请求），保留原频率不动。
--
-- 幂等：可重复执行；作业已不存在时什么都不做。
-- 手动恢复（如需回到 pg_cron 调度）请在 SQL Editor 里重新 cron.schedule，
-- 不要修改本文件来「打开」旧调度，避免与 GitHub schedule 再次冲突。

do $$
begin
  if exists (select 1 from cron.job where jobname = 'trade-dispatch-market') then
    perform cron.unschedule('trade-dispatch-market');
    raise notice 'stopped duplicate pg_cron job: trade-dispatch-market';
  end if;
  if exists (select 1 from cron.job where jobname = 'trade-dispatch') then
    perform cron.unschedule('trade-dispatch');
    raise notice 'stopped redundant pg_cron job: trade-dispatch';
  end if;
end $$;
