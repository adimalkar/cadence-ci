-- Explicit local registration of a fix PR; no publishing or finding-state changes.
ALTER TABLE finding ADD CONSTRAINT finding_repo_identity UNIQUE (repo_id, id);

CREATE TABLE fix_pull_request (
    repo_id bigint NOT NULL REFERENCES repo(id) ON DELETE CASCADE,
    number integer NOT NULL CHECK (number > 0),
    provider_id bigint NOT NULL UNIQUE CHECK (provider_id > 0),
    finding_id uuid NOT NULL,
    url text NOT NULL,
    head_sha text NOT NULL CHECK (head_sha ~ '^[0-9a-f]{40}$'),
    base_ref text NOT NULL CHECK (length(btrim(base_ref)) > 0),
    state text NOT NULL CHECK (state IN ('open', 'closed', 'merged')),
    provider_updated_at timestamptz NOT NULL,
    merge_commit_sha text,
    merged_at timestamptz,
    last_synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repo_id, number),
    FOREIGN KEY (repo_id, finding_id) REFERENCES finding(repo_id, id) ON DELETE CASCADE,
    CHECK (
        (state = 'merged' AND merged_at IS NOT NULL AND merge_commit_sha IS NOT NULL
         AND merge_commit_sha ~ '^[0-9a-f]{40}$' AND provider_updated_at >= merged_at)
        OR (state IN ('open', 'closed') AND merged_at IS NULL AND merge_commit_sha IS NULL)
    )
);
CREATE INDEX fix_pull_request_finding_idx ON fix_pull_request (finding_id);
CREATE INDEX run_repo_pr_idx ON run (repo_id, pull_request_number)
    WHERE pull_request_number IS NOT NULL;
