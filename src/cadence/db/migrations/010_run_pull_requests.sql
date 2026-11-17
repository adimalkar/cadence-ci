-- Preserve positive associations; an empty API list is not proof of no PR.
-- 009 is reserved for the independent fix-PR registry implementation.
ALTER TABLE run ADD CONSTRAINT run_repo_identity UNIQUE (repo_id, id);
ALTER TABLE run ADD COLUMN pr_payload_state text NOT NULL DEFAULT 'unknown'
    CHECK (pr_payload_state IN ('unknown', 'empty', 'reported', 'malformed'));

CREATE TABLE run_pull_request (
    repo_id bigint NOT NULL,
    run_id bigint NOT NULL,
    number integer NOT NULL CHECK (number > 0),
    source text NOT NULL CHECK (source IN ('provider', 'legacy')),
    first_recorded_at timestamptz NOT NULL DEFAULT now(),
    last_recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, number),
    FOREIGN KEY (repo_id, run_id) REFERENCES run(repo_id, id) ON DELETE CASCADE
);
CREATE INDEX run_pull_request_lookup_idx ON run_pull_request (repo_id, number, run_id);

-- Existing scalar values are positive evidence, with explicitly weaker provenance.
INSERT INTO run_pull_request (repo_id, run_id, number, source)
SELECT repo_id, id, pull_request_number, 'legacy' FROM run WHERE pull_request_number > 0;

-- Old workers may still write the scalar during deployment. Capture those positives too.
CREATE FUNCTION capture_run_legacy_pr() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.pull_request_number > 0 THEN
        INSERT INTO run_pull_request (repo_id, run_id, number, source)
        VALUES (NEW.repo_id, NEW.id, NEW.pull_request_number, 'legacy')
        ON CONFLICT (run_id, number) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER run_legacy_pr_capture
AFTER INSERT OR UPDATE OF pull_request_number ON run
FOR EACH ROW EXECUTE FUNCTION capture_run_legacy_pr();
