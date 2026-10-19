"""Per-action caching inputs/defaults, from upstream source (CAVEATS 65).

Warm caches can have flat timings. These cases must stay silent even when the timing
gate would admit them; a shared truthy `cache:` check cannot establish their state.
"""

from __future__ import annotations

import pytest

from cadence.actions import ActionManifest
from cadence.cost import CostContext, RateCard
from cadence.detectors.cache import DependencyCacheDetector, cache_state, install_step
from cadence.detectors.context import AuditContext, RootPackageJson, StepSeries
from cadence.workflow import parse_workflow

PIN = "a" * 40


def _workflow(uses, inputs="", *, runner="ubuntu-latest", command="uv sync"):
    return parse_workflow(
        "ci.yml",
        f"on: push\njobs:\n  test:\n    runs-on: {runner}\n    steps:\n"
        f"      - uses: actions/checkout@v5\n      - uses: {uses}\n"
        + ("        with:\n" + inputs if inputs else "")
        + f"      - run: {command}\n",
    )


def _state(wf, actions=None):
    assert wf.parse_error is None
    job = wf.jobs["test"]
    return cache_state(job, install_step(job), RootPackageJson(), actions or {})


@pytest.mark.parametrize(("uses", "inputs", "expected"), [
    ("actions/setup-go@v4", "", "cached"),
    ("actions/setup-go@v5.0.2", "", "cached"),
    ("actions/setup-go@v6", "", "cached"),
    ("actions/setup-go@v3", "", "uncached"),
    ("actions/setup-go@v3", "          cache: true\n", "cached"),
    ("actions/setup-go@v4", "          cache: false\n", "disabled"),
    ("actions/setup-go@v4", "          cache: 'false'\n", "disabled"),
    ("actions/setup-go@v4", "          cache: ${{ inputs.cache }}\n", "unknown"),
    ("actions/setup-go@v2", "          cache: true\n", "uncached"),
    (f"actions/setup-go@{PIN}", "", "unknown"),
    ("actions/setup-go@main", "", "unknown"),
    (f"actions/setup-go@{PIN}", "          cache: true\n", "cached"),
    (f"actions/setup-go@{PIN}", "          cache: false\n", "disabled"),
    ("ruby/setup-ruby@v1", "", "uncached"),
    ("ruby/setup-ruby@v1", "          bundler-cache: true\n", "cached"),
    (f"ruby/setup-ruby@{PIN}", "          bundler-cache: 'true'\n", "cached"),
    ("ruby/setup-ruby@v1", "          bundler-cache: false\n", "disabled"),
    ("ruby/setup-ruby@v1", "          bundler-cache: 'false'\n", "disabled"),
    ("ruby/setup-ruby@v1", "          bundler-cache: ${{ inputs.cache }}\n", "unknown"),
    ("ruby/setup-ruby@v1", "          cache: true\n", "uncached"),
    ("astral-sh/setup-uv@v3", "", "uncached"),
    ("astral-sh/setup-uv@v4", "", "uncached"),
    ("astral-sh/setup-uv@v5.0.0", "", "cached"),
    ("astral-sh/setup-uv@v6", "", "cached"),
    ("astral-sh/setup-uv@v4", "          enable-cache: true\n", "cached"),
    ("astral-sh/setup-uv@v6", "          enable-cache: 'true'\n", "cached"),
    ("astral-sh/setup-uv@v6", "          enable-cache: false\n", "disabled"),
    ("astral-sh/setup-uv@v6", "          enable-cache: 'false'\n", "disabled"),
    ("astral-sh/setup-uv@v6", "          enable-cache: auto\n", "cached"),
    ("astral-sh/setup-uv@v6", "          enable-cache: ${{ inputs.cache }}\n", "unknown"),
    (f"astral-sh/setup-uv@{PIN}", "", "unknown"),
    (f"astral-sh/setup-uv@{PIN}", "          enable-cache: true\n", "cached"),
    (f"astral-sh/setup-uv@{PIN}", "          enable-cache: false\n", "disabled"),
    (f"astral-sh/setup-uv@{PIN}", "          enable-cache: auto\n", "unknown"),
    ("astral-sh/setup-uv@v6", "          restore-cache: false\n", "disabled"),
    ("astral-sh/setup-uv@v6", "          restore-cache: 'false'\n", "disabled"),
    ("astral-sh/setup-uv@v6.7.0", "          restore-cache: false\n", "disabled"),
    ("astral-sh/setup-uv@v6.0.0", "          restore-cache: false\n", "cached"),
    ("astral-sh/setup-uv@v6.6.0", "          restore-cache: false\n", "cached"),
    ("astral-sh/setup-uv@v6", "          restore-cache: ${{ inputs.restore }}\n", "unknown"),
    # v5 has no restore-cache input, so it cannot override enable-cache there.
    ("astral-sh/setup-uv@v5", "          restore-cache: false\n", "cached"),
    (f"astral-sh/setup-uv@{PIN}", "          restore-cache: false\n", "unknown"),
    ("astral-sh/setup-uv@v4", "          cache: true\n", "uncached"),
])
def test_setup_action_inputs_and_versioned_defaults(uses, inputs, expected):
    assert _state(_workflow(uses, inputs)) == expected


