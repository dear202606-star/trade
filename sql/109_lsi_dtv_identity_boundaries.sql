-- LSI Corporation and Life Storage are different common equities. The latter
-- inherits SSS history; agreement among ticker-labelled vendors is insufficient.
insert into public.series_quarantine(symbol,reason)
values ('LSI',
 'Original index security is LSI Corporation (CIK 703360); selected 2013-12 close 65.17 is Sovran/Life Storage history, outside original issuer Q4 range 7.39-11.04. SEC d649519dprer14a; d243423dex991. Preserve observations; independently identify original equity before release.')
on conflict(symbol) do nothing;

insert into public.security_history_window(symbol,month_to,evidence_url,reason) values
 ('LSI','2014-04',
  'https://www.sec.gov/Archives/edgar/data/1441634/000144163414000045/R21.htm',
  'LSI Corporation common shares converted to USD11.15 cash on 2014-05-06; later LSI ticker belongs to unrelated Life Storage. Entire current series separately quarantined for pre-exit identity contamination.'),
 ('DTV','2015-06',
  'https://www.sec.gov/Archives/edgar/data/1465112/000104746915006714/a2225634z10-q.htm',
  'DIRECTV common shares converted on 2015-07-24 to USD28.50 cash plus 1.892 AT&T common shares; original ticker quotes after June are not complete month-end holding values.')
on conflict(symbol) do nothing;
