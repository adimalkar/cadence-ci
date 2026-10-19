"""Missing dependency cache, and cache keys that can never hit.

Two findings share this module because they are the same measurement read differently:

  * **No cache configured** — action-specific inputs and defaults, including caches in
    composites, leave the job uncached, and its install duration is *flat*. A warm cache
    can be flat too: timing alone cannot establish that nothing is being restored.
  * **A key that cannot hit** — a key interpolating `github.run_id` (or `github.sha`)
    is unique per run, so a fresh run never restores it. That is *only* a bug when nothing
    else restores the entry, and on the corpus nothing-else was the exception: all 9
    flagged steps were working caches (CAVEATS 58). Two patterns restore a per-run key:
    `restore-keys:` (restore the newest entry by prefix, save a fresh one each run — the
    documented pattern for incremental caches), and a *handoff*, where a later job in the
    same run, or another workflow, restores the exact key to pass a build along. Either one
    silences the finding.

Savings here are **projection**, never replay: we have no observation of this repo in a
cached state, so the number is an estimate with a range and a named basis.
"""

from __future__ import annotations

import re

from cadence.actions import ActionIndex, manifest_steps
from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext, RootPackageJson
from cadence.simulate import duration_is_flat, project_cache_savings
from cadence.workflow import Job, Step, Workflow

DETECTOR_ID = "waste.dependency_cache"
DETECTOR_VERSION = "dependency_cache@6"

_CACHE_ACTION = "actions/cache"
# Setup actions with built-in caching. Go, Ruby and uv have their own input/default
# models below; the remaining actions retain the explicit `cache:` check.
_SETUP_ACTIONS = {
    "actions/setup-node", "actions/setup-python", "actions/setup-java",
    "actions/setup-go", "actions/setup-dotnet", "ruby/setup-ruby",
    "astral-sh/setup-uv", "pnpm/action-setup",
}
# Dedicated caching actions that make an explicit actions/cache step unnecessary.
# Not `actions/setup-node`: until @3 it sat here, which marked every setup-node job as
# cached. Without `cache:` it caches only in one narrow case -- see setup_node_auto_caches.
_CACHE_EQUIVALENT = {
    "Swatinem/rust-cache", "actions/cache/restore", "buildjet/cache",
    "runs-on/cache", "useblacksmith/cache",
}
_SETUP_NODE = "actions/setup-node"
_CHECKOUT = "actions/checkout"
# Actions whose effect on a dependency cache is modelled here or is nil, so they are never
# looked up. Anything else ahead of the install is read through (a composite) or, if it
# cannot be read, makes the job's cache state unknown (CAVEATS 64).
_NON_CACHING = {
    _CHECKOUT, "actions/upload-artifact", "actions/download-artifact",
    "actions/github-script", "actions/create-github-app-token",
    "step-security/harden-runner",
}
KNOWN_ACTIONS = frozenset(
    _NON_CACHING | _SETUP_ACTIONS | _CACHE_EQUIVALENT | {_CACHE_ACTION, "actions/cache/save"}
)
# setup-node gained automatic caching in v5.0.0 (2025-09-04).
_SETUP_NODE_AUTO_CACHE_MAJOR = 5
_MAJOR = re.compile(r"^v?(\d+)(\.|$)")
_VERSION = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?$")
# Only standard hosted labels establish setup-uv's `auto` behaviour. Expressions,
# runner groups and custom labels remain unknown; explicit `self-hosted` is definitive.
_HOSTED_RUNNER = re.compile(
    r"^(ubuntu-(latest|\d+\.\d+)(-arm)?|"
    r"windows-(latest|\d+)(-arm)?|macos-(latest|\d+)(-(large|xlarge|intel))?)$"
)

