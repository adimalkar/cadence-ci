-- Persist the workflow YAML we analyse. Until now it was fetched live at audit time and
-- thrown away, which cost three things:
--
--   1. No config history, so "the workflow changed here and waste started" was
--      unanswerable -- and that is a Phase 3 blame feature as well as a Phase 1 one.
--   2. Phase 2's round-trip ship criterion ("200 corpus workflows, byte-identical when no
--      fix applied") was not reproducible: re-fetching from HEAD lets the corpus shift
--      under the test, so a failure could not be told apart from an upstream edit.
--   3. Every re-analysis spent API calls re-reading bytes we had already seen, against a
--      rate limit the ingest worker is already competing for.

-- Content-addressed, mirroring log_chunk: workflow files change rarely, so 51 repos
-- observed repeatedly collapse to a small number of distinct blobs.
CREATE TABLE workflow_blob (
    content_sha text        PRIMARY KEY,   -- sha256 of content, hex
    content     text        NOT NULL,
    byte_size   int         NOT NULL,
    first_seen  timestamptz NOT NULL DEFAULT now()
);

-- One row per (repo, path, distinct content). A config edit produces a *new* row rather
-- than mutating one, so the history of a workflow file is the rows for that path ordered
-- by first_seen -- no commit resolution and no extra API call needed to build it.
--
-- Deliberately not keyed by commit sha. Anchoring to a commit would mean either an extra
-- request per capture to resolve HEAD, or changing which ref the audit reads and with it
-- the analysis results. Content plus observation window answers every question we
-- actually have.
CREATE TABLE workflow_snapshot (
    id          bigserial   PRIMARY KEY,
    repo_id     bigint      NOT NULL REFERENCES repo(id) ON DELETE CASCADE,
    path        text        NOT NULL,      -- .github/workflows/ci.yml
    content_sha text        NOT NULL REFERENCES workflow_blob(content_sha),
    -- What was asked for, for provenance: NULL means the default branch's HEAD.
    ref         text,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (repo_id, path, content_sha)
);

-- "What did this repo's config look like, and when did it change" -- the history query.
CREATE INDEX workflow_snapshot_history_idx ON workflow_snapshot (repo_id, path, first_seen DESC);

-- "Which repos have we ever seen this exact file in" -- shared-config detection later.
CREATE INDEX workflow_snapshot_content_idx ON workflow_snapshot (content_sha);
