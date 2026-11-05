-- User approved this read-time quarantine on 2026-11-05. Apply only after
-- matching the rollback evidence. Preserve prices, picks, locks and evidence.
-- Original Sunoco, Inc. (CIK 95304) exited on 2012-10-05:
-- https://www.sec.gov/Archives/edgar/data/1012569/000119312512416888/d421405d8k.htm
-- Different common units first traded as SUSP in September 2012, becoming SUN
-- on 2014-10-27 (CIK 1552275):
-- https://www.sec.gov/Archives/edgar/data/1552275/000155227515000007/sun-20141231x10k.htm
insert into public.series_quarantine(symbol,reason)
values ('SUN',
 'Historical index member is Sunoco, Inc. (CIK 95304), acquired 2012-10-05. Current SUN is Sunoco LP (CIK 1552275), first traded as SUSP in 2012-09 and renamed SUN 2014-10-27. Selected Yahoo history begins 2012-09 and is not verified original-equity lineage. Preserve observations and existing locks; original common shares and actual merger consideration require separate review. SEC d421405d8k; sun-20141231x10k.')
on conflict(symbol) do nothing;