# Language dependency installs -- what a lockfile-keyed cache speeds up. Not
# `apt-get install`: system packages have no lockfile, and the suggested fix cannot apply
# to them. Until @4 it was here, and 16 of 50 corpus findings were apt (CAVEATS 61).
_INSTALL_PATTERNS = re.compile(
    r"\b(npm (ci|install)|yarn install|pnpm install|bundle install|"
    r"pip install|poetry install|uv sync|uv pip install|"
    r"go mod download|cargo fetch|mvn .*dependency:go-offline|gradle .*dependencies|"
    r"composer install)\b",
    re.IGNORECASE,
)
# Commands that can share a step with the install without changing what its duration
# measures: shell bookkeeping, version probes, and the package manager updating itself.
# Anything else -- a build, a test run, apt -- means the step's time is not the install's,
# and projecting from it prices that other work as "cache savings".
_BOOKKEEPING = re.compile(
    r"^(\#|cd\b|echo\b|printf\b|export\b|set\b|mkdir\b|source\b|\.\s|which\b|"
    r"corepack\b|true$|:$|"
    r"(python\d*(\.\d+)?\s+-m\s+)?pip\d*\s+(uninstall|list|freeze|config|--version)\b|"
    r"(node|npm|npx|pnpm|yarn|python\d*(\.\d+)?|pip\d*|uv|go|cargo|ruby|bundle|java|mvn|"
    r"gradle|composer)\s+(--version|-v|-V|version)\s*$)",
    re.IGNORECASE,
)
# The package manager upgrading itself. Bookkeeping, and never the dependency install.
_SELF_UPDATE = re.compile(
    r"^((python\d*(\.\d+)?\s+-m\s+)?pip\d*\s+install\s+(-U|--upgrade)\s+"
    r"(pip|setuptools|wheel)(\s+(pip|setuptools|wheel))*|"
    r"npm\s+(install|i)\s+(-g|--global)\s+(npm|corepack)(@\S+)?)\s*$",
    re.IGNORECASE,
)

# An expression that *is* one of these -- not one that merely mentions it, since
# `github.head_ref || github.run_id` is per-branch on a pull request -- makes the key
# unique at that scope. run_id and run_number survive a re-run; run_attempt does not.
_UNIQUE_SCOPE = {
    "github.run_id": "run",
    "github.run_number": "run",
    "github.run_attempt": "attempt",
    "github.sha": "commit",
}
# Steps that read a cache entry back, and so can make a per-run key hit.
_RESTORERS = {_CACHE_ACTION, "actions/cache/restore"}
_EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)


class DependencyCacheDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        drafts: list[FindingDraft] = []
        # Every restoring step in the repo: a handoff can cross workflows (`workflow_run`).
        restorers = [
            step
            for wf in ctx.workflows if not wf.parse_error
            for job in wf.jobs.values()
            for step in job.steps if step.action in _RESTORERS
        ]
        for wf in ctx.workflows:
            if wf.parse_error:
                continue
            for job_key, job in wf.jobs.items():
                drafts.extend(self._never_hits(wf, job_key, job, restorers))
                draft = self._missing_cache(ctx, wf, job_key, job)
                if draft is not None:
                    drafts.append(draft)
        return drafts

    # ── a key that can never hit ───────────────────────────────────────────────
    def _never_hits(
        self, wf: Workflow, job_key: str, job: Job, restorers: list[Step]
    ) -> list[FindingDraft]:
        out: list[FindingDraft] = []
        for step in job.steps:
            if step.action != _CACHE_ACTION:
                continue
            key = str(step.with_.get("key", ""))
            scope = unique_scope(key)
            if scope is None:
                continue
            if _restore_keys(step):
                continue  # restores the newest entry by prefix: a working cache
            if any(_restores(other, step, key) for other in restorers):
                continue  # handed to another job or workflow, by key or by prefix
            out.append(
                FindingDraft(
                    kind="cache_key_never_hits",
                    module="waste",
                    severity=4,
                    # Down from 0.98: the rule was 0 for 9 on the corpus before the two
                    # restore paths were checked (CAVEATS 58). What is left is structural,
                    # but "nothing restores it" is a claim about the files we could see.
                    confidence=0.9,
                    dedupe_key=f"cache_key_never_hits:{wf.path}:{job_key}:{step.index}",
                    title=_NEVER_HITS_TITLE[scope].format(job=job_key),
                    detector_version=DETECTOR_VERSION,
                    suggested_action=(
                        "Key on content, not the run: "
                        "`${{ runner.os }}-${{ hashFiles('**/lockfile') }}`. If a fresh "
                        "entry every run is the point, add `restore-keys:` with the key's "
                        "prefix so the newest one is restored."
                    ),
                    savings=None,  # the waste is the whole restore; sizing needs a baseline
                    evidence=[
                        EvidenceDraft(
                            kind="code_range",
                            file_path=wf.path,
                            line_start=step.line,
                            line_end=step.line,
                            payload={
                                "key": key[:200],
                                "job": job_key,
                                "unique_per": scope,
                                "restore_keys": None,
                                "restoring_steps_checked": len(restorers) - 1,
                            },
                        )
                    ],
                )
            )
        return out

    # ── no cache configured at all ─────────────────────────────────────────────
    def _missing_cache(
        self, ctx: AuditContext, wf: Workflow, job_key: str, job: Job
    ) -> FindingDraft | None:
        install = install_step(job)
        if install is None:
            return None
        # Only a job we can see to be uncached is a finding. `unknown` means an action ran
        # before the install that we could not read; `disabled` means someone turned the
        # cache off on purpose -- ruff and vite do, in publish workflows, which zizmor
        # recommends against caching in. Neither is ours to call waste.
        if cache_state(job, install, ctx.root_package_json, ctx.actions) != "uncached":
            return None

        # This step's own durations or nothing. Until @4 a miss fell back to the job's
        # longest-running step, which priced 40 corpus jobs from a test or upload step.
        series = ctx.series_for_step(wf.path, job_key, install)
        if series is None or len(series.durations) < 5:
            return None

        # Bimodal durations suggest a cache we did not model. Flat durations alone do
        # not prove a cold install: an always-warm cache is flat too (CAVEATS 64).
        if not duration_is_flat(series.durations):
            return None

        savings = project_cache_savings(series.durations)
        if savings is None or savings.high < 5.0:
            return None

        return FindingDraft(
            kind="no_dependency_cache",
            module="waste",
            severity=3,
            confidence=0.75,  # projection-based; lower than the config-only rules
            dedupe_key=f"no_dependency_cache:{wf.path}:{job_key}",
            title=f"`{job_key}` installs dependencies cold on every run — no cache configured",
            detector_version=DETECTOR_VERSION,
            suggested_action=(
                "Add actions/cache keyed on `${{ runner.os }}-${{ hashFiles('<lockfile>') }}`, "
                "or set `cache:` on the setup action for this ecosystem."
            ),
            savings=savings,
            parallel_jobs=1.0,
            evidence=[
                EvidenceDraft(
                    kind="code_range",
                    file_path=wf.path,
                    line_start=install.line,
                    line_end=install.line,
                    payload={"job": job_key, "step": install.name or install.run},
                ),
                EvidenceDraft(
                    kind="timing_series",
                    payload={
                        "step": series.step_name,
                        "durations": [round(d, 1) for d in series.durations[:100]],
                        "flat": True,
                        "detail": savings.detail,
                    },
                ),
                EvidenceDraft(
                    kind="counterfactual",
                    payload={
                        "basis": savings.basis.value,
                        "low_seconds": round(savings.low, 1),
                        "high_seconds": round(savings.high, 1),
                        "method": "projected from cold-install p50; no cached state observed",
                    },
                ),
            ],
        )


