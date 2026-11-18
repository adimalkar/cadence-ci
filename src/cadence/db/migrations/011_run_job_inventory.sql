-- Historical receipts only. Consumers MUST verify current membership/status in a
-- consistent snapshot via job_inventory.job_coverage; mixed-version writers may change jobs.
-- No backfill: existing counts/webhook rows do not prove an exhausted REST inventory.
CREATE TABLE run_job_inventory (
    repo_id bigint NOT NULL REFERENCES repo(id) ON DELETE CASCADE,
    run_id bigint NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    attempt integer NOT NULL CHECK (attempt > 0),
    expected_count integer NOT NULL CHECK (expected_count BETWEEN 0 AND 10000),
    job_ids bigint[] NOT NULL,
    pages integer NOT NULL CHECK (pages BETWEEN 1 AND 10000),
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (run_id, attempt),
    CHECK (cardinality(job_ids) = expected_count),
    CHECK (array_position(job_ids, NULL) IS NULL),
    CHECK (0 < ALL(job_ids))
);
