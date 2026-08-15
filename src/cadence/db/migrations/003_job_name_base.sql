-- Migration 003: stop destroying job-name information.
--
-- `_parse_matrix` strips trailing parens from every job name, so a job literally named
-- "deploy (staging)" and one named "deploy (production)" both collapsed to "deploy" --
-- merging two unrelated timing distributions under one identity. GitHub gives no way to
-- distinguish a matrix leg from a name that merely contains parentheses, so guessing
-- one interpretation and discarding the other is the actual bug.
--
-- Keep both. `name` is now the verbatim GitHub job name (unique per matrix leg, which is
-- what the finding dedupe key needs); `name_base` is the parenthetical-stripped form
-- (shared across legs, which is what "how long does `cargo test` usually take" needs).

ALTER TABLE job ADD COLUMN name_base text;

-- Backfill: existing rows hold the stripped form in `name`, so it is already the base.
-- The verbatim name is unrecoverable for these rows without a re-poll; leaving `name`
-- as-is keeps them self-consistent, and re-polling overwrites both correctly.
UPDATE job SET name_base = name WHERE name_base IS NULL;

ALTER TABLE job ALTER COLUMN name_base SET NOT NULL;

CREATE INDEX job_repo_name_base_idx ON job (repo_id, name_base);
