"""Read-only preview orchestration; detectors and fixers remain pure."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from cadence.detectors.base import FindingDraft
from cadence.detectors.cache import DependencyCacheDetector
from cadence.detectors.context import AuditContext
from cadence.fixers.base import FixResult
from cadence.fixers.cache import CacheFixer
from cadence.source import PreviewSource, SourceUnavailable
from cadence.suppress import stored_matches


@dataclass(slots=True)
class CachePreview:
    finding: FindingDraft
    result: FixResult

    def as_dict(self) -> dict:
        saving = self.finding.savings
        diff = self.result.diff
        return {
            "finding": self.finding.dedupe_key,
            "detector_version": self.finding.detector_version,
            "evidence": [asdict(e) for e in self.finding.evidence],
            "saving": ({"basis": saving.basis.value, "low_seconds": saving.low,
                        "high_seconds": saving.high, "n_runs": saving.n_runs} if saving else None),
            "declined": self.result.declined,
            "diff": diff.unified if diff else None,
            "summary": diff.summary if diff else None,
            "revert_hint": diff.revert_hint if diff else None,
        }


def preview_cache_fixes(
    ctx: AuditContext, source: PreviewSource, *, job_key: str | None = None,
    suppressed: list[dict] | None = None,
) -> list[CachePreview]:
    if source.repo.casefold() != f"{ctx.owner}/{ctx.name}".casefold():
        raise SourceUnavailable("source belongs to a different repository")
    fixer = CacheFixer(source)
    out: list[CachePreview] = []
    for draft in DependencyCacheDetector().run(ctx):
        if draft.kind != fixer.applies_to:
            continue
        parts = draft.dedupe_key.split(":", 2)
        if len(parts) != 3 or job_key and parts[2] != job_key:
            continue
        workflow = next(wf for wf in ctx.workflows if wf.path == parts[1])
        decision = next((row for row in suppressed or []
                         if _matches_suppression(row, draft)), None)
        if decision:
            result = FixResult(None, f"suppressed: {decision['suppressed_reason']}")
        else:
            result = fixer.preview(workflow, source.workflows.get(workflow.path, ""), draft)
        out.append(CachePreview(draft, result))
    return out


def _matches_suppression(row: dict, finding: FindingDraft) -> bool:
    return stored_matches(row, finding.kind, finding.dedupe_key)
