-- Make suppression reachable. The columns have existed since 001 and nothing ever wrote
-- them: no ignore file, no inline comment, no CLI verb. Phase 2's anti-spam rule 3 -- "a
-- closed PR permanently suppresses that finding at rule_repo scope" -- was therefore
-- unimplementable, and a declined fix would have been re-proposed on every audit. That is
-- the behaviour that gets a bot muted, and it is not recoverable. See CAVEATS 37.

-- When, and by what route. `suppressed_by` in 001 was meant for a user id, but there is no
-- user table and a single maintainer running a CLI is not identified by one. Provenance is
-- the useful fact instead: a suppression that came from a committed `.cadenceignore` is a
-- team decision, one typed at a CLI is a person's, and they age differently.
ALTER TABLE finding ADD COLUMN IF NOT EXISTS suppressed_at timestamptz;
ALTER TABLE finding ADD COLUMN IF NOT EXISTS suppress_source text
    CHECK (suppress_source IN ('ignore_file', 'inline', 'cli', 'closed_pr'));

-- A reason is mandatory, enforced here rather than in code, for the same reason evidence
-- is enforced by a trigger: a rule that lives only in review is a rule that erodes. A
-- suppression without a reason becomes a permanent mystery -- nobody can tell six months
-- later whether the finding was wrong, already fixed, or merely inconvenient, so nobody
-- ever dares remove it.
ALTER TABLE finding DROP CONSTRAINT IF EXISTS finding_suppression_needs_reason;
ALTER TABLE finding ADD CONSTRAINT finding_suppression_needs_reason CHECK (
    status <> 'suppressed'
    OR (suppressed_reason IS NOT NULL AND btrim(suppressed_reason) <> ''
        AND suppress_scope IS NOT NULL AND suppress_source IS NOT NULL)
);

-- "What is silenced in this repo, and why" -- the query a maintainer runs before trusting
-- a clean report, and the one that keeps suppression from becoming a quiet uninstall.
CREATE INDEX IF NOT EXISTS finding_suppressed_idx
    ON finding (repo_id, status) WHERE status = 'suppressed';