@pytest.mark.parametrize(("runner", "expected"), [
    ("ubuntu-24.04", "cached"),
    ("ubuntu-24.04-arm", "cached"),
    ("windows-latest", "cached"),
    ("macos-14", "cached"),
    ("macos-15-large", "cached"),
    ("self-hosted", "uncached"),
    ("[self-hosted, linux, x64]", "uncached"),
    ("${{ matrix.os }}", "unknown"),
    ("custom-runner", "unknown"),
    ("{group: build-runners}", "unknown"),
])
def test_uv_auto_depends_on_the_runner_environment(runner, expected):
    assert _state(_workflow("astral-sh/setup-uv@v6", runner=runner)) == expected


def test_explicit_uv_cache_can_enable_a_self_hosted_runner():
    assert _state(_workflow("astral-sh/setup-uv@v6", "          enable-cache: true\n",
                            runner="self-hosted")) == "cached"


def test_runner_context_reaches_a_setup_action_inside_a_composite():
    manifest = ActionManifest.from_text(
        "runs:\n  using: composite\n  steps:\n    - uses: astral-sh/setup-uv@v6\n"
    )
    for runner, expected in [("ubuntu-latest", "cached"), ("self-hosted", "uncached")]:
        wf = _workflow("./.github/actions/python", runner=runner)
        assert _state(wf, {"./.github/actions/python": manifest}) == expected


def test_an_independent_cache_still_wins_over_a_setup_opt_out():
    wf = _workflow("ruby/setup-ruby@v1", "          bundler-cache: false\n")
    job = wf.jobs["test"]
    cache_job = _workflow("actions/cache@v4").jobs["test"]
    job.steps.append(cache_job.steps[1])
    job.steps[-1].index = 3
    assert _state(wf) == "cached"


@pytest.mark.parametrize(("uses", "inputs", "command", "expected_findings"), [
    ("actions/setup-go@v4", "", "go mod download", 0),
    ("actions/setup-go@v3", "", "go mod download", 1),
    ("ruby/setup-ruby@v1", "          bundler-cache: true\n", "bundle install", 0),
    ("ruby/setup-ruby@v1", "          bundler-cache: false\n", "bundle install", 0),
    ("ruby/setup-ruby@v1", "", "bundle install", 1),
    ("astral-sh/setup-uv@v6", "", "uv sync", 0),
    ("astral-sh/setup-uv@v4", "          enable-cache: true\n", "uv sync", 0),
    ("astral-sh/setup-uv@v6", "          enable-cache: false\n", "uv sync", 0),
    (f"astral-sh/setup-uv@{PIN}", "", "uv sync", 0),
    ("astral-sh/setup-uv@v4", "", "uv sync", 1),
])
def test_warm_caches_and_opt_outs_never_become_findings_from_flat_timings(
    uses, inputs, command, expected_findings,
):
    wf = _workflow(uses, inputs, command=command)
    ctx = AuditContext(
        repo_id=1, owner="acme", name="widget", is_private=False, workflows=[wf], runs=[],
        step_series={},
        step_series_resolved={("ci.yml", "test", f"Run {command}"): StepSeries(
            "test", f"Run {command}", [90.0] * 20, list(range(20)))},
        cost=CostContext(is_private=False, runs_per_month=200.0,
                         rate_card=RateCard(version=2026, rates={}, free_on_public={})),
        window_days=90,
    )
    findings = DependencyCacheDetector().run(ctx)
    assert len(findings) == expected_findings
    assert all(f.kind == "no_dependency_cache" and f.detector_version == "dependency_cache@7"
               for f in findings)
