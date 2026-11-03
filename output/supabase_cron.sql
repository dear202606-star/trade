-- 1) 清理测试数据
delete from public.holdings;

-- 2) 扩展：定时任务 + HTTP 调用
create extension if not exists pg_cron;
create extension if not exists pg_net;

-- 3) GitHub Token 存入 Vault（加密存储，不出现在任何表里）
do $$
begin
  if not exists (select 1 from vault.secrets where name = 'gh_dispatch_token') then
    perform vault.create_secret(
      '__PAT__',
      'gh_dispatch_token',
      'GitHub fine-grained PAT：仅本仓库 Actions 读写'
    );
  else
    perform vault.update_secret(
      (select id from vault.secrets where name = 'gh_dispatch_token'),
      '__PAT__'
    );
  end if;
end $$;

-- 4) 每小时 15 分触发一次 GitHub 流水线（替代不可靠的 GitHub 原生 schedule）
select cron.unschedule('trade-dispatch')
where exists (select 1 from cron.job where jobname = 'trade-dispatch');

select cron.schedule(
  'trade-dispatch',
  '*/5 13-21 * * 1-5',
  $job$
  select net.http_post(
    url := 'https://api.github.com/repos/dear202606-star/trade/actions/workflows/quotes.yml/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'gh_dispatch_token'),
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'Content-Type', 'application/json',
      'User-Agent', 'supabase-pg-cron'
    ),
    body := '{"ref":"main"}'::jsonb,
    timeout_milliseconds := 8000
  );
  $job$
);