def cache_state(
    job: Job, install: Step, package_json: RootPackageJson, actions: ActionIndex
) -> str:
    """`cached`, `uncached`, `disabled` or `unknown`, for the dependencies `install` fetches.

    A caching step anywhere in the job counts, as it always has. What is new in @5 is that
    an action ahead of the install is read through when it is a composite, and makes the
    answer `unknown` when it cannot be read at all: 28 of 50 corpus findings were jobs
    cached inside a composite (CAVEATS 64).
    """
    before = [s for s in job.steps if s.index < install.index]
    after = [s for s in job.steps if s.index > install.index]
    state = _steps_state(before, package_json, actions, checkout=None, depth=0,
                         runs_on=job.runs_on)
    if state == "cached":
        return state
    # A cache step after the install still counts, as it always has. An unreadable action
    # there does not: it runs too late to have set the install up.
    checkout = next((s for s in reversed(before) if s.action == _CHECKOUT), None)
    if _steps_state(after, package_json, actions, checkout=checkout, depth=0,
                    runs_on=job.runs_on) == "cached":
        return "cached"
    return state


def _steps_state(
    steps: list[Step],
    package_json: RootPackageJson,
    actions: ActionIndex,
    *,
    checkout: Step | None,
    depth: int,
    runs_on: list[str],
) -> str:
    unknown = disabled = False
    for step in steps:
        action = step.action
        if action is None:
            continue
        if action == _CHECKOUT:
            checkout = step
            continue
        if action == _CACHE_ACTION or action in _CACHE_EQUIVALENT:
            return "cached"
        state = _setup_cache_state(step, runs_on)
        if state is not None:
            if state == "cached":
                return state
            unknown = unknown or state == "unknown"
            disabled = disabled or state == "disabled"
            continue
        if action in _SETUP_ACTIONS and step.with_.get("cache"):
            return "cached"
        if action == _SETUP_NODE:
            if _explicitly_disabled(step):
                disabled = True
            elif _setup_node_caches(step, checkout, package_json):
                return "cached"
            continue
        if action in KNOWN_ACTIONS:
            continue
        inner = manifest_steps(actions, step.uses)
        if inner is None or depth >= 2:
            unknown = True
            continue
        state = _steps_state(inner, package_json, actions, checkout=checkout, depth=depth + 1,
                             runs_on=runs_on)
        if state == "cached":
            return "cached"
        unknown = unknown or state == "unknown"
        disabled = disabled or state == "disabled"
    if disabled:
        return "disabled"
    return "unknown" if unknown else "uncached"


def _setup_cache_state(step: Step, runs_on: list[str]) -> str | None:
    """Per-action cache semantics verified from upstream source (CAVEATS 65).

    setup-go v3/action.yml defaults off, v4 defaults on; src/main.ts reads a boolean.
    ruby/setup-ruby v1/index.js reads `bundler-cache`, default false, never `cache`.
    setup-uv v1–v4/action.yml default off, v5+ default `auto`; src/utils/inputs.ts
    enables auto only on GitHub-hosted runners. v6.7 adds `restore-cache`.
    Unresolved versions/inputs remain unknown rather than becoming new findings.
    """
    if step.action == "ruby/setup-ruby":
        return _boolean_cache_state(step, "bundler-cache", default="uncached")
    if step.action == "actions/setup-go":
        major = setup_node_major(step)  # parses the ref, independent of action identity
        if major is not None and major < 3:
            return "uncached"  # these versions do not implement the cache input
        default = "unknown" if major is None else "cached" if major >= 4 else "uncached"
        return _boolean_cache_state(step, "cache", default=default)
    if step.action != "astral-sh/setup-uv":
        return None

    major = setup_node_major(step)
    # A deliberate restore opt-out is not a missing-cache finding. The knob appeared
    # in v6.7, not v6.0; floating @v6 now supports it, older exact pins do not.
    version_match = _VERSION.fullmatch((step.uses or "").partition("@")[2].strip())
    version = tuple(int(v) for v in version_match.groups() if v is not None) \
        if version_match else ()
    supports_restore = version == (6,) or version >= (6, 7)
    if (not version or supports_restore) and "restore-cache" in step.with_:
        restore = _boolean_cache_state(step, "restore-cache", default="unknown")
        if restore != "cached":
            return restore if supports_restore else "unknown"
    if "enable-cache" in step.with_:
        if step.with_["enable-cache"] != "auto":
            return _boolean_cache_state(step, "enable-cache", default="unknown")
        if major is None or major < 5:
            return "unknown"  # `auto` was introduced in v5
    elif major is None:
        return "unknown"
    elif major < 5:
        return "uncached"
    if "self-hosted" in runs_on:
        return "uncached"
    if len(runs_on) == 1 and _HOSTED_RUNNER.fullmatch(runs_on[0]):
        return "cached"
    return "unknown"


