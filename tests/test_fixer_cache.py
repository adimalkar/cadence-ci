"""A cache preview must be a safe edit, not merely YAML containing `cache:`."""

from __future__ import annotations

from dataclasses import replace
from difflib import SequenceMatcher

import pytest

from cadence.actions import ActionManifest
from cadence.cost import CostContext, RateCard
from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.cache import DETECTOR_VERSION, DependencyCacheDetector, install_step
from cadence.detectors.context import AuditContext, RootPackageJson, StepSeries, step_runtime_name
from cadence.fixers.cache import CacheFixer
from cadence.preview import preview_cache_fixes
from cadence.simulate import project_cache_savings
from cadence.source import FileInventory, PreviewSource
from cadence.workflow import parse_workflow

PATH = ".github/workflows/ci.yml"
SHA = "a" * 40
CI = """\
# Keep this header and every original byte.
name: CI
on: [push, pull_request]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v5 # keep the pin comment
        with:
          # This describes the version, not the cache.
          python-version: '3.12'
      - run: pip install -r requirements/test.txt
"""
FILES = frozenset({PATH, "requirements/test.txt", "pyproject.toml", "package.json",
                   "package-lock.json"})


def source(content=CI, *, files=FILES):
    return PreviewSource(
        "acme/widget", FileInventory(SHA, files, complete=True), {PATH: content},
        RootPackageJson.from_text('{"name":"widget"}'),
        {use: ActionManifest("node20", inputs=frozenset({"cache", "cache-dependency-path"}))
         for use in ["actions/setup-python@v5", "actions/setup-node@v4"]},
    )


def finding(content=CI):
    job = parse_workflow(PATH, content).jobs.get("test")
    install = install_step(job) if job else None
    return FindingDraft(
        "no_dependency_cache", "waste", 3, 0.75, f"no_dependency_cache:{PATH}:test", "cold",
        DETECTOR_VERSION,
        [EvidenceDraft("code_range", PATH, install.line if install else 1,
                       payload={"job": "test"})],
        savings=project_cache_savings([90.0] * 20),
    )


def fix(content=CI, *, src=None, draft=None):
    return CacheFixer(src or source(content)).preview(
        parse_workflow(PATH, content), content, draft or finding(content),
    )


def context(src):
    wf = parse_workflow(PATH, src.workflows[PATH])
    install = install_step(wf.jobs["test"])
    return AuditContext(
        repo_id=1, owner="acme", name="widget", is_private=False, workflows=[wf], runs=[],
        step_series={},
        step_series_resolved={(PATH, "test", step_runtime_name(install)): StepSeries(
            "test", step_runtime_name(install), [90.0] * 20, list(range(20)))},
        cost=CostContext(False, 100, RateCard(2026, {}, {})), window_days=90,
        root_package_json=src.package_json, actions=src.actions,
    )


