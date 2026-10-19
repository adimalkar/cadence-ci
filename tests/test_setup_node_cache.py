"""setup-node's automatic caching, mirrored from its source (CAVEATS 59).

Until dependency_cache@3 any job using `actions/setup-node` counted as cached. On the corpus
that silenced 84 install jobs, and 65 of them cache nothing: they pin pnpm or yarn, have no
root package.json, or turn the cache off. Each case below is one of those shapes.
"""

from __future__ import annotations

import asyncio

import pytest

from cadence.audit import fetch_root_package_json
from cadence.cost import CostContext, RateCard
from cadence.detectors.cache import (
    DependencyCacheDetector,
    needs_root_package_json,
    setup_node_auto_caches,
    setup_node_major,
)
from cadence.detectors.context import AuditContext, RootPackageJson, StepSeries
from cadence.workflow import parse_workflow

RATE_CARD = RateCard(version=2026, rates={"ubuntu-latest": 0.006},
                     free_on_public={"ubuntu-latest": True})

NPM = RootPackageJson.from_text('{"packageManager": "npm@11.19.1+sha512.abc"}')
PNPM = RootPackageJson.from_text('{"packageManager": "pnpm@10.34.2"}')
ABSENT = RootPackageJson.from_text(None)
UNFETCHED = RootPackageJson()


def _job(setup: str, *, checkout: str = "      - uses: actions/checkout@v5\n"):
    wf = parse_workflow(
        "ci.yml",
        "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        + checkout + setup + "      - run: npm ci\n",
    )
    job = wf.jobs["build"]
    step = next(s for s in job.steps if s.action == "actions/setup-node")
    return wf, job, step


SETUP_V7 = "      - uses: actions/setup-node@v7\n        with:\n          node-version: 24\n"


class TestPackageManagerField:
    @pytest.mark.parametrize("text", [
        '{"packageManager": "npm"}',
        '{"packageManager": "npm@11.0.0"}',
        '{"packageManager": "^npm@10"}',
        '{"devEngines": {"packageManager": {"name": "npm"}}}',
        '{"devEngines": {"packageManager": [{"name": "pnpm"}, {"name": "npm@11"}]}}',
    ])
    def test_npm_is_recognised_where_setup_node_looks(self, text):
        assert RootPackageJson.from_text(text).declares_npm()

    @pytest.mark.parametrize("text", [
        '{"packageManager": "pnpm@10.34.2"}',        # vite, remix, angular, vue
        '{"packageManager": "yarn@4.17.0+sha512.c"}', # babel
        '{"name": "eslint"}',                        # no field: eslint, vscode, rollup
        '{"packageManager": "npmx@1"}',
        "not json",
        "[]",
    ])
    def test_anything_else_is_not(self, text):
        assert not RootPackageJson.from_text(text).declares_npm()

    def test_absent_and_unfetched_are_different_states(self):
        assert ABSENT.fetched and ABSENT.data is None
        assert not UNFETCHED.fetched


class TestSetupNodeAutoCaches:
    def test_v5_plus_with_npm_declared_caches(self):
        """microsoft/TypeScript: `packageManager: npm@11…` at the root."""
        _, job, step = _job(SETUP_V7)
        assert setup_node_auto_caches(job, step, NPM)

    def test_pnpm_declared_does_not(self):
        _, job, step = _job(SETUP_V7)
        assert not setup_node_auto_caches(job, step, PNPM)

    def test_no_root_package_json_does_not(self):
        """astral-sh/ruff, nodejs/node: setup-node for a tool, no package.json."""
        _, job, step = _job(SETUP_V7)
        assert not setup_node_auto_caches(job, step, ABSENT)

    def test_unfetched_keeps_the_old_answer(self):
        _, job, step = _job(SETUP_V7)
        assert setup_node_auto_caches(job, step, UNFETCHED)

    def test_explicit_opt_out_wins(self):
        _, job, step = _job(SETUP_V7 + "          package-manager-cache: false\n")
        assert not setup_node_auto_caches(job, step, NPM)

    @pytest.mark.parametrize("ref", ["v4", "v4.4.0", "v3"])
    def test_before_v5_there_was_no_auto_cache(self, ref):
        _, job, step = _job(f"      - uses: actions/setup-node@{ref}\n")
        assert not setup_node_auto_caches(job, step, NPM)

    def test_a_sha_pin_is_decided_by_the_package_json(self):
        """Version unknown from a SHA; the manifest still decides, conservatively."""
        pin = "      - uses: actions/setup-node@8207627860262fc9b2ed8f8a54b35e4a6f5d4b1e\n"
        _, job, step = _job(pin)
        assert setup_node_major(step) is None
        assert setup_node_auto_caches(job, step, NPM)
        assert not setup_node_auto_caches(job, step, PNPM)

    def test_setup_before_checkout_reads_nothing(self):
        _, job, step = _job(SETUP_V7, checkout="")
        assert not setup_node_auto_caches(job, step, NPM)

    def test_checkout_into_a_subdirectory_is_not_our_root(self):
        co = "      - uses: actions/checkout@v5\n        with:\n          path: src\n"
        _, job, step = _job(SETUP_V7, checkout=co)
        assert setup_node_auto_caches(job, step, PNPM)  # unknown -> conservative

    def test_explicit_cache_input_always_caches(self):
        _, job, step = _job(SETUP_V7 + "          cache: pnpm\n")
        assert setup_node_auto_caches(job, step, ABSENT)


