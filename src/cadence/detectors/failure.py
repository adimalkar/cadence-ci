"""Where failures start — the first-failing-step index (`FEATURE_CANDIDATES.md` F8).

For every failed job, the first step with a failing conclusion. Aggregated, that answers
*"why does CI fail here?"* at the level people actually ask it, using nothing but step
conclusions: no log parsing, no classifier, no gold labels.

It is also Phase 3's first stage. Building it here means the flake work starts from a
measured distribution rather than an assumed one.

**The pitch in `FEATURE_CANDIDATES.md` did not survive measurement.** F8 was written around
a split between "your tests" and "infrastructure, not you", illustrated with `npm ci` at 9%
and `actions/checkout` at 5%. Measured against the corpus on 2026-09-06 — 6,085 failed jobs
across 51 repos — recognisable infrastructure steps account for **2–5% of first failures,
not 14%**, and carry about 2.5 of 495 wasted hours. Failures overwhelmingly start at the
project's own test and build commands.

So this detector does not lead with a split it cannot support. It reports **concentration** —
which step starts most of your failures, and what share — and reports the infrastructure
share only as a secondary number, always alongside the fraction of steps it could classify
at all. Withholding a claim is cheaper than making a wrong one.

It carries no savings figure. The minutes a failed job spent were really spent, and no
config change recovers them: the fix is to make the step stop failing, which is work we
cannot specify or price. `long_tail_step` makes the same trade for the same reason.
"""

from __future__ import annotations

import re
from collections import Counter

from cadence.detectors.base import EvidenceDraft, FindingDraft
from cadence.detectors.context import AuditContext

DETECTOR_ID = "flake.first_failing_step"
DETECTOR_VERSION = "first_failing_step@1"

# Below this the distribution is noise: a repo with 8 failures spread over 6 steps has
# nothing for us to point at. 41 of 49 corpus repos clear 20.
MIN_FAILURES = 20
# A flat distribution is a true observation and a useless finding. If the top step is not
# at least this share, there is no single place to look and we stay silent. Corpus median
# top-step share is 38%.
MIN_TOP_SHARE = 0.25

# Deterministic allowlist, in the spirit of F7: recognise what we recognise, refuse to
# guess at the rest, and publish the coverage so nobody reads an unclassified majority as
# "not infrastructure". Ordered — first match wins.
_INFRA_PATTERNS: tuple[tuple[str, str], ...] = (
    ("checkout", r"\bactions/checkout\b|^Checkout\b"),
    ("runner_setup", r"^(Set up job|Complete job|Initialize containers|Stop containers)$"),
    ("toolchain", r"\bactions/setup-|\bsetup-(python|node|go|java|dotnet|ruby)\b"),
    ("cache", r"\bactions/cache\b|\bcache@"),
    ("dependencies", r"\b(npm ci|npm install|yarn install|pnpm install|pip install"
                     r"|poetry install|uv sync|bundle install|go mod download|cargo fetch"
                     r"|apt-get install|brew install)\b"),
    ("container", r"\bdocker (build|pull|push)\b|\bbuildx\b|^Pull \b"),
    ("artifact", r"\b(upload|download)-artifact\b"),
    ("post_step", r"^Post\s"),
)


def classify_step(name: str) -> str | None:
    """Infrastructure category for a step name, or None when we cannot tell.

    None is the honest answer for most steps — a `run:` block containing a project's own
    command is not classifiable from its name, and pretending otherwise is how a tool
    starts reporting confident nonsense.
    """
    for label, pattern in _INFRA_PATTERNS:
        if re.search(pattern, name, re.IGNORECASE):
            return label
    return None


class FirstFailingStepDetector:
    id = DETECTOR_ID
    version = DETECTOR_VERSION

    def run(self, ctx: AuditContext) -> list[FindingDraft]:
        failures = ctx.failures
        if len(failures) < MIN_FAILURES:
            return []

        by_step: Counter[str] = Counter(f.step_name for f in failures)
        top_step, top_n = by_step.most_common(1)[0]
        total = len(failures)
        share = top_n / total
        if share < MIN_TOP_SHARE:
            return []

        infra_n = sum(1 for f in failures if classify_step(f.step_name) is not None)
        classified_share = infra_n / total
        top_kind = classify_step(top_step)

        at_top = [f for f in failures if f.step_name == top_step]
        seconds_before = sorted(f.job_seconds for f in at_top)
        median_burn = seconds_before[len(seconds_before) // 2] if seconds_before else 0.0

        distribution = [
            {"step": name, "failures": n, "share": round(n / total, 4),
             "infrastructure": classify_step(name)}
            for name, n in by_step.most_common(10)
        ]

        return [
            FindingDraft(
                kind="first_failing_step",
                module="flake",
                # A diagnostic, not a defect. It earns attention by being specific, not by
                # claiming something is broken.
                severity=2,
                # A count of step conclusions, not an inference from them. The only
                # uncertainty is whether the first failing step is the *cause*, which is
                # true for an ordinary job and false for one with `continue-on-error`.
                confidence=0.90,
                dedupe_key=f"first_failing_step:{top_step}",
                title=(
                    f"{share:.0%} of failed jobs first fail at `{_trim(top_step)}` "
                    f"({top_n} of {total} failures, {ctx.window_days}d)"
                ),
                detector_version=DETECTOR_VERSION,
                suggested_action=_action(top_step, top_kind, share, median_burn),
                # No savings. The minutes were really spent and no config change recovers
                # them; the fix is to stop the step failing, which we cannot price.
                savings=None,
                evidence=[
                    EvidenceDraft(
                        kind="run_history",
                        run_ids=sorted({f.run_id for f in at_top})[:50],
                        payload={"note": f"runs whose first failing step was {top_step!r}"},
                    ),
                    EvidenceDraft(
                        kind="timing_series",
                        payload={
                            "failed_jobs": total,
                            "distinct_first_failing_steps": len(by_step),
                            "top_step": top_step,
                            "top_step_failures": top_n,
                            "top_step_share": round(share, 4),
                            "top_step_infrastructure": top_kind,
                            "median_seconds_before_failure": round(median_burn, 1),
                            # Published on every finding so the infrastructure share is
                            # never read as complete. Typically low: most failures start
                            # at a project's own command, which no allowlist can name.
                            "classification_coverage": round(classified_share, 4),
                            "infrastructure_failures": infra_n,
                            "distribution": distribution,
                        },
                    ),
                ],
            )
        ]


def _trim(name: str, limit: int = 60) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


def _action(step: str, kind: str | None, share: float, median_burn: float) -> str:
    where = (
        f"`{_trim(step)}` is where {share:.0%} of your failed jobs stop. "
        f"They run a median of {_mmss(median_burn)} before doing so."
    )
    if kind is not None:
        return (
            f"{where} This is a **{kind.replace('_', ' ')}** step rather than your own "
            "code, so the failures are likely environmental — a flaky network, a moved "
            "upstream artifact, or a cache miss. Those are usually fixable with a retry "
            "or a pin, and fixing them removes failures nobody learns anything from."
        )
    return (
        f"{where} We could not match it to a known infrastructure step, so treat it as "
        "your own build or test command. Worth checking whether these are genuine "
        "regressions or one test failing repeatedly — the run list is attached."
    )


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"