class TestAcceptedEdits:
    def test_pip_uses_the_requirements_file_the_command_installs(self):
        result = fix()
        assert result.ok, result.declined
        setup = parse_workflow(PATH, result.diff.after).jobs["test"].steps[1]
        assert setup.with_ == {"python-version": "3.12", "cache": "pip",
                               "cache-dependency-path": "requirements/test.txt"}
        assert result.diff.changed_lines == 2
        assert result.diff.revert_hint

    @pytest.mark.parametrize("ref", ["v4", SHA])
    def test_npm_lockfile_is_explicit(self, ref):
        text = CI.replace("actions/setup-python@v5", f"actions/setup-node@{ref}").replace(
            "pip install -r requirements/test.txt", "npm ci")
        src = source(text)
        src.actions[f"actions/setup-node@{ref}"] = src.actions["actions/setup-node@v4"]
        result = fix(text, src=src)
        assert result.ok, result.declined
        assert "cache: npm" in result.diff.after
        assert "cache-dependency-path: 'package-lock.json'" in result.diff.after

    @pytest.mark.parametrize("command, expected", [
        ("pip install --group dev", "pyproject.toml"),
        ("python -m pip install --group=dev --group=test", "pyproject.toml"),
        ("python -m pip install -e '.[test]'", "pyproject.toml"),
        ("pip install --requirement=requirements/test.txt", "requirements/test.txt"),
        ("pip install -rrequirements/test.txt", "requirements/test.txt"),
        ("python -m pip install -U pip\n          pip install -r requirements/test.txt",
         "requirements/test.txt"),
    ])
    def test_supported_pip_commands(self, command, expected):
        # A block script carries the multi-line command without changing its semantics.
        text = CI.replace("run: pip install -r requirements/test.txt",
                          "run: |\n          " + command)
        result = fix(text)
        assert result.ok, result.declined
        assert f"cache-dependency-path: '{expected}'" in result.diff.after

    def test_multiple_dependency_files_are_all_keyed(self):
        text = CI.replace("pip install -r requirements/test.txt",
                          "pip install -r requirements/test.txt -c constraints.txt")
        src = source(text, files=FILES | {"constraints.txt"})
        result = fix(text, src=src)
        assert result.ok, result.declined
        inputs = parse_workflow(PATH, result.diff.after).jobs["test"].steps[1].with_
        assert inputs["cache-dependency-path"] == "constraints.txt\nrequirements/test.txt"

    @pytest.mark.parametrize("variant", ["comments", "crlf", "no-final-newline", "four-spaces",
                                         "no-with", "quoted-key", "other-flow-mapping"])
    def test_every_original_byte_survives(self, variant):
        text = CI
        if variant == "crlf":
            text = text.replace("\n", "\r\n")
        elif variant == "no-final-newline":
            text = text.rstrip("\n")
        elif variant == "four-spaces":
            # A sequence's '- ' stays two columns wide when mapping indents double.
            converted = []
            for line in text.split("\n"):
                width = len(line) - len(line.lstrip())
                converted.append(" " * (2 * width - (2 if width >= 8 else 0)) + line.lstrip())
            text = "\n".join(converted)
        elif variant == "no-with":
            text = text.replace("        with:\n"
                                "          # This describes the version, not the cache.\n"
                                "          python-version: '3.12'\n", "")
        elif variant == "quoted-key":
            text = text.replace("  test:", "  'test':")
        elif variant == "other-flow-mapping":
            text = text.replace("    runs-on:", "    env: { FOO: bar }\n    runs-on:")
        result = fix(text)
        assert result.ok, result.declined
        operations = SequenceMatcher(None, text.splitlines(keepends=True),
                                     result.diff.after.splitlines(keepends=True)).get_opcodes()
        assert all(op[0] in {"equal", "insert"} for op in operations)
        assert result.diff.after.endswith("\n") == text.endswith("\n")

    def test_the_real_detector_stops_firing_after_a_fix(self):
        src = source()
        ctx = context(src)
        drafts = DependencyCacheDetector().run(ctx)
        assert len(drafts) == 1
        result = CacheFixer(src).preview(ctx.workflows[0], CI, drafts[0])
        assert result.ok, result.declined
        ctx.workflows = [parse_workflow(PATH, result.diff.after)]
        assert DependencyCacheDetector().run(ctx) == []
        assert not fix(result.diff.after).ok  # no repeated proposal


