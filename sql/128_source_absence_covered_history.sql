-- User decision 2026-11-21/22 extension: a delisted member whose required window
-- (2012-01..2025-12 intersected with its own membership span) is ALREADY fully
-- covered by series_selected is closed out for a source that cannot deliver the
-- total_return months inside that window — whether it answers "Not Found",
-- returns only post-rellisting bars, or the whole relay group comes back empty.
--
-- Before this, CA / CTRA / FI (all channels empty) and EQR (only 2026-07..08)
-- were counted as real failures every night while 41 of the 43 queued symbols
-- had zero missing months. A gate that is red every night carries no more
-- information than one that is green every night.
--
-- The verdict shape stays explicit: a 200 is only legal next to
-- provider_no_window_history, and 4xx only next to provider_not_found, so a
-- relay outage (403/429/timeout) still cannot be laundered into a closure.
-- accepted_basis stays 'price_only', and both sql/127 triggers stay: only a
-- confirmed delisting whose members.end_date matches the recorded
-- membership_end may be closed, and the ledger stays append-only.
--
-- Closing here cannot change a reviewed number: db_select.compute_picks already
-- selected price_only for these names, so the only thing a verdict stops is a
-- repeated basis-upgrade attempt. Symbols with a genuine hole in the window
-- (LIFE: 0 months, TGNA: 25 of 66) fail the coverage test in code and keep the
-- job red.

alter table public.source_absence_acceptance
  drop constraint if exists source_absence_acceptance_reason_check,
  drop constraint if exists source_absence_acceptance_http_status_check,
  drop constraint if exists source_absence_acceptance_verdict_shape_check,
  add constraint source_absence_acceptance_verdict_shape_check check (
    (reason = 'provider_not_found' and http_status between 400 and 499)
    or (reason = 'provider_no_window_history' and http_status = 200));
