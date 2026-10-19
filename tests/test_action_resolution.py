"""Reading through the actions a job runs before its install (CAVEATS 64).

28 of 50 `no_dependency_cache` findings were jobs cached inside a composite action the
detector could not see: remix restores its pnpm store in `./.github/actions/...`, angular in
`angular/dev-infra/...@<sha>`. Each shape here is one of those, or one of the guards.
"""

from __future__ import annotations

import asyncio

import pytest

from cadence.actions import ActionManifest, parse_ref, resolve_actions
from cadence.audit import fetch_action_index
from cadence.detectors.cache import KNOWN_ACTIONS, cache_state, install_step
from cadence.detectors.context import RootPackageJson
from cadence.evalsweep import _DiskCachedFiles
from cadence.models import Repo
from cadence.workflow import parse_workflow

REPO = Repo(id=1, owner="acme", name="widget", is_private=False, default_branch="main")
UNFETCHED = RootPackageJson()

# remix-run/remix .github/actions/setup-node-pnpm-cache/action.yaml, abridged
REMIX_COMPOSITE = """\
name: Setup Node.js with best-effort pnpm cache
runs:
  using: composite
  steps:
    - uses: actions/setup-node@v6
      with:
        node-version-file: 'package.json'
        package-manager-cache: false
    - id: pnpm-store
      shell: bash
      run: echo "path=$(pnpm store path --silent)" >> "$GITHUB_OUTPUT"
    - uses: actions/cache@v5
      with:
        path: ${{ steps.pnpm-store.outputs.path }}
        key: pnpm-store-${{ runner.os }}-${{ hashFiles('pnpm-lock.yaml') }}
"""
# angular/dev-infra github-actions/npm/checkout-and-setup-node/action.yml, abridged
ANGULAR_COMPOSITE = """\
runs:
  using: composite
  steps:
    - uses: actions/checkout@v5
    - uses: pnpm/action-setup@v6
      with:
        run_install: false
        cache: ${{ inputs.disable-package-manager-cache != 'true' }}
    - uses: actions/setup-node@v7
      with:
        package-manager-cache: false
"""
JS_ACTION = "runs:\n  using: node20\n  main: dist/index.js\n"


def _job(steps: str):
    wf = parse_workflow(
        "ci.yml", "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n" + steps
    )
    job = wf.jobs["build"]
    return wf, job, install_step(job)


def _index(**manifests: str | None) -> dict:
    return {u: (ActionManifest.from_text(t) if t is not None else None)
            for u, t in manifests.items()}


class TestParseRef:
    @pytest.mark.parametrize(("uses", "owner", "repo", "path", "ref"), [
        ("./.github/actions/setup", None, None, ".github/actions/setup", None),
        ("pnpm/action-setup@v6", "pnpm", "action-setup", "", "v6"),
        ("angular/dev-infra/github-actions/npm/checkout-and-setup-node@1ffbb25",
         "angular", "dev-infra", "github-actions/npm/checkout-and-setup-node", "1ffbb25"),
    ])
    def test_local_and_remote(self, uses, owner, repo, path, ref):
        r = parse_ref(uses)
        assert (r.owner, r.repo, r.path, r.ref) == (owner, repo, path, ref)

    @pytest.mark.parametrize("uses", ["docker://alpine:3", "not-an-action", "", None])
    def test_unreadable_refs(self, uses):
        assert parse_ref(uses) is None


class TestManifest:
    def test_composite_steps_are_parsed(self):
        m = ActionManifest.from_text(REMIX_COMPOSITE)
        assert m.is_composite
        assert [s.action for s in m.steps if s.action] == ["actions/setup-node", "actions/cache"]

    def test_js_action_has_no_steps(self):
        m = ActionManifest.from_text(JS_ACTION)
        assert m.using == "node20" and not m.is_composite and m.steps == []

    def test_garbage_is_unusable_not_an_error(self):
        assert ActionManifest.from_text(": : :\n\t-").using is None


LOCAL = "./.github/actions/setup-node-pnpm-cache"
REMOTE = "angular/dev-infra/github-actions/npm/checkout-and-setup-node@1ffbb25"
BEFORE = "      - uses: actions/checkout@v5\n"
INSTALL = "      - run: pnpm install --frozen-lockfile\n"


