-- Cadence schema, migration 001
--
-- Design notes:
--   * Step-level timings are the simulator's entire input. They are the reason this
--     schema exists rather than reusing an off-the-shelf DORA warehouse, which retains
--     only run-level aggregates.
--   * `finding` may not exist without `evidence`. That rule is enforced by a CHECK plus
--     a deferred constraint trigger, not by convention -- code review will not hold this
--     line for 24 weeks.

CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ---------------------------------------------------------------- ingest entities

CREATE TABLE repo (
    id              bigint PRIMARY KEY,          -- GitHub's repo id, not a surrogate
    owner           text        NOT NULL,
    name            text        NOT NULL,
    is_private      boolean     NOT NULL DEFAULT false,
    default_branch  text,
    -- ingest bookkeeping
    first_ingested_at timestamptz NOT NULL DEFAULT now(),
    last_polled_at    timestamptz,
    runs_etag         text,                      -- conditional requests; 304s are free
    UNIQUE (owner, name)
);

CREATE TABLE run (
    id              bigint PRIMARY KEY,          -- GitHub workflow run id
    repo_id         bigint      NOT NULL REFERENCES repo(id) ON DELETE CASCADE,
    workflow_id     bigint,
    workflow_path   text,
    workflow_name   text,
    run_number      int,
    -- run_attempt is load-bearing: retries are the strongest flaky signal we get,
    -- and it cannot be backfilled once the 90-day log retention window passes.
    run_attempt     int         NOT NULL DEFAULT 1,
    event           text,                        -- push | pull_request | schedule | ...
    status          text,                        -- queued | in_progress | completed
    conclusion      text,                        -- success | failure | cancelled | ...
    head_sha        text        NOT NULL,
    head_branch     text,
    -- tree_sha enables the strongest flaky label: same tree, different outcome
    tree_sha        text,
    pull_request_number int,
    created_at      timestamptz NOT NULL,
    started_at      timestamptz,
    updated_at      timestamptz,
    ingested_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX run_repo_created_idx  ON run (repo_id, created_at DESC);
CREATE INDEX run_repo_workflow_idx ON run (repo_id, workflow_path);
CREATE INDEX run_head_sha_idx      ON run (repo_id, head_sha);
CREATE INDEX run_tree_sha_idx      ON run (repo_id, tree_sha) WHERE tree_sha IS NOT NULL;

CREATE TABLE job (
    id              bigint PRIMARY KEY,          -- GitHub job id
    run_id          bigint      NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    repo_id         bigint      NOT NULL REFERENCES repo(id) ON DELETE CASCADE,
    name            text        NOT NULL,
    status          text,
    conclusion      text,
    -- runner_labels drives the rate card lookup and the runner-fit rules
    runner_labels   text[]      NOT NULL DEFAULT '{}',
    runner_group    text,
    -- queue time is execution time's twin: a queue-bound repo gets the OPPOSITE advice
    -- from a compute-bound one, and we are the only tool positioned to tell them apart.
    created_at      timestamptz,                 -- job queued
    started_at      timestamptz,                 -- runner picked it up
    completed_at    timestamptz,
    attempt         int         NOT NULL DEFAULT 1,
    -- the matrix leg this job represents, e.g. {"os":"ubuntu-latest","node":"20"}
    matrix          jsonb
);

CREATE INDEX job_run_idx       ON job (run_id);
CREATE INDEX job_repo_name_idx ON job (repo_id, name);

CREATE TABLE step (
    job_id          bigint      NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    number          int         NOT NULL,
    name            text        NOT NULL,
    status          text,
    conclusion      text,
    started_at      timestamptz,
    completed_at    timestamptz,
    PRIMARY KEY (job_id, number)
);

CREATE INDEX step_job_started_idx ON step (job_id, started_at);
-- name-keyed lookup powers "how long does the install step take across all runs",
-- which is how missing-cache detection works (flat duration == no cache).
CREATE INDEX step_name_idx        ON step (name);

CREATE TABLE log_chunk (
    id              bigserial PRIMARY KEY,
    job_id          bigint      NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    -- content-addressed: a log is downloaded exactly once, ever. Log download is the
    -- rate-limit hog, so this is the single most important ingest optimization.
    sha256          text        NOT NULL,
    storage_key     text        NOT NULL,        -- path/key in the object store
    byte_size       bigint      NOT NULL,        -- uncompressed
    compressed_size bigint,
    fetched_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id)
);

CREATE INDEX log_chunk_sha_idx ON log_chunk (sha256);


-- ---------------------------------------------------------------- webhook idempotency

CREATE TABLE webhook_delivery (
    delivery_id     uuid PRIMARY KEY,            -- X-GitHub-Delivery
    event           text        NOT NULL,
    received_at     timestamptz NOT NULL DEFAULT now(),
    processed_at    timestamptz
);


-- ---------------------------------------------------------------- rate cards
--
-- GitHub cut hosted-runner prices up to 39% on 2026-01-01 and folded a $0.002/min
-- platform charge into the listed rates. A self-hosted charge was announced for
-- 2026-03 and then shelved. Rates move -- so they are versioned data, never constants,
-- and every finding records which card produced its dollar figure.

CREATE TABLE rate_card (
    version         int         NOT NULL,
    runner_label    text        NOT NULL,
    os              text        NOT NULL,
    cores           int,
    usd_per_minute  numeric(10, 6) NOT NULL,
    -- public repos get standard hosted runners free; larger runners are billed.
    free_on_public  boolean     NOT NULL DEFAULT false,
    effective_from  date        NOT NULL,
    PRIMARY KEY (version, runner_label)
);

