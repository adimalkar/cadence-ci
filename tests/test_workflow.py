from __future__ import annotations

from cadence.workflow import parse_workflow

CI = """\
name: CI
on:
  push:
    branches: [main]
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
jobs:
  setup:
    runs-on: ubuntu-latest
    outputs:
      version: ${{ steps.v.outputs.version }}
    steps:
      - uses: actions/checkout@v4
      - id: v
        run: echo "version=1" >> $GITHUB_OUTPUT
  test:
    name: Test Suite
    needs: [setup]
    runs-on: ubuntu-latest
    strategy:
      matrix:
        node: [18, 20, 22]
        os: [ubuntu-latest, macos-latest]
        include:
          - node: 18
            experimental: true
    steps:
      - uses: actions/checkout@v4
      - uses: actions/cache@v4
        with:
          path: ~/.npm
          key: ${{ runner.os }}-${{ hashFiles('**/package-lock.json') }}
      - run: npm ci
"""


class TestParsing:
    def test_extracts_jobs_and_needs(self):
        wf = parse_workflow(".github/workflows/ci.yml", CI)
        assert wf.parse_error is None
        assert set(wf.jobs) == {"setup", "test"}
        assert wf.jobs["test"].needs == ["setup"]

    def test_cancel_in_progress_detected(self):
        wf = parse_workflow("ci.yml", CI)
        assert wf.cancel_in_progress is True

    def test_cancel_in_progress_false_when_absent(self):
        wf = parse_workflow("ci.yml", CI.replace("  cancel-in-progress: true\n", ""))
        assert wf.cancel_in_progress is False

    def test_expression_cancel_in_progress_is_not_trusted(self):
        """We cannot evaluate an expression, and claiming a saving we cannot verify is
        worse than staying quiet."""
        wf = parse_workflow(
            "ci.yml",
            CI.replace("cancel-in-progress: true", "cancel-in-progress: ${{ !inputs.keep }}"),
        )
        assert wf.cancel_in_progress is False

    def test_line_numbers_are_captured_for_evidence(self):
        wf = parse_workflow("ci.yml", CI)
        # Every finding cites a code_range; without real lines it points nowhere.
        assert wf.jobs["setup"].line > 0
        assert wf.jobs["test"].line > wf.jobs["setup"].line
        cache_step = next(s for s in wf.jobs["test"].steps if s.action == "actions/cache")
        assert cache_step.line > 0

    def test_action_identity_strips_version(self):
        wf = parse_workflow("ci.yml", CI)
        actions = {s.action for s in wf.jobs["test"].steps if s.action}
        assert "actions/cache" in actions  # not actions/cache@v4

    def test_matrix_dimensions_exclude_include_and_exclude(self):
        """include/exclude are modifiers, not dimensions -- counting them inflates the
        leg count that the matrix rules depend on."""
        wf = parse_workflow("ci.yml", CI)
        matrix = wf.jobs["test"].matrix
        assert set(matrix) == {"node", "os"}
        assert wf.jobs["test"].matrix_leg_count == 6  # 3 node x 2 os

    def test_runtime_names_cover_key_and_display_name(self):
        """Observed jobs record name_base, which may be either -- matching only the key
        would miss every job that sets `name:`."""
        wf = parse_workflow("ci.yml", CI)
        assert wf.jobs["test"].runtime_names == {"test", "Test Suite"}
        assert wf.job_for_runtime_name("Test Suite") is wf.jobs["test"]
        assert wf.job_for_runtime_name("setup") is wf.jobs["setup"]


HAND_WRITTEN_PARENS = """\
jobs:
  cargo-test-linux:
    name: "cargo test (linux)"
    steps: []
  cargo-test-linux-release:
    name: "cargo test (linux, release)"
    steps: []
  build:
    steps: []
"""


class TestJobMatching:
    """Regression: matching on `name_base` alone mapped almost nothing on real repos.

    Ruff writes `name: "cargo test (linux)"` — a hand-written name GitHub renders
    identically to a matrix suffix, so ingest strips it to `cargo test`. With only the
    stripped form to match on, the DAG collapsed to one node and critical-path analysis
    silently produced nonsense.
    """

    def test_verbatim_name_matches_exactly(self):
        wf = parse_workflow("ci.yml", HAND_WRITTEN_PARENS)
        job = wf.job_for_runtime_name("cargo test (linux)", "cargo test")
        assert job is not None
        assert job.key == "cargo-test-linux"

    def test_verbatim_match_distinguishes_jobs_sharing_a_stripped_name(self):
        wf = parse_workflow("ci.yml", HAND_WRITTEN_PARENS)
        a = wf.job_for_runtime_name("cargo test (linux)", "cargo test")
        b = wf.job_for_runtime_name("cargo test (linux, release)", "cargo test")
        assert a.key == "cargo-test-linux"
        assert b.key == "cargo-test-linux-release"

    def test_ambiguous_stripped_name_refuses_to_guess(self):
        """Two config jobs share the stripped name `cargo test`. Without a verbatim
        match we cannot tell them apart, and guessing would attribute one job's timings
        to another."""
        wf = parse_workflow("ci.yml", HAND_WRITTEN_PARENS)
        assert wf.job_for_runtime_name(None, "cargo test") is None

    def test_stripped_fallback_works_for_a_genuine_matrix_leg(self):
        wf = parse_workflow("ci.yml", """\
jobs:
  test:
    steps: []
""")
        # Runtime leg "test (ubuntu-latest, 20)" -> name_base "test"
        assert wf.job_for_runtime_name("test (ubuntu-latest, 20)", "test").key == "test"

    def test_unmatched_name_returns_none(self):
        wf = parse_workflow("ci.yml", HAND_WRITTEN_PARENS)
        assert wf.job_for_runtime_name("build / inner", "build / inner") is None


class TestRobustness:
    def test_malformed_yaml_yields_parse_error_not_exception(self):
        """One unparseable workflow must not abort a repo's whole audit."""
        wf = parse_workflow("bad.yml", "jobs:\n  build:\n   - [unclosed\n")
        assert wf.parse_error is not None
        assert wf.jobs == {}

    def test_non_mapping_root_handled(self):
        wf = parse_workflow("bad.yml", "- just\n- a list\n")
        assert wf.parse_error is not None

    def test_on_key_parsed_despite_yaml_boolean_quirk(self):
        """YAML 1.1 turns `on:` into True. Workflows in the wild rely on it parsing as
        the trigger key."""
        wf = parse_workflow("ci.yml", CI)
        assert wf.on is not None

    def test_concurrency_shorthand_string(self):
        wf = parse_workflow("ci.yml", "concurrency: mygroup\njobs:\n  a:\n    steps: []\n")
        assert wf.concurrency == {"group": "mygroup"}
        assert wf.cancel_in_progress is False

    def test_empty_workflow_is_not_an_error(self):
        wf = parse_workflow("empty.yml", "name: nothing\non: push\n")
        assert wf.parse_error is None
        assert wf.jobs == {}
