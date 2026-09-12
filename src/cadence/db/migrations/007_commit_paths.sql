-- Persist what each commit changed. One fetch, four things unblocked.
--
-- Until now `enrich_changed_paths` fetched changed files per commit, handed them to the
-- detectors in memory, and threw them away. Its `seen` cache is function-local, so a
-- second audit of the same repo re-paid the full cost, and the corpus sweep could never
-- afford it at all. The consequence is measured: `irrelevant_path_trigger` fires on 0 of
-- 51 repos, because `evalsweep` never calls the enrichment (CAVEATS 44, 45).
--
-- The same `GET /repos/{o}/{r}/commits/{sha}` response carries three things we want:
--
--   1. `files[].filename`  -- changed paths, for the path-trigger rule and for asking
--                             which parts of a repo break CI
--   2. `commit.tree.sha`   -- `run.tree_sha` has been NULL on all 29,134 rows since 001,
--                             with a partial index built for it and never used. The
--                             schema calls it "the strongest flaky label: same tree,
--                             different outcome" and nothing has ever populated it.
--   3. authored/committed timestamps -- cheap, and they let a repo's change rate be
--                             measured without a second endpoint.
--
-- Cost measured 2026-09-08: 6,174 distinct head_shas across the 90-day corpus window, so
-- a full backfill is ~6.2k requests against a 5,000/hour limit. Per repo the median is
-- far smaller. Caching makes it a one-time cost per commit rather than per audit, which
-- is the difference between "opt-in behind a flag" and "always on".

CREATE TABLE repo_commit (
    repo_id     bigint      NOT NULL REFERENCES repo(id) ON DELETE CASCADE,
    sha         text        NOT NULL,
    tree_sha    text,
    -- Changed paths as an array rather than a child table. A commit's file list is read
    -- whole, never joined against, and Postgres arrays keep it one row per commit --
    -- which matters when the alternative is tens of millions of narrow rows for a corpus
    -- this size. `unnest` covers the aggregate queries.
    paths       text[]      NOT NULL DEFAULT '{}',
    -- Distinguishes "this commit changed nothing we can see" from "we never asked".
    -- A merge commit legitimately reports no files, and so does a commit whose diff
    -- exceeded GitHub's 300-file response cap -- see `truncated`.
    path_count  int         NOT NULL DEFAULT 0,
    -- GitHub caps the `files` array at 300 entries. Past that the list is incomplete, and
    -- a fragility measure computed from a truncated list would silently under-count the
    -- very largest changes. Recorded so a consumer can withhold rather than guess.
    truncated   boolean     NOT NULL DEFAULT false,
    authored_at timestamptz,
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repo_id, sha)
);

-- "Which commits have we not fetched yet" -- the backfill's driving query.
CREATE INDEX repo_commit_fetched_idx ON repo_commit (repo_id, fetched_at DESC);

-- "Same tree, different outcome" -- the flaky label run.tree_sha was indexed for and
-- never given data. Kept here so the join has a home once the backfill has run.
CREATE INDEX repo_commit_tree_idx ON repo_commit (repo_id, tree_sha)
    WHERE tree_sha IS NOT NULL;
