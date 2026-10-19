"""What `no_dependency_cache` may price, and from which timings (CAVEATS 61).

Before dependency_cache@4, about 18 of 50 corpus findings priced work that was not a
dependency install: `apt-get install` counted as one, a mixed step's whole duration was
projected, and a timing miss fell back to the job's longest step. Each case below is a
shape that produced one of those.
"""

from __future__ import annotations

import pytest

from cadence.audit import index_step_rows
from cadence.cost import CostContext, RateCard
from cadence.detectors.cache import DependencyCacheDetector, _install_step, install_only
from cadence.detectors.context import AuditContext, StepSeries, step_runtime_name
from cadence.workflow import parse_workflow

RATE_CARD = RateCard(version=2026, rates={"ubuntu-latest": 0.006},
                     free_on_public={"ubuntu-latest": True})


class TestInstallOnly:
    @pytest.mark.parametrize("script", [
        "npm ci",
        "pnpm install --frozen-lockfile",
        "cd web\nyarn install",
        "python -m pip install --upgrade pip\npip install -r requirements.txt",
        "pip uninstall -y pytest-xdist\npip install -e .",   # numpy
        "python --version\npip install -r req.txt",           # numpy
        "pip install \\\n  -r a.txt \\\n  -r b.txt",
        "uv sync --locked --no-dev",
        "corepack enable && pnpm install",
    ])
    def test_an_install_with_bookkeeping_is_an_install(self, script):
        assert install_only(script)

    @pytest.mark.parametrize("script", [
        "sudo apt-get install tcl8.6 tclx\n./runtest --verbose",   # redis: priced 594-705 s
        "sudo apt-get update && sudo apt-get install libc6-dev-i386\nmake 32bit",  # redis
        "sudo apt-get install -y libssl-dev",                    # apt alone is not one either
        "npm ci && npm run build",
        "pip install pre-commit\npre-commit run --all-files",    # requests
        "npm install -g npm@latest",                             # self-update only
        "python -m pip install --upgrade pip setuptools wheel",
        "cargo build --release",
    ])
    def test_anything_heavier_or_not_an_install_is_not(self, script):
        assert not install_only(script)


class TestStepRuntimeName:
    def _step(self, body: str):
        wf = parse_workflow("ci.yml", "on: push\njobs:\n  b:\n    runs-on: x\n    steps:\n" + body)
        return wf.jobs["b"].steps[0]

    def test_named_step_is_its_name(self):
        s = self._step("      - name: Install\n        run: npm ci\n")
        assert step_runtime_name(s) == "Install"

    def test_unnamed_run_step_is_run_plus_first_line(self):
        s = self._step("      - run: |\n          npm ci\n          echo done\n")
        assert step_runtime_name(s) == "Run npm ci"

    def test_unnamed_uses_step(self):
        assert step_runtime_name(self._step("      - uses: actions/checkout@v5\n")) \
            == "Run actions/checkout@v5"

    def test_an_expression_name_cannot_be_predicted(self):
        s = self._step("      - name: Install ${{ matrix.pm }}\n        run: npm ci\n")
        assert step_runtime_name(s) is None


def _job(steps: str):
    return parse_workflow(
        "ci.yml", "on: push\njobs:\n  build:\n    runs-on: x\n    steps:\n" + steps
    ).jobs["build"]


class TestInstallStepChoice:
    def test_a_mixed_step_is_skipped_for_a_later_pure_one(self):
        job = _job("      - run: sudo apt-get install -y tcl\n"
                   "      - run: npm ci && npm test\n"
                   "      - run: npm ci\n")
        assert _install_step(job).index == 2

    def test_no_pure_install_means_no_candidate(self):
        assert _install_step(_job("      - run: sudo apt-get install tcl\n        \n"
                                  "      - run: ./runtest\n")) is None


ROW = dict(name_base="Build", job_name="Build", workflow_path="ci.yml",
           step_name="Run npm ci", run_id=1, dur=90.0)
NAMED_JOB = ("on: push\njobs:\n  build:\n    name: Build\n    runs-on: ubuntu-latest\n"
             "    steps:\n      - uses: actions/checkout@v5\n      - run: npm ci\n")


class TestIndexStepRows:
    def test_a_named_job_is_found_under_its_key(self):
        """Keyed by display name, `name: Build` never matched the job key `build`."""
        _, resolved = index_step_rows([ROW], [parse_workflow("ci.yml", NAMED_JOB)])
        assert ("ci.yml", "build", "Run npm ci") in resolved

    def test_two_workflows_with_the_same_job_name_stay_apart(self):
        other = parse_workflow("release.yml", NAMED_JOB)
        rows = [ROW, {**ROW, "workflow_path": "release.yml", "dur": 400.0, "run_id": 2}]
        by_name, resolved = index_step_rows(rows, [parse_workflow("ci.yml", NAMED_JOB), other])
        assert by_name[("Build", "Run npm ci")].durations == [90.0, 400.0]  # merged, as before
        assert resolved[("ci.yml", "build", "Run npm ci")].durations == [90.0]
        assert resolved[("release.yml", "build", "Run npm ci")].durations == [400.0]

    def test_an_unresolvable_job_is_left_out_not_guessed(self):
        row = {**ROW, "name_base": "Deploy", "job_name": "Deploy"}
        _, resolved = index_step_rows([row], [parse_workflow("ci.yml", NAMED_JOB)])
        assert resolved == {}


class TestNoFallbackToAnotherStep:
    def _ctx(self, resolved):
        return AuditContext(
            repo_id=1, owner="a", name="b", is_private=False,
            workflows=[parse_workflow("ci.yml", NAMED_JOB)], runs=[], step_series={},
            step_series_resolved=resolved,
            cost=CostContext(is_private=False, runs_per_month=200.0, rate_card=RATE_CARD),
            window_days=90,
        )

    def test_the_install_steps_own_flat_series_is_a_finding(self):
        s = StepSeries("build", "Run npm ci", [90.0] * 20, list(range(20)))
        found = [d for d in DependencyCacheDetector().run(
            self._ctx({("ci.yml", "build", "Run npm ci"): s})) if d.kind == "no_dependency_cache"]
        assert len(found) == 1
        assert found[0].evidence[1].payload["step"] == "Run npm ci"

    def test_another_steps_series_is_never_used(self):
        """fastapi's `uv sync` was priced from `Upload coverage to Smokeshow`."""
        s = StepSeries("build", "Run ./test.sh", [600.0] * 20, list(range(20)))
        assert [d for d in DependencyCacheDetector().run(
            self._ctx({("ci.yml", "build", "Run ./test.sh"): s}))
            if d.kind == "no_dependency_cache"] == []
