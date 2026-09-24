"""`concurrency.add` — the first Phase 2 fixer.

Most of these tests are about **declining**, because that is where a fixer earns trust.
`PHASE_2_FIX_PRS.md`: *"Declining to fix is always correct; a wrong fix is not."* A bad
cache config wastes a minute; cancelling a production deploy mid-flight does not.

The rest pin the two properties the fix is only worth anything with: it touches nothing but
the lines it adds, and the detector stops firing once it merges — otherwise the fix would
be re-proposed forever, which is the spam failure the anti-spam rules exist to prevent.
"""

from __future__ import annotations

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.fixers.concurrency import ConcurrencyFixer
from cadence.workflow import parse_workflow

PATH = ".github/workflows/ci.yml"

CI = """\
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pytest
"""


def _finding(path: str = PATH) -> FindingDraft:
    return FindingDraft(
        kind="no_run_cancellation", module="waste", severity=3, confidence=0.95,
        dedupe_key=f"no_run_cancellation:{path}", title="t", detector_version="v1",
        evidence=[EvidenceDraft(kind="run_history", run_ids=[1])],
    )


def fix(content: str, path: str = PATH):
    return ConcurrencyFixer().preview(parse_workflow(path, content), content, _finding(path))


# --- the fix itself ------------------------------------------------------------------------


class TestWhatItInserts:
    def test_a_plain_ci_workflow_gets_the_block(self):
        out = fix(CI)
        assert out.ok, out.declined
        assert "concurrency:" in out.diff.after
        assert "cancel-in-progress: true" in out.diff.after

    def test_it_uses_a_literal_true_so_the_detector_is_satisfied(self):
        """An expression would leave `cancel_in_progress` False — the parser refuses to
        evaluate expressions — and the fix would be re-proposed after it merged."""
        out = fix(CI)
        assert parse_workflow(PATH, out.diff.after).cancel_in_progress is True

    def test_only_the_inserted_lines_change(self):
        """The whole point of text-first: every original line survives, in order.

        Checked by position, not by set membership — a set test cannot tell the inserted
        blank line from one the file already had.
        """
        out = fix(CI)
        after = out.diff.after.splitlines()
        at = after.index("concurrency:")
        assert after[:at] + after[at + 4 :] == CI.splitlines()
        assert out.diff.changed_lines == 4

    def test_the_block_sits_directly_above_jobs(self):
        lines = fix(CI).diff.after.splitlines()
        i = lines.index("jobs:")
        assert lines[i - 4] == "concurrency:"
        assert lines[i - 1] == ""

    def test_the_group_matches_how_the_detector_groups_runs(self):
        """By workflow and ref (branch) — so the saving claimed is the saving delivered."""
        assert "${{ github.workflow }}-${{ github.ref }}" in fix(CI).diff.after

    def test_a_revert_hint_is_always_attached(self):
        """A PR a maintainer cannot cheaply reverse is one they will not merge."""
        assert "concurrency:" in fix(CI).diff.revert_hint


class TestItMatchesTheFileStyle:
    def test_four_space_indent_is_followed(self):
        four = CI.replace("\n  test:", "\n    test:").replace("\n    runs-on", "\n        runs-on")
        out = fix(four)
        assert out.ok, out.declined
        assert "\n    group: " in out.diff.after

    def test_crlf_line_endings_are_preserved(self):
        crlf = CI.replace("\n", "\r\n")
        out = fix(crlf)
        assert out.ok, out.declined
        assert "\r\n" in out.diff.after
        assert "\n" not in out.diff.after.replace("\r\n", "")

    def test_a_comment_on_jobs_stays_attached_to_jobs(self):
        """Inserting between a comment and the key it describes would detach it."""
        commented = CI.replace("jobs:", "# The test matrix.\njobs:")
        lines = fix(commented).diff.after.splitlines()
        assert lines[lines.index("jobs:") - 1] == "# The test matrix."


# --- declining -----------------------------------------------------------------------------


class TestItDeclinesWhenItShould:
    def test_existing_concurrency_is_left_alone(self):
        """Changing someone's block overrides a choice they made — propose, don't push."""
        existing = CI.replace("jobs:", "concurrency: ci-${{ github.ref }}\n\njobs:")
        out = fix(existing)
        assert not out.ok
        assert "already declares concurrency" in out.declined

    def test_an_explicit_false_is_respected(self):
        """`cancel-in-progress: false` may be deliberate. Never flip it automatically."""
        explicit = CI.replace(
            "jobs:", "concurrency:\n  group: x\n  cancel-in-progress: false\n\njobs:"
        )
        assert not fix(explicit).ok

    def test_a_deploy_workflow_is_declined(self):
        out = fix(CI, ".github/workflows/deploy.yml")
        assert not out.ok
        assert "deploy" in out.declined

    def test_a_deploy_named_workflow_is_declined(self):
        out = fix(CI.replace("name: CI", "name: Publish to PyPI"))
        assert not out.ok

    def test_a_job_with_an_environment_is_declined(self):
        runs = "    runs-on: ubuntu-latest"
        env = CI.replace(runs, runs + "\n    environment: production")
        out = fix(env)
        assert not out.ok
        assert "environment" in out.declined

    def test_write_permissions_are_declined(self):
        perms = CI.replace("jobs:", "permissions:\n  id-token: write\n\njobs:")
        out = fix(perms)
        assert not out.ok
        assert "write permissions" in out.declined

    def test_read_only_permissions_do_not_block(self):
        """`contents: read` is the least-privilege default; it must not look like a deploy."""
        perms = CI.replace("jobs:", "permissions:\n  contents: read\n\njobs:")
        assert fix(perms).ok

    def test_release_triggers_are_declined(self):
        out = fix(CI.replace("  pull_request:", "  pull_request:\n  release:\n    types: [x]"))
        assert not out.ok
        assert "release" in out.declined

    def test_tag_pushes_are_declined(self):
        out = fix(CI.replace("    branches: [main]", "    tags: ['v*']"))
        assert not out.ok
        assert "tag" in out.declined

    def test_reusable_workflows_are_declined(self):
        """In a called workflow `github.workflow` names the caller, so two calls in one run
        would share a group and cancel each other."""
        triggers = "on:\n  push:\n    branches: [main]\n  pull_request:"
        called = CI.replace(triggers, "on:\n  workflow_call:")
        out = fix(called)
        assert not out.ok
        assert "workflow_call" in out.declined


class TestItDeclinesRatherThanGuesses:
    def test_a_mismatched_finding_is_refused(self):
        wf = parse_workflow(PATH, CI)
        out = ConcurrencyFixer().preview(wf, CI, _finding(".github/workflows/other.yml"))
        assert not out.ok

    def test_the_wrong_finding_kind_is_refused(self):
        wf = parse_workflow(PATH, CI)
        wrong = _finding()
        wrong.kind = "long_tail_step"
        assert not ConcurrencyFixer().preview(wf, CI, wrong).ok

    def test_an_unparseable_file_is_refused(self):
        out = fix("name: CI\njobs: [unclosed\n")
        assert not out.ok

    def test_a_file_without_top_level_jobs_is_refused(self):
        out = fix("name: CI\non: push\n")
        assert not out.ok

    def test_every_decline_carries_a_reason(self):
        """Across strangers' repositories nobody can ask us why a fix did not appear."""
        for content, path in (
            (CI, ".github/workflows/deploy.yml"),
            ("name: CI\non: push\n", PATH),
        ):
            out = fix(content, path)
            assert not out.ok
            assert out.declined and len(out.declined) > 10
