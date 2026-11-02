-- Reviewed common-equity rename; does not copy prices or change source picks.
-- The proof hash binds the audited alias record in verified_ticker_renames.json.
-- Effective date: issuer announcement and OCC memo 58945 (2026-05-21).
begin;
insert into public.verified_ticker_renames
 (old_symbol,current_symbol,effective_date,evidence_url,evidence_sha256,verified)
values ('BK','BNY','2026-05-21',
 'https://www.sec.gov/Archives/edgar/data/1390777/000119312526281160/d137058d8k.htm',
 '7ec51539702648288be803cc7a7ffedfccd3a096efb03ba9b24844a79bfb1be4',true)
on conflict (old_symbol,current_symbol,effective_date) do nothing;
commit;
