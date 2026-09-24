"""`concurrency.add` — the first fixer, for `no_run_cancellation`.

**Why this one first.** `no_run_cancellation` reaches 76.5% of the corpus (CAVEATS 45), the
widest of any rule, and `PHASE_2_FIX_PRS.md` classes its fix as *"three lines, additive, no
semantics change"*. Highest value, lowest blast radius.

**Built text-first.** It inserts lines into the source and never re-serialises the file.
Measured in #20: only 71.8% of corpus workflows survive a ruamel round-trip byte-identically,
while line insertion preserves 100% by construction — including the 28% ruamel would reshape.
`is_faithful` is therefore not a precondition here; the verification below is.

**What it inserts, and why literally `true`.**

    concurrency:
      group: ${{ github.workflow }}-${{ github.ref }}
      cancel-in-progress: true

The fashionable form cancels only pull-request runs —
`cancel-in-progress: ${{ github.event_name == 'pull_request' }}`. It was rejected for a
specific reason: `Workflow.cancel_in_progress` is True **only for a literal `true`**, because
the parser deliberately refuses to evaluate expressions. An expression would leave the
detector unsatisfied, and it would **re-propose this fix after it merged** — the exact
behaviour the anti-spam rules exist to prevent. A literal is also what this repo's own CI
uses.

The group is keyed on workflow and ref, matching how the detector groups superseded runs (by
branch), so the saving the finding claims is the saving the fix delivers.

**Declining is always correct; a wrong fix is not.** It declines, with a reason, for:

- a workflow that **already has `concurrency:`** — changing it overrides a choice someone
  made, which is a proposal, not an automated fix;
- anything **deploy-shaped** — cancelling a deployment mid-flight is the one outcome here
  with real cost;
- **reusable workflows** (`on: workflow_call`) — `github.workflow` there names the *caller*,
  so two calls in one run share a group and cancel each other;
- anything that fails to parse, or has no single top-level `jobs:`;
- any result that fails verification.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from cadence.detectors.base import FindingDraft
from cadence.fixers.base import Diff, FixResult
from cadence.workflow import Workflow, parse_workflow

FIXER_ID = "concurrency.add"
FIXER_VERSION = 1
APPLIES_TO = "no_run_cancellation"

# Names and paths that say "this ships something". Deliberately broad: a false decline costs
# one un-opened PR, a false accept can cancel a production deploy.
_DEPLOYISH = re.compile(
    r"deploy|release|publish|production|\bprod\b|\bcd\b|rollout|promote|ship",
    re.IGNORECASE,
)
# Permissions that mean the workflow writes somewhere outside CI: OIDC to a cloud, or
# pushing packages, tags or releases.
_WRITE_PERMS = {"id-token", "packages", "contents", "deployments", "pages"}

_TOP_LEVEL_JOBS = re.compile(r"^jobs:\s*(#.*)?$")
_TOP_LEVEL_KEY = re.compile(r"^[A-Za-z_\-\"']")


@dataclass(frozen=True, slots=True)
class _Insertion:
    at: int              # line index in the original the block goes before
    lines: list[str]     # lines to insert, without line terminators


class ConcurrencyFixer:
    id = FIXER_ID
    version = FIXER_VERSION
    applies_to = APPLIES_TO

    def preview(self, workflow: Workflow, content: str, finding: FindingDraft) -> FixResult:
        if finding.kind != APPLIES_TO:
            return FixResult(None, f"applies to {APPLIES_TO}, not {finding.kind}")
        if finding.dedupe_key != f"{APPLIES_TO}:{workflow.path}":
            return FixResult(None, "finding does not refer to this workflow")
        if workflow.parse_error:
            return FixResult(None, f"workflow does not parse: {workflow.parse_error[:80]}")
        if workflow.concurrency is not None or _has_top_level_key(content, "concurrency"):
            return FixResult(
                None,
                "workflow already declares concurrency; changing it would override a "
                "choice someone made, so this is proposed by hand rather than automated",
            )

        declined = _deploy_reason(workflow, content)
        if declined:
            return FixResult(None, declined)

        insertion = _plan_insertion(content)
        if isinstance(insertion, str):
            return FixResult(None, insertion)

        newline = "\r\n" if "\r\n" in content else "\n"
        original = content.split(newline)
        after = newline.join(
            original[: insertion.at] + insertion.lines + original[insertion.at :]
        )

        problem = _verify(workflow, content, after, insertion, newline)
        if problem:
            return FixResult(None, f"verification failed, not proposing: {problem}")

        return FixResult(
            Diff(
                path=workflow.path,
                before=content,
                after=after,
                summary=(
                    "Cancel superseded runs: a newer commit on the same branch now cancels "
                    "the run it replaces instead of letting it finish."
                ),
                revert_hint=(
                    f"Delete the {len(insertion.lines) - 1} `concurrency:` lines directly "
                    f"above `jobs:` in {workflow.path}."
                ),
            )
        )

    def confidence(self, workflow: Workflow, finding: FindingDraft) -> float:
        # Additive, three lines, verified by re-parse. What is left uncertain is whether
        # the maintainer wants main-branch runs cancelled too, which is a preference,
        # not a correctness question.
        return 0.9


# ── declining ──────────────────────────────────────────────────────────────────────────


def _deploy_reason(workflow: Workflow, content: str) -> str | None:
    """A reason this workflow must not be auto-fixed, or None."""
    triggers = _trigger_names(workflow.on)
    if "workflow_call" in triggers:
        return (
            "reusable workflow (on: workflow_call): github.workflow names the caller, so "
            "two calls in one run would share a group and cancel each other"
        )
    if "release" in triggers:
        return "triggered by release events; cancelling could interrupt a publish"
    if _pushes_tags(workflow.on):
        return "triggered by tag pushes; cancelling could interrupt a release"

    for label, text in (("name", workflow.name or ""), ("path", workflow.path)):
        if _DEPLOYISH.search(str(text)):
            return (
                f"workflow {label} looks deploy-shaped ({text!r}); cancelling a deploy "
                "mid-flight is the costly failure"
            )

    for key, job in workflow.jobs.items():
        if job.raw.get("environment"):
            return f"job {key!r} targets an environment; cancelling could interrupt a deploy"
        perms = _write_permissions(job.raw.get("permissions"))
        if perms:
            return f"job {key!r} has write permissions ({', '.join(sorted(perms))}); it may publish"

    perms = _write_permissions(_top_level(content).get("permissions"))
    if perms:
        return f"workflow has write permissions ({', '.join(sorted(perms))}); it may publish"
    return None


def _trigger_names(on: Any) -> set[str]:
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return {str(x) for x in on}
    if isinstance(on, dict):
        return {str(k) for k in on}
    return set()


def _pushes_tags(on: Any) -> bool:
    if not isinstance(on, dict):
        return False
    push = on.get("push")
    return isinstance(push, dict) and ("tags" in push or "tags-ignore" in push)


def _write_permissions(perms: Any) -> set[str]:
    if perms == "write-all":
        return {"write-all"}
    if not isinstance(perms, dict):
        return set()
    return {str(k) for k, v in perms.items() if str(k) in _WRITE_PERMS and v == "write"}


def _top_level(content: str) -> dict[str, Any]:
    try:
        doc = YAML(typ="safe").load(io.StringIO(content))
    except (YAMLError, Exception):  # noqa: BLE001 - third-party parser, any error
        return {}
    return doc if isinstance(doc, dict) else {}


def _has_top_level_key(content: str, key: str) -> bool:
    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    return any(pattern.match(line) for line in content.splitlines())


# ── inserting ──────────────────────────────────────────────────────────────────────────


def _plan_insertion(content: str) -> _Insertion | str:
    """Where the block goes, and what it says — or a reason it cannot be placed."""
    lines = content.splitlines()
    jobs_at = [i for i, line in enumerate(lines) if _TOP_LEVEL_JOBS.match(line)]
    if not jobs_at:
        return "no top-level `jobs:` line to insert above"
    if len(jobs_at) > 1:
        return "more than one top-level `jobs:` line; refusing to guess which is real"

    at = jobs_at[0]
    # Comments sitting directly on `jobs:` belong to it. Insert above them, so the new block
    # does not separate a comment from the key it describes.
    while at > 0 and lines[at - 1].lstrip().startswith("#") and not lines[at - 1].startswith(" "):
        at -= 1

    indent = _child_indent(lines, jobs_at[0])
    block = [
        "concurrency:",
        f"{indent}group: ${{{{ github.workflow }}}}-${{{{ github.ref }}}}",
        f"{indent}cancel-in-progress: true",
        "",
    ]
    return _Insertion(at=at, lines=block)


def _child_indent(lines: list[str], jobs_line: int) -> str:
    """The indent this file uses for a top-level key's children, so the block matches.

    Measured from the first indented line under `jobs:`. Falls back to two spaces, which is
    what `actions/starter-workflows` emits and what most of the corpus uses. This is plain
    whitespace measurement on our own inserted text — unrelated to the ruamel indent
    detector that failed in #20, which was about re-serialising other people's lists.
    """
    for line in lines[jobs_line + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        width = len(line) - len(line.lstrip(" "))
        return " " * width if 0 < width <= 8 else "  "
    return "  "


# ── verifying ──────────────────────────────────────────────────────────────────────────


def _verify(
    before_wf: Workflow, before: str, after: str, insertion: _Insertion, newline: str
) -> str | None:
    """Every check that must hold before a diff reaches a maintainer. None means all pass."""
    # 1. Only the inserted lines changed, in place. Checked, not assumed.
    expected = (
        before.split(newline)[: insertion.at]
        + insertion.lines
        + before.split(newline)[insertion.at :]
    )
    if after.split(newline) != expected:
        return "diff would touch lines outside the inserted block"

    after_wf = parse_workflow(before_wf.path, after)
    # 2. Still valid YAML the parser understands.
    if after_wf.parse_error:
        return f"result does not parse: {after_wf.parse_error[:80]}"
    # 3. The detector's own predicate is now satisfied, so it will not re-propose.
    if not after_wf.cancel_in_progress:
        return "result does not satisfy the detector; it would re-propose this fix"
    # 4. Nothing about the jobs or triggers moved.
    if set(after_wf.jobs) != set(before_wf.jobs):
        return "job set changed"
    if after_wf.on != before_wf.on:
        return "triggers changed"
    for key, job in before_wf.jobs.items():
        if after_wf.jobs[key].raw != job.raw:
            return f"job {key!r} changed"
    return None
