"""The fixer contract.

From `PHASE_2_FIX_PRS.md`, unchanged in substance:

    preview() returns None rather than guessing when the workflow shape is unfamiliar.
    Declining to fix is always correct; a wrong fix is not.

That asymmetry is the whole design. A bad cache config wastes a minute; a wrongly removed
`needs:` edge ships broken code. So fixers ship only for **additive, reversible** changes,
and every other finding is reported with a suggested diff the maintainer applies by hand.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Protocol

from cadence.detectors.base import FindingDraft
from cadence.workflow import Workflow


@dataclass(frozen=True, slots=True)
class Diff:
    """A unified diff against one workflow file, plus how to undo it.

    `revert_hint` is carried on every fix because a PR the maintainer cannot cheaply
    reverse is a PR they will not merge -- and the Phase 2 risk section names a bad fix
    as the failure mode with real cost.
    """

    path: str
    before: str
    after: str
    summary: str
    revert_hint: str

    @property
    def unified(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=f"a/{self.path}",
                tofile=f"b/{self.path}",
                n=3,
            )
        )

    @property
    def changed_lines(self) -> int:
        return sum(
            1
            for line in self.unified.splitlines()
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        )


@dataclass(frozen=True, slots=True)
class FixResult:
    """Why a fixer declined, when it did.

    A silent `None` is unactionable: across strangers' repositories nobody can ask us why
    a fix did not appear, so the reason travels with the answer.
    """

    diff: Diff | None
    declined: str | None = None

    @property
    def ok(self) -> bool:
        return self.diff is not None


class Fixer(Protocol):
    id: str          # 'cache.node.npm'
    version: int     # bump invalidates prior PRs
    applies_to: str  # finding kind

    def preview(self, workflow: Workflow, content: str, finding: FindingDraft) -> FixResult:
        """Produce a diff, or decline with a reason. Never guess."""
        ...

    def confidence(self, workflow: Workflow, finding: FindingDraft) -> float:
        ...