def _boolean_cache_state(step: Step, key: str, *, default: str) -> str:
    if key not in step.with_:
        return default
    value = step.with_[key]
    # YAML booleans and string action inputs both occur in the corpus. Never use Python
    # truthiness: the string "false" is truthy, and an expression is not a known boolean.
    if value is True or isinstance(value, str) and value.strip() == "true":
        return "cached"
    if value is False or isinstance(value, str) and value.strip() == "false":
        return "disabled"
    return "unknown"


def _explicitly_disabled(step: Step) -> bool:
    return str(step.with_.get("package-manager-cache", "")).strip().lower() == "false"


def setup_node_auto_caches(job: Job, step: Step, package_json: RootPackageJson) -> bool:
    """Does this `actions/setup-node` step cache with no `cache:` input?

    Mirrors setup-node's `src/main.ts`, not its changelog. It caches on its own only when
    all of these hold: v5 or later, `package-manager-cache` not false, and the
    `package.json` at the workspace root names npm. That file exists only once something
    has checked the repository out, so a setup-node before any checkout reads nothing.

    Where we cannot know, the answer is "cached" -- the pre-@3 behaviour -- because this
    decides whether a job is examined at all, and the timing gate is the only check left.
    """
    return _setup_node_caches(step, _checkout_before(job, step), package_json)


def _setup_node_caches(
    step: Step, checkout: Step | None, package_json: RootPackageJson
) -> bool:
    if step.with_.get("cache"):
        return True
    if _explicitly_disabled(step):
        return False
    major = setup_node_major(step)
    if major is not None and major < _SETUP_NODE_AUTO_CACHE_MAJOR:
        return False
    if checkout is None:
        return False  # nothing in the workspace to read yet
    if checkout.with_.get("path") or checkout.with_.get("repository"):
        return True  # it reads a package.json that is not our repo root
    if not package_json.fetched:
        return True
    return package_json.declares_npm()


def setup_node_major(step: Step) -> int | None:
    """`4` from `@v4` or `@v4.1.0`; None for a SHA pin or a branch."""
    ref = (step.uses or "").partition("@")[2].strip()
    m = _MAJOR.match(ref)
    return int(m.group(1)) if m else None


def needs_root_package_json(workflows: list[Workflow]) -> bool:
    """Is the root `package.json` worth an API call? Only if a setup-node step's caching
    turns on it; most repos never pay the request."""
    for wf in workflows:
        if wf.parse_error:
            continue
        for job in wf.jobs.values():
            for step in job.steps:
                if step.action != _SETUP_NODE:
                    continue
                if setup_node_auto_caches(job, step, RootPackageJson(fetched=True)) != \
                        setup_node_auto_caches(job, step, RootPackageJson()):
                    return True
    return False


def _checkout_before(job: Job, step: Step) -> Step | None:
    """The last `actions/checkout` ahead of `step` in the same job."""
    found = None
    for s in job.steps:
        if s is step:
            return found
        if s.action == _CHECKOUT:
            found = s
    return None


def install_step(job: Job) -> Step | None:
    """The first step that is a dependency install and nothing heavier.

    A step that also builds or tests is skipped, not priced: its duration is the sum, and a
    projection from it would call the build time "cache savings" (redis's `apt-get install
    tcl` then `./runtest` came out at 594-705 s per run; CAVEATS 61).
    """
    for step in job.steps:
        if step.run and install_only(step.run):
            return step
    return None


