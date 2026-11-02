-- Only the reviewed old-code return alias through the rename year's holding
-- end is eligible. Later FB-labelled observations remain archived unchanged.
insert into public.security_history_window(symbol,month_to,evidence_url,reason)
values ('FB','2022-12',
 'https://www.sec.gov/Archives/edgar/data/1326801/000132680122000070/fb-20220531.htm',
 'FB to META same Class A equity from 2022-06-09; reviewed historical old-code alias ends at 2022 holding-year end; later FB-labelled observations are unreviewed')
on conflict(symbol) do nothing;
