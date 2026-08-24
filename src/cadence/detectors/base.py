"""Detector contract and the in-memory finding draft.

A detector never writes to the database. It returns drafts; `findings.py` persists them
inside one transaction so the deferred `finding_requires_evidence` trigger can enforce
"no finding without evidence" at commit. That split is deliberate -- it makes it
impossible for a detector to emit a bare assertion by forgetting a write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from cadence.simulate import Savings


@dataclass(slots=True)
class EvidenceDraft:
    kind: str  # code_range | log_span | run_history | timing_series | counterfactual
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    run_ids: list[int] = field(default_factory=list)
    payload: dict[str, Any] | None = None


@dataclass(slots=True)
class FindingDraft:
    kind: str
    module: str
    severity: int          # 1..5
    confidence: float      # 0..1
    dedupe_key: str
    title: str
    detector_version: str
    evidence: list[EvidenceDraft]
    suggested_action: str | None = None
    savings: Savings | None = None
    # Wall-clock seconds saved are not billed seconds: unblocking one job on the critical
    # path saves elapsed time for that job alone, while cancelling a superseded run saves
    # every job it was running concurrently. Detectors declare the multiplier; the cost
    # model applies it.
    parallel_jobs: float = 1.0

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError(
                f"finding {self.kind!r} has no evidence; every finding must cite evidence"
            )
        if not 1 <= self.severity <= 5:
            raise ValueError(f"severity {self.severity} out of range for {self.kind!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence {self.confidence} out of range for {self.kind!r}")


class Detector(Protocol):
    """Detectors are pure: repo state in, drafts out, no I/O of their own."""

    id: str
    version: str

    def run(self, ctx: Any) -> list[FindingDraft]: ...