def install_only(script: str) -> bool:
    """True if the script installs dependencies and does nothing else of substance."""
    commands = _commands(script)
    installs = [c for c in commands if _INSTALL_PATTERNS.search(c) and not _SELF_UPDATE.match(c)]
    if not installs:
        return False
    return all(
        c in installs or _SELF_UPDATE.match(c) or _BOOKKEEPING.match(c) for c in commands
    )


def _commands(script: str) -> list[str]:
    """Split a `run:` script into commands: line continuations joined, then split on
    newlines, `&&`, `||` and `;`. A shell parser would be exact; this is enough to tell
    `npm ci` from `npm ci && npm test`, and it errs toward "mixed", which declines."""
    joined = re.sub(r"\\\s*\n", " ", script)
    parts = re.split(r"\n|&&|\|\||;", joined)
    return [p.strip() for p in parts if p.strip()]




_NEVER_HITS_TITLE = {
    "run": "Cache key in `{job}` is unique per run — a fresh run never restores it",
    "attempt": (
        "Cache key in `{job}` is unique per attempt — never restored, not even on a re-run"
    ),
    "commit": (
        "Cache key in `{job}` is unique per commit — restored only when that commit is rebuilt"
    ),
}
_SCOPE_RANK = {"attempt": 0, "run": 1, "commit": 2}


def _norm_expr(expr: str) -> str:
    return " ".join(expr.split())


def unique_scope(key: str) -> str | None:
    """The narrowest scope a key is unique at, or None if it can repeat across runs.

    Only whole expressions count. `${{ github.head_ref || github.run_id }}` is per-branch on
    a pull request and would be a false positive to treat as per-run.
    """
    scopes = [
        _UNIQUE_SCOPE[e]
        for e in (_norm_expr(m) for m in _EXPR.findall(key))
        if e in _UNIQUE_SCOPE
    ]
    return min(scopes, key=_SCOPE_RANK.__getitem__) if scopes else None


def _key_pattern(key: str, *, prefix: bool = False) -> re.Pattern[str]:
    """A key as a pattern: per-run expressions stay literal, every other expression
    (`matrix.arch`, `hashFiles(...)`, `runner.os`) matches anything.

    Wildcarding errs toward *finding a restorer*, which silences the finding -- the right
    direction for a rule that claims a cache is dead.
    """
    parts: list[str] = []
    pos = 0
    for m in _EXPR.finditer(key):
        parts.append(re.escape(key[pos:m.start()]))
        expr = _norm_expr(m.group(1))
        parts.append(re.escape(f"${{{{ {expr} }}}}") if expr in _UNIQUE_SCOPE else ".*")
        pos = m.end()
    parts.append(re.escape(key[pos:]))
    return re.compile("".join(parts) + (".*" if prefix else ""), re.S)


def _canonical(key: str) -> str:
    return _EXPR.sub(lambda m: f"${{{{ {_norm_expr(m.group(1))} }}}}", key.strip())


def _key_matches(a: str, b: str) -> bool:
    """Could lookup key `a` hit an entry saved under `b`? Symmetric: either side may carry
    the matrix value literally (`dev-image-amd64-…` against `…-${{ matrix.arch }}-…`)."""
    a, b = a.strip(), b.strip()
    if not a or not b:
        return False
    return bool(
        _key_pattern(a).fullmatch(_canonical(b)) or _key_pattern(b).fullmatch(_canonical(a))
    )


def _restores(other: Step, step: Step, key: str) -> bool:
    """Does `other` read back what `step` saves under `key`? By exact key (a different
    step), or by any of its `restore-keys` prefixes (itself included)."""
    if other is not step and _key_matches(str(other.with_.get("key", "")), key):
        return True
    return any(_key_pattern(p, prefix=True).fullmatch(_canonical(key))
               for p in _restore_keys(other))


def _restore_keys(step: Step) -> list[str]:
    raw = step.with_.get("restore-keys")
    if raw is None:
        return []
    lines = raw if isinstance(raw, list) else str(raw).splitlines()
    return [str(line).strip() for line in lines if str(line).strip()]
