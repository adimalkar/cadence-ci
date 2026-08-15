-- Cadence schema, migration 002: the ingest queue.
--
-- Postgres, not Redis/Celery -- one less thing to operate, and SKIP LOCKED gives
-- correct concurrent claiming with no broker. Revisit only on a measured bottleneck,
-- which at this scale it will not be.
--
-- Three kinds share one table: a webhook delivery becomes a job the moment it's
-- verified (so a delivery marked "received" that never got enqueued would be a silent
-- drop -- see webhook.py), a corpus repo re-enqueues itself 30 minutes out after each
-- poll (the one line that turns a single backfill into continuous ingest), and a log
-- fetch is queued explicitly since log download is the rate-limit hog and must never
-- happen automatically for every job.

CREATE TABLE ingest_job (
    id          bigserial PRIMARY KEY,
    kind        text        NOT NULL
                  CHECK (kind IN ('poll_repo', 'webhook_event', 'fetch_log')),
    payload     jsonb       NOT NULL,
    run_at      timestamptz NOT NULL DEFAULT now(),
    status      text        NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    attempts    int         NOT NULL DEFAULT 0,
    last_error  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Partial: only rows a worker could actually claim need to be fast to find. Done/failed
-- rows accumulate as history and must not bloat the index a live worker scans.
CREATE INDEX ingest_job_claimable_idx ON ingest_job (run_at) WHERE status = 'pending';
