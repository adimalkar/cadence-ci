"""What an action a job `uses:` actually does, as far as config can say (CAVEATS 64).

A workflow step like `uses: ./.github/actions/setup` or
`uses: angular/dev-infra/github-actions/npm/checkout-and-setup-node@<sha>` is opaque from the
workflow file alone. On the corpus, 28 of 50 `no_dependency_cache` findings sat behind one:
remix restores its pnpm store in a local composite action, angular in a remote one. Their
install durations were flat because the cache *always hits* -- which timing alone cannot tell
apart from "never cached". Config is the only thing that can, so config has to be read through
the action.

Only **composite** actions have readable steps. A JavaScript or Docker action is code; what it
does is known only for the handful the detectors name (the setup actions), and every other one
is treated as doing nothing we measure.

`ActionIndex` is keyed by the `uses:` string. A key that is absent means *not looked up*, and
detectors must treat that as unknown -- never as "does nothing".
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from cadence.models import Repo
from cadence.workflow import Step, Workflow, parse_steps

# `owner/repo@ref` or `owner/repo/sub/path@ref`.
_REMOTE = re.compile(r"^(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)(?P<path>(/[^@]+)?)@(?P<ref>\S+)$")
# Resolution is breadth-first and capped: every lookup is an API request, and a composite
# can nest others. The corpus needed at most 9 for one repo.
MAX_LOOKUPS = 30
MAX_DEPTH = 3


@dataclass(frozen=True, slots=True)
class ActionRef:
    """Where an action's `action.yml` lives. None of the fields are fetched here."""

    uses: str
    owner: str | None      # None for a local action: it is in the audited repo
    repo: str | None
    path: str              # directory holding action.yml, "" for the repo root
    ref: str | None        # None for a local action: read at the same ref as the workflows

    @property
    def is_local(self) -> bool:
        return self.owner is None


def parse_ref(uses: str | None) -> ActionRef | None:
    """`./x`, `owner/repo[/path]@ref`; None for `docker://` and anything unrecognised."""
    if not uses:
        return None
    uses = uses.strip()
    if uses.startswith("./"):
        return ActionRef(uses, None, None, uses[2:].rstrip("/"), None)
    m = _REMOTE.match(uses)
    if not m:
        return None
    return ActionRef(uses, m["owner"], m["repo"], m["path"].strip("/"), m["ref"])


@dataclass(frozen=True, slots=True)
class ActionManifest:
    """The parts of an `action.yml` a detector can reason about.

    `using` is `composite`, `node20`, `docker`, ... or None when the file would not parse.
    `steps` is populated for composites only.
    """

    using: str | None
    steps: list[Step] = field(default_factory=list)

    @property
    def is_composite(self) -> bool:
        return self.using == "composite"

    @classmethod
    def from_text(cls, text: str) -> ActionManifest:
        try:
            doc = YAML().load(io.StringIO(text))
        except (YAMLError, Exception):  # noqa: BLE001 - third-party parser, any error
            return cls(using=None)
        runs = doc.get("runs") if isinstance(doc, dict) else None
        if not isinstance(runs, dict):
            return cls(using=None)
        using = str(runs.get("using") or "") or None
        steps = parse_steps(runs.get("steps")) if using == "composite" else []
        return cls(using=using, steps=steps)


# uses -> manifest, or None when it was looked up and does not exist. Absent: not looked up.
ActionIndex = dict[str, "ActionManifest | None"]


async def resolve_actions(
    provider,
    repo: Repo,
    uses: list[str],
    *,
    ref: str | None = None,
    known: set[str] | frozenset[str] = frozenset(),
    max_lookups: int = MAX_LOOKUPS,
) -> ActionIndex:
    """Fetch the manifests behind `uses`, following composites into what they use.

    `known` names actions whose behaviour the caller already models (`actions/checkout`,
    the setup actions); they are never fetched. A failed request leaves the action absent
    -- unknown -- rather than recording it as missing.
    """
    index: ActionIndex = {}
    queue: list[tuple[str, int]] = [(u, 0) for u in dict.fromkeys(uses)]
    lookups = 0
    while queue and lookups < max_lookups:
        use, depth = queue.pop(0)
        target = parse_ref(use)
        if target is None or use in index or _identity(use) in known:
            continue
        lookups += 1
        try:
            manifest = await _fetch_manifest(provider, repo, target, ref)
        except Exception:  # best-effort: unknown, not absent
            continue
        index[use] = manifest
        if manifest and manifest.is_composite and depth + 1 < MAX_DEPTH:
            queue.extend((s.uses, depth + 1) for s in manifest.steps if s.uses)
    return index


def uses_before(workflow: Workflow, job_key: str, step_index: int) -> list[str]:
    """Every `uses:` in a job ahead of one step: what could have set up its environment."""
    job = workflow.jobs[job_key]
    return [s.uses for s in job.steps if s.index < step_index and s.uses]


def _identity(uses: str) -> str:
    return uses.split("@", 1)[0].strip()


async def _fetch_manifest(
    provider, repo: Repo, target: ActionRef, ref: str | None
) -> ActionManifest | None:
    owner = repo if target.is_local else Repo(
        id=0, owner=target.owner or "", name=target.repo or "", is_private=False,
        default_branch=None,
    )
    at = ref if target.is_local else target.ref
    for name in ("action.yml", "action.yaml"):
        path = f"{target.path}/{name}" if target.path else name
        text = await provider.fetch_text_file(owner, path, ref=at)
        if text is not None:
            return ActionManifest.from_text(text)
    return None


def manifest_steps(index: ActionIndex, uses: str | None) -> list[Step] | None:
    """A composite's steps; [] for a non-composite that was read; None when unknown."""
    if not uses or uses not in index:
        return None
    manifest = index[uses]
    if manifest is None:
        return None
    return manifest.steps if manifest.is_composite else []


def iter_uses(workflows: list[Workflow]) -> list[str]:
    """Every distinct `uses:` across parsed workflows, in first-seen order."""
    seen: dict[str, None] = {}
    for wf in workflows:
        if wf.parse_error:
            continue
        for job in wf.jobs.values():
            for step in job.steps:
                if step.uses:
                    seen.setdefault(step.uses, None)
    return list(seen)

