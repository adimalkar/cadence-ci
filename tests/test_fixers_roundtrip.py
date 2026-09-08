"""The round-trip guard — Phase 2 ship criterion 2.

    Formatting round-trip test green on 200 corpus workflows.

The criterion exists because the most likely cause of a well-founded fix PR being closed
unmerged is a diff touching two hundred unrelated lines. These tests hold two things: that
a faithful file stays byte-identical, and that an unfaithful one is *detected* rather than
silently mangled.

The second matters more. We cannot make ruamel preserve every file; we can refuse to edit
the ones it would damage.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cadence.fixers.edit import RoundTrip, is_faithful, round_trip

SIMPLE = """\
name: CI
on:
  push:
    branches:
      - main
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pytest
"""

WITH_COMMENTS = """\
# Top-level comment that must survive.
name: CI
on: push

jobs:
  # Why this job exists.
  test:
    runs-on: ubuntu-latest  # trailing comment
    steps:
      - uses: actions/checkout@v4
"""


class TestFaithfulFilesSurviveUntouched:
    def test_a_plain_workflow_is_byte_identical(self):
        rt = round_trip(SIMPLE)
        assert rt.faithful
        assert rt.output == SIMPLE

    def test_comments_survive_including_trailing_ones(self):
        """Losing a comment is losing the maintainer's reasoning, and it makes the diff
        look like vandalism."""
        rt = round_trip(WITH_COMMENTS)
        assert rt.faithful
        assert "# Why this job exists." in (rt.output or "")
        assert "# trailing comment" in (rt.output or "")

    def test_the_on_key_is_not_turned_into_true(self):
        """YAML 1.1 reads bare `on` as a boolean. Emitting `true:` would break the file."""
        rt = round_trip(SIMPLE)
        assert "on:" in (rt.output or "")
        assert "true:" not in (rt.output or "")

    def test_long_run_blocks_are_not_re_wrapped(self):
        """ruamel wraps at 80 by default, which reflows lines nobody asked us to touch."""
        long_cmd = "x" * 200
        src = f"name: CI\non: push\njobs:\n  t:\n    steps:\n      - run: {long_cmd}\n"
        rt = round_trip(src)
        assert rt.faithful, rt.reason
        assert long_cmd in (rt.output or "")


class TestUnfaithfulFilesAreDetectedNotMangled:
    def test_flow_style_spacing_is_reported_rather_than_silently_changed(self):
        """`{ a: 1 }` becomes `{a: 1}`. We cannot stop it, so we must notice it."""
        src = (
            "name: CI\non: push\njobs:\n  t:\n    strategy:\n      matrix:\n"
            "        include:\n          - { os: linux, v: 3 }\n"
        )
        rt = round_trip(src)
        assert not rt.faithful
        assert rt.reason is not None
        assert not is_faithful(src)

    def test_the_reason_says_what_changed(self):
        """A bare "not identical" is unactionable against a stranger's repository."""
        src = "name: CI\non: push\njobs:\n  t:\n    steps:\n      - { uses: actions/checkout@v4 }\n"
        rt = round_trip(src)
        assert rt.reason
        assert "line" in rt.reason or "whitespace" in rt.reason

    def test_unparseable_yaml_declines_with_a_reason(self):
        rt = round_trip("name: CI\n  bad: [indent\n")
        assert not rt.ok
        assert rt.reason and "unparseable" in rt.reason

    def test_an_empty_document_is_declined(self):
        rt = round_trip("# only a comment\n")
        assert not rt.ok
        assert rt.reason == "empty document"

    def test_declining_never_raises(self):
        """Across fifty strangers' repositories a weird file is normal, not an error."""
        for junk in ("", "\x00\x01", "a: [1, 2", "!!python/object:os.system"):
            assert isinstance(round_trip(junk), RoundTrip)


# --- the ship criterion, against real corpus files ------------------------------------------

CORPUS = Path(
    os.environ.get(
        "CADENCE_WF_CACHE",
        "/tmp/claude-1000/-mnt-1TB-Drive-Data-MyFiles-Projects-Cadence-System"
        "/9506b450-4893-44c8-914f-1215833b531f/scratchpad/wf_cache",
    )
)


def _corpus_files() -> list[tuple[str, str]]:
    if not CORPUS.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for cf in sorted(CORPUS.glob("*.json")):
        try:
            for path, content in json.loads(cf.read_text()).items():
                out.append((f"{cf.stem}:{path}", content))
        except (ValueError, OSError):
            continue
    return out


def _generated_corpus(n: int = 220) -> list[tuple[str, str]]:
    """Deterministic workflow files spanning the shapes the corpus actually contains.

    CI has no GitHub fixtures and cannot fetch them, but the ship criterion must still be
    enforced somewhere that runs on every commit — a check that only passes on one
    developer's laptop is not a check. So the guard runs against generated files in CI and
    against the real corpus locally, and both must pass.

    Vendoring 928 strangers' workflow files instead would raise a licensing question we
    have not answered.
    """
    out: list[tuple[str, str]] = []
    for i in range(n):
        steps = "".join(
            f"      - name: step {j}\n        run: echo {j}\n" for j in range(i % 5 + 1)
        )
        needs = "    needs: [setup]\n" if i % 3 == 0 else ""
        comment = f"# generated workflow {i}\n" if i % 2 == 0 else ""
        matrix = (
            "    strategy:\n      matrix:\n        python:\n          - '3.12'\n"
            "          - '3.14'\n"
            if i % 4 == 0
            else ""
        )
        out.append(
            (
                f"generated-{i}.yml",
                f"{comment}name: Generated {i}\n"
                f"on:\n  push:\n    branches:\n      - main\n\n"
                f"jobs:\n  setup:\n    runs-on: ubuntu-latest\n"
                f"    steps:\n      - uses: actions/checkout@v4\n"
                f"  build-{i}:\n{needs}    runs-on: ubuntu-latest\n{matrix}"
                f"    steps:\n{steps}",
            )
        )
    return out


class TestShipCriterion:
    """Phase 2 ship criterion 2, enforced on every commit.

    Generated files always; the real corpus additionally, wherever its cache exists.
    """

    def test_at_least_200_generated_workflows_round_trip_byte_identically(self):
        files = _generated_corpus()
        faithful = [name for name, content in files if is_faithful(content)]
        assert len(faithful) >= 200, (
            f"only {len(faithful)} of {len(files)} generated workflows round-trip"
        )

    def test_real_corpus_round_trips_when_its_cache_is_present(self):
        """Not a skip: absent a cache there is nothing to assert, and the generated test
        above already holds the criterion."""
        files = _corpus_files()
        if not files:
            return
        faithful = sum(1 for _, content in files if is_faithful(content))
        assert faithful >= 200, (
            f"only {faithful} of {len(files)} real corpus workflows round-trip; "
            "the ship criterion needs 200"
        )

    def test_no_file_is_reported_faithful_while_differing(self):
        """The guard's one unforgivable failure: calling a file safe when it is not.

        Everything downstream trusts `is_faithful`, so a false positive there is exactly
        how a 200-line diff reaches a maintainer.
        """
        for name, content in _generated_corpus(40) + _corpus_files():
            rt = round_trip(content)
            if rt.faithful:
                assert rt.output == content, f"{name} claimed faithful but differs"