class TestDeclines:
    @pytest.mark.parametrize("command", [
        "pip install --no-cache-dir -r requirements/test.txt",
        "pip install --cache-dir /tmp/pip -r requirements/test.txt",
        "pip install -r $REQ", "pip install -r ../requirements.txt",
        "pip install -r /requirements.txt", "pip install -r missing.txt",
        "pip install pytest", "yarn install", "pnpm install", "npm install -g eslint",
        "pip install --group other/pyproject.toml:dev",
    ])
    def test_unknown_install_shapes_and_missing_files(self, command):
        text = CI.replace("pip install -r requirements/test.txt", command)
        result = fix(text)
        assert not result.ok and result.declined

    @pytest.mark.parametrize("replacement", [
        ("name: CI", "name: Publish"),
        ("on: [push, pull_request]", "on: pull_request_target"),
        ("on: [push, pull_request]", "on: workflow_call"),
        ("contents: read", "contents: write"),
        ("    runs-on:", "    environment: production\n    runs-on:"),
        ("    runs-on: ubuntu-latest", "    runs-on: self-hosted"),
        ("jobs:", "defaults:\n  run:\n    working-directory: app\njobs:"),
        ("      - run:", "      - working-directory: app\n        run:"),
        ("      - run:", "      - if: matrix.enabled\n        run:"),
        ("        with:\n", "        if: matrix.enabled\n        with:\n"),
        ("      - uses: actions/checkout@v5\n",
         "      - uses: actions/checkout@v5\n        with:\n          path: src\n"),
        ("      - uses: actions/checkout@v5\n",
         "      - uses: actions/checkout@v5\n        with:\n          ref: other\n"),
        ("      - run:", "      - run: echo x > requirements/test.txt\n      - run:"),
        ("        with:\n          # This describes the version, not the cache.\n"
         "          python-version: '3.12'", "        with: {python-version: '3.12'}"),
        ("      - uses: actions/setup-python",
         "      - &setup\n        uses: actions/setup-python"),
        ("    runs-on:", "    env:\n      PIP_NO_CACHE_DIR: '1'\n    runs-on:"),
    ])
    def test_unsafe_or_ambiguous_context(self, replacement):
        text = CI.replace(*replacement)
        result = fix(text)
        assert not result.ok and result.declined

    def test_npm_without_lockfile_never_gets_a_build_breaking_cache(self):
        text = CI.replace("actions/setup-python@v5", "actions/setup-node@v4").replace(
            "pip install -r requirements/test.txt", "npm install")
        result = fix(text, src=source(text, files=FILES - {"package-lock.json"}))
        assert "lockfile" in result.declined

    def test_incomplete_inventory_is_not_an_absent_lockfile(self):
        src = source()
        src.inventory = replace(src.inventory, complete=False)
        assert "commit-pinned" in fix(src=src).declined

    def test_branch_ref_is_not_an_immutable_source(self):
        src = source()
        src.inventory = replace(src.inventory, ref="main")
        assert not fix(src=src).ok

    def test_mismatched_yaml_and_inventory_are_refused(self):
        assert "snapshot" in fix(CI + "# moved\n", src=source()).declined

    def test_an_unread_setup_manifest_is_not_assumed_to_support_the_inputs(self):
        src = source()
        src.actions = {}
        assert "verified" in fix(src=src).declined

    def test_a_setup_without_cache_dependency_path_is_not_supported(self):
        src = source()
        src.actions["actions/setup-python@v5"] = ActionManifest(
            "node20", inputs=frozenset({"cache"}),
        )
        assert not fix(src=src).ok

    def test_malformed_setup_metadata_cannot_establish_input_support(self):
        src = source()
        src.actions["actions/setup-python@v5"] = ActionManifest(
            None, inputs=frozenset({"cache", "cache-dependency-path"}),
        )
        assert not fix(src=src).ok

    def test_an_aliased_job_cannot_be_changed_through_its_anchor(self):
        text = CI.replace("  test:\n", "  test: &job\n") + "  other: *job\n"
        result = fix(text)
        assert not result.ok and "semantics" in result.declined

    @pytest.mark.parametrize("low", [29.99, float("nan"), float("inf")])
    def test_small_or_invalid_savings_do_not_generate_diffs(self, low):
        draft = finding()
        draft.savings.low = low
        assert not fix(draft=draft).ok

    def test_suppression_in_the_repository_is_honoured(self):
        src = source()
        src.ignore_file = "no_dependency_cache — container already caches downloads\n"
        assert "suppressed" in fix(src=src).declined

    def test_inline_suppression_is_honoured(self):
        text = "# cadence:ignore no_dependency_cache — intentional\n" + CI
        assert "suppressed" in fix(text).declined

    def test_stale_evidence_and_detector_version_require_a_new_audit(self):
        draft = finding()
        draft.evidence[0].line_start = 1
        assert "evidence" in fix(draft=draft).declined
        draft = finding()
        draft.detector_version = "dependency_cache@5"
        assert "re-audit" in fix(draft=draft).declined


@pytest.mark.parametrize("scope, key, expected", [
    ("finding", f"no_dependency_cache:{PATH}:test", False),
    ("finding", f"no_dependency_cache:{PATH}:another", True),
    ("rule_path", f"no_dependency_cache:{PATH}:another", False),
    ("rule_repo", "no_dependency_cache:other.yml:another", False),
])
def test_stored_suppressions_keep_their_original_scope(scope, key, expected):
    src = source()
    previews = preview_cache_fixes(context(src), src, suppressed=[{
        "kind": "no_dependency_cache", "dedupe_key": key, "suppress_scope": scope,
        "suppressed_reason": "do not propose this",
    }])
    assert len(previews) == 1 and previews[0].result.ok is expected
