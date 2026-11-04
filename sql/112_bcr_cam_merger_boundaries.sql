-- Original common shares cease to be tradable month-end assets after conversion.
-- Retain raw observations. Cash/successor wealth belongs in a reviewed event path.
insert into public.security_history_window(symbol,month_to,evidence_url,reason) values
 ('BCR','2017-11',
  'https://investors.bd.com/sec-filings/all-sec-filings/content/0001193125-17-383523/d517273dex991.htm',
  'Bard shares converted on 2017-12-29 into USD222.93 cash and 0.5077 BD shares; December 28 quote is not December month-end wealth. Post-conversion original-ticker observations excluded; prior history and consideration path not thereby accepted.'),
 ('CAM','2016-03',
  'https://www.sec.gov/Archives/edgar/data/87347/000156459016026174/R12.htm',
  'Cameron shares converted on 2016-04-01 into USD14.44 cash and 0.716 Schlumberger shares; April 1 quote is not April month-end wealth. Post-conversion original-ticker observations excluded; prior history and consideration path not thereby accepted.')
on conflict(symbol) do nothing;
