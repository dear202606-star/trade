-- BK is a reviewed historical alias for the same BNY common equity.
-- Preserve later old-code observations but exclude them from selected reads.
insert into public.security_history_window(symbol,month_to,evidence_url,reason)
values ('BK','2026-12',
 'https://www.sec.gov/Archives/edgar/data/1390777/000119312526281160/d137058d8k.htm',
 'BK to BNY same common equity from 2026-05-21; reviewed return alias ends at rename-year holding end; future BK reuse is unreviewed')
on conflict(symbol) do nothing;