class TestCacheState:
    def test_remix_local_composite_with_actions_cache_is_cached(self):
        _, job, inst = _job(BEFORE + f"      - uses: {LOCAL}\n" + INSTALL)
        assert cache_state(job, inst, UNFETCHED, _index(**{LOCAL: REMIX_COMPOSITE})) == "cached"

    def test_angular_remote_composite_with_pnpm_cache_is_cached(self):
        _, job, inst = _job(f"      - uses: {REMOTE}\n" + INSTALL)
        assert cache_state(job, inst, UNFETCHED, _index(**{REMOTE: ANGULAR_COMPOSITE})) == "cached"

    def test_an_action_not_looked_up_is_unknown(self):
        _, job, inst = _job(BEFORE + f"      - uses: {LOCAL}\n" + INSTALL)
        assert cache_state(job, inst, UNFETCHED, {}) == "unknown"

    def test_an_action_that_does_not_exist_is_unknown(self):
        _, job, inst = _job(BEFORE + f"      - uses: {LOCAL}\n" + INSTALL)
        assert cache_state(job, inst, UNFETCHED, _index(**{LOCAL: None})) == "unknown"

    def test_a_read_javascript_action_does_nothing_we_measure(self):
        """deadsnakes/action, jetli/wasm-bindgen-action: code, not setup we model."""
        _, job, inst = _job(BEFORE + "      - uses: deadsnakes/action@v3\n" + INSTALL)
        assert cache_state(job, inst, UNFETCHED,
                           _index(**{"deadsnakes/action@v3": JS_ACTION})) == "uncached"

    def test_explicit_opt_out_is_disabled_not_uncached(self):
        """ruff and vite, in publish workflows."""
        _, job, inst = _job(BEFORE + "      - uses: actions/setup-node@v7\n        with:\n"
                            "          package-manager-cache: false\n" + INSTALL)
        assert cache_state(job, inst, UNFETCHED, {}) == "disabled"

    def test_an_unreadable_action_after_the_install_does_not_matter(self):
        _, job, inst = _job(BEFORE + INSTALL + f"      - uses: {LOCAL}\n")
        assert cache_state(job, inst, UNFETCHED, {}) == "uncached"

    def test_a_cache_step_after_the_install_still_counts(self):
        _, job, inst = _job(BEFORE + INSTALL + "      - uses: actions/cache/save@v4\n"
                            "      - uses: actions/cache@v4\n")
        assert cache_state(job, inst, UNFETCHED, {}) == "cached"

    def test_nested_composites_are_followed(self):
        outer = f"runs:\n  using: composite\n  steps:\n    - uses: {LOCAL}\n"
        _, job, inst = _job(BEFORE + "      - uses: ./.github/actions/outer\n" + INSTALL)
        idx = _index(**{"./.github/actions/outer": outer, LOCAL: REMIX_COMPOSITE})
        assert cache_state(job, inst, UNFETCHED, idx) == "cached"

    def test_a_plain_job_is_uncached(self):
        _, job, inst = _job(BEFORE + INSTALL)
        assert cache_state(job, inst, UNFETCHED, {}) == "uncached"


class _Files:
    """A provider holding a few files; records every request."""

    def __init__(self, files: dict[tuple[str, str, str | None], str], fail: set[str] = frozenset()):
        self.files, self.fail, self.calls = files, fail, []

    async def fetch_text_file(self, repo, path, *, ref=None):
        self.calls.append((f"{repo.owner}/{repo.name}", path, ref))
        if path in self.fail:
            raise RuntimeError("502")
        return self.files.get((f"{repo.owner}/{repo.name}", path, ref))


class TestResolve:
    def test_local_reads_the_audited_repo_and_falls_back_to_action_yaml(self):
        p = _Files({("acme/widget", ".github/actions/setup-node-pnpm-cache/action.yaml", None):
                    REMIX_COMPOSITE})
        idx = asyncio.run(resolve_actions(p, REPO, [LOCAL], known=KNOWN_ACTIONS))
        assert idx[LOCAL].is_composite
        assert [c[1].rsplit("/", 1)[1] for c in p.calls] == ["action.yml", "action.yaml"]

    def test_remote_reads_the_action_repo_at_its_ref(self):
        p = _Files({("angular/dev-infra",
                     "github-actions/npm/checkout-and-setup-node/action.yml", "1ffbb25"):
                    ANGULAR_COMPOSITE})
        idx = asyncio.run(resolve_actions(p, REPO, [REMOTE], known=KNOWN_ACTIONS))
        assert idx[REMOTE].is_composite
        assert len(p.calls) == 1  # the known actions inside were not fetched

    def test_missing_is_recorded_and_failure_is_not(self):
        p = _Files({}, fail={".github/actions/broken/action.yml"})
        idx = asyncio.run(resolve_actions(p, REPO, ["./.github/actions/gone",
                                                    "./.github/actions/broken"]))
        assert idx == {"./.github/actions/gone": None}  # broken stays absent: unknown

    def test_lookups_are_capped(self):
        p = _Files({})
        asyncio.run(resolve_actions(p, REPO, [f"./a{i}" for i in range(50)], max_lookups=5))
        assert len({c[1] for c in p.calls}) == 10  # 5 actions x (yml, yaml)


class TestFetchActionIndex:
    def test_only_actions_ahead_of_an_install_are_fetched(self):
        files = {"ci.yml": (
            "on: push\njobs:\n  build:\n    runs-on: x\n    steps:\n"
            "      - uses: actions/checkout@v5\n"
            f"      - uses: {LOCAL}\n"
            "      - run: pnpm install\n"
            "      - uses: ./.github/actions/after\n"
            "  lint:\n    runs-on: x\n    steps:\n"
            "      - uses: ./.github/actions/no-install-here\n"
        )}
        p = _Files({})
        asyncio.run(fetch_action_index(p, REPO, files))
        fetched = {c[1].rsplit("/", 1)[0] for c in p.calls}
        assert fetched == {".github/actions/setup-node-pnpm-cache"}

    def test_a_repo_with_nothing_to_look_up_costs_nothing(self):
        files = {"ci.yml": "on: push\njobs:\n  b:\n    runs-on: x\n    steps:\n"
                           "      - uses: actions/checkout@v5\n      - run: npm ci\n"}
        p = _Files({})
        assert asyncio.run(fetch_action_index(p, REPO, files)) == {}
        assert p.calls == []


class TestDiskCache:
    def test_a_second_sweep_makes_no_request(self, tmp_path):
        p = _Files({("acme/widget", "x/action.yml", None): JS_ACTION})
        cached = _DiskCachedFiles(p, tmp_path)
        first = asyncio.run(cached.fetch_text_file(REPO, "x/action.yml"))
        second = asyncio.run(cached.fetch_text_file(REPO, "x/action.yml"))
        assert first == second == JS_ACTION and len(p.calls) == 1

    def test_a_failure_is_not_cached(self, tmp_path):
        p = _Files({}, fail={"x/action.yml"})
        cached = _DiskCachedFiles(p, tmp_path)
        with pytest.raises(RuntimeError):
            asyncio.run(cached.fetch_text_file(REPO, "x/action.yml"))
        assert list(tmp_path.iterdir()) == []