INSERT INTO rate_card (version, runner_label, os, cores, usd_per_minute, free_on_public, effective_from) VALUES
    (2026, 'ubuntu-latest',    'linux',   2, 0.006, true,  '2026-01-01'),
    (2026, 'ubuntu-22.04',     'linux',   2, 0.006, true,  '2026-01-01'),
    (2026, 'ubuntu-24.04',     'linux',   2, 0.006, true,  '2026-01-01'),
    (2026, 'windows-latest',   'windows', 2, 0.010, true,  '2026-01-01'),
    (2026, 'windows-2022',     'windows', 2, 0.010, true,  '2026-01-01'),
    (2026, 'macos-latest',     'macos',   3, 0.062, true,  '2026-01-01'),
    (2026, 'macos-14',         'macos',   3, 0.062, true,  '2026-01-01'),
    (2026, 'ubuntu-latest-4-core',  'linux', 4, 0.012, false, '2026-01-01'),
    (2026, 'ubuntu-latest-8-core',  'linux', 8, 0.024, false, '2026-01-01'),
    (2026, 'ubuntu-latest-16-core', 'linux',16, 0.048, false, '2026-01-01');


-- ---------------------------------------------------------------- findings

CREATE TABLE finding (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id            bigint   NOT NULL REFERENCES repo(id) ON DELETE CASCADE,
    module             text     NOT NULL,   -- 'waste' | 'flake' | 'observability'
    kind               text     NOT NULL,   -- 'no_dependency_cache' | 'false_needs_edge' | ...
    severity           smallint NOT NULL CHECK (severity BETWEEN 1 AND 5),
    confidence         real     NOT NULL CHECK (confidence BETWEEN 0 AND 1),

    -- Semantic identity, never hash(file, line, rule). Waste findings key on
    -- (rule, workflow_path, job_name) so editing the YAML does not orphan a suppression.
    dedupe_key         text     NOT NULL,
    fingerprint_v      smallint NOT NULL DEFAULT 1,

    status             text     NOT NULL DEFAULT 'new'
                         CHECK (status IN ('new','acknowledged','resolved','suppressed','regressed')),
    suppress_scope     text CHECK (suppress_scope IN ('finding','rule_path','rule_repo')),
    suppressed_by      bigint,
    suppressed_reason  text,

    first_seen_commit  text NOT NULL,
    last_seen_commit   text NOT NULL,
    first_seen_at      timestamptz NOT NULL DEFAULT now(),
    last_seen_at       timestamptz NOT NULL DEFAULT now(),
    resolved_at        timestamptz,

    title              text NOT NULL,
    suggested_action   text,
    llm_narrative      text,                -- nullable, NEVER load-bearing
    detector_version   text NOT NULL,

    -- Savings. `basis` separates replay (arithmetic over observed timings) from
    -- projection (estimate of an unobserved state). They are never blended into one
    -- number -- the credibility of the product rests on that separation.
    est_seconds_saved_per_run  real,
    est_dollars_per_month      real,
    savings_basis              text CHECK (savings_basis IN
                                 ('replay','projection_intra_repo','projection_corpus')),
    rate_card_version          int,
    -- backfilled 30 days after a fix PR merges; this is the calibration ground truth
    -- and the only reason the public calibration dashboard can exist.
    realized_seconds_per_run   real,
    realized_dollars_per_month real,
    realized_at                timestamptz,

    UNIQUE (repo_id, dedupe_key, fingerprint_v)
);

CREATE INDEX finding_repo_status_idx ON finding (repo_id, status);
CREATE INDEX finding_kind_idx        ON finding (kind);

CREATE TABLE evidence (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    finding_id   uuid NOT NULL REFERENCES finding(id) ON DELETE CASCADE,
    kind         text NOT NULL,
    -- code_range: a span in a workflow YAML (or source) file
    file_path    text,
    line_start   int,
    line_end     int,
    -- log_span: a byte range inside a stored log
    log_chunk_id bigint REFERENCES log_chunk(id) ON DELETE SET NULL,
    byte_start   int,
    byte_end     int,
    -- run_history: the runs this claim was computed from
    run_ids      bigint[],
    -- timing_series / counterfactual: simulator input, output distribution, basis
    payload      jsonb,
    CHECK (
        (kind = 'code_range'     AND file_path IS NOT NULL AND line_start IS NOT NULL)
     OR (kind = 'log_span'       AND log_chunk_id IS NOT NULL)
     -- coalesce is load-bearing: array_length('{}', 1) is NULL, not 0, and a CHECK
     -- passes on NULL. Without it, a run_history row citing zero runs is accepted --
     -- an assertion wearing evidence's clothes, which is the one thing this table exists
     -- to prevent.
     OR (kind = 'run_history'    AND coalesce(array_length(run_ids, 1), 0) > 0)
     OR (kind = 'timing_series'  AND payload IS NOT NULL)
     OR (kind = 'counterfactual' AND payload IS NOT NULL)
     OR (kind = 'graph_path'     AND payload IS NOT NULL)
    )
);

CREATE INDEX evidence_finding_idx ON evidence (finding_id);


-- ---------------------------------------------------------------- the structural rule
--
-- "No finding without evidence." A CHECK cannot express this (it spans tables), so it
-- is a CONSTRAINT TRIGGER deferred to commit: insert the finding, insert its evidence,
-- commit. A finding committed alone aborts the transaction.

CREATE OR REPLACE FUNCTION finding_requires_evidence() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM evidence WHERE finding_id = NEW.id) THEN
        RAISE EXCEPTION
            'finding % (kind=%) has no evidence rows; every finding must cite evidence',
            NEW.id, NEW.kind
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER finding_requires_evidence_trg
    AFTER INSERT ON finding
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION finding_requires_evidence();