def _ctx(wf, package_json: RootPackageJson, durations: list[float]) -> AuditContext:
    return AuditContext(
        repo_id=1, owner="acme", name="widget", is_private=False, workflows=[wf], runs=[],
        step_series={("build", "npm ci"): StepSeries("build", "npm ci", durations,
                                                       list(range(len(durations))))},
        cost=CostContext(is_private=False, runs_per_month=200.0, rate_card=RATE_CARD),
        window_days=90, root_package_json=package_json,
    )


class TestTheDetectorNowLooks:
    FLAT = [90.0] * 20

    def test_a_pnpm_repo_with_a_flat_install_is_a_finding(self):
        wf, _, _ = _job(SETUP_V7)
        found = [d for d in DependencyCacheDetector().run(_ctx(wf, PNPM, self.FLAT))
                 if d.kind == "no_dependency_cache"]
        assert len(found) == 1

    def test_an_npm_repo_is_still_cached(self):
        wf, _, _ = _job(SETUP_V7)
        assert [d for d in DependencyCacheDetector().run(_ctx(wf, NPM, self.FLAT))
                if d.kind == "no_dependency_cache"] == []

    def test_unfetched_behaves_exactly_as_before(self):
        wf, _, _ = _job(SETUP_V7)
        assert [d for d in DependencyCacheDetector().run(_ctx(wf, UNFETCHED, self.FLAT))
                if d.kind == "no_dependency_cache"] == []

    def test_the_timing_gate_still_applies(self):
        """Uncached by config but bimodal in practice: something restores. No finding."""
        wf, _, _ = _job(SETUP_V7)
        assert [d for d in DependencyCacheDetector().run(_ctx(wf, PNPM, [90.0, 5.0] * 10))
                if d.kind == "no_dependency_cache"] == []


class _Provider:
    def __init__(self, result=None, exc: Exception | None = None):
        self.result, self.exc, self.calls = result, exc, 0

    async def fetch_text_file(self, repo, path):
        self.calls += 1
        assert path == "package.json"
        if self.exc:
            raise self.exc
        return self.result


def _files(setup: str) -> dict[str, str]:
    return {"ci.yml": "on: push\njobs:\n  b:\n    runs-on: x\n    steps:\n"
                      "      - uses: actions/checkout@v5\n" + setup}


class TestFetchIsLazyAndBestEffort:
    def test_no_request_when_nothing_depends_on_it(self):
        p = _Provider('{"packageManager": "npm"}')
        files = _files("      - uses: actions/setup-node@v7\n        with:\n          cache: npm\n")
        assert not needs_root_package_json([parse_workflow(k, v) for k, v in files.items()])
        assert asyncio.run(fetch_root_package_json(p, None, files)) == UNFETCHED
        assert p.calls == 0

    def test_one_request_when_it_matters(self):
        p = _Provider('{"packageManager": "pnpm@10"}')
        got = asyncio.run(fetch_root_package_json(p, None, _files(SETUP_V7)))
        assert got.fetched and not got.declares_npm()
        assert p.calls == 1

    def test_missing_file_is_absent(self):
        got = asyncio.run(fetch_root_package_json(_Provider(None), None, _files(SETUP_V7)))
        assert got == ABSENT

    def test_a_failed_request_is_unfetched_not_absent(self):
        """Absent would make every setup-node job a candidate on a network blip."""
        p = _Provider(exc=RuntimeError("502"))
        got = asyncio.run(fetch_root_package_json(p, None, _files(SETUP_V7)))
        assert got == UNFETCHED
