-- A fresh ownership token for each claim/reclaim. Existing rows remain tokenless until
-- claimed again; do not mint tokens for old workers that cannot present them.
-- Apply before updated workers, and restart old workers: unfenced legacy SQL can still
-- acknowledge rows by ID alone. This fences queue state, not dispatch side effects.
ALTER TABLE ingest_job ADD COLUMN claim_token uuid;
