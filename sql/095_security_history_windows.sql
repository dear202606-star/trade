-- Keep original provider observations, but never present post-delisting/reused
-- ticker quotes as the historical index security. Partial exit months require
-- separate consideration/corporate-action accounting, not a phantom close.
begin;
create table if not exists security_history_window (
  symbol text primary key,
  month_to text not null check(month_to ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  evidence_url text not null,
  reason text not null
);
alter table security_history_window enable row level security;
insert into security_history_window(symbol,month_to,evidence_url,reason) values
('BJS','2010-03','https://www.sec.gov/Archives/edgar/data/808362/000095012310039884/h72191exv99w1.htm','BJ Services merger effective 2010-04-28; later monthly quotes are not complete original-security months'),
('AYE','2011-01','https://www.sec.gov/Archives/edgar/data/3673/000119312511047579/d8k.htm','Allegheny shares converted in FirstEnergy merger 2011-02-25'),
('LB','2021-07','https://www.sec.gov/Archives/edgar/data/701985/000114036121026658/nt10026698x7_8k.htm','L Brands renamed BBWI 2021-08-03 with VSCO separation; LB since 2024 is unrelated LandBridge'),
('HES','2025-06','https://www.sec.gov/Archives/edgar/data/93410/000095014225001949/eh250652625_8k.htm','Hess shares converted to Chevron shares before market open 2025-07-18'),
('ALXN','2021-06','https://www.astrazeneca.com/media-centre/press-releases/2021/acquisition-of-alexion-completed.html','Alexion acquisition completed 2021-07-21; later original-ticker month-end quotes are unreviewed'),
('XLNX','2022-01','https://www.amd.com/en/newsroom/press-releases/2022-2-14-amd-completes-acquisition-of-xilinx.html','Xilinx acquisition completed 2022-02-14')
on conflict(symbol) do nothing;

create or replace view v_series as
select p.symbol,p.month,p.adj_close,p.raw_close,p.source,p.basis
from prices_monthly p
join series_pick k on k.symbol=p.symbol and k.source=p.source
where p.month>='2008-01'
  and not exists(select 1 from series_quarantine q where q.symbol=p.symbol)
  and not exists(select 1 from security_history_window w
                 where w.symbol=p.symbol and p.month>w.month_to);
commit;
