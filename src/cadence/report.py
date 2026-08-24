"""F1 — the audit report: the cold-pitch artifact.

Self-contained HTML, no auth, no JS dependencies, shareable as a single file. This is what
gets linked from an issue comment to a maintainer who has never heard of Cadence, so it
has to make its case in fifteen seconds and then let every number be checked.

The design contract lives in `docs/FRONTEND.md`. Two rules from it are enforced here
rather than left to the CSS:

  * **No element without a job.** Every row is evidence, an action on evidence, or the
    number that ranks them.
  * **Replay and projection must be distinguishable without reading.** Replay renders as
    a solid bar and a point value; projection renders hatched with a range. That is
    `PRODUCT.md` §6 made visible.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass

from cadence.cost import render_saving
from cadence.detectors.base import FindingDraft
from cadence.detectors.context import AuditContext


def _mmss(seconds: float | None) -> str:
    if not seconds:
        return "—"
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


@dataclass(slots=True)
class ReportModel:
    """Everything the template needs, resolved — no logic in the markup."""

    repo: str
    is_private: bool
    runs: int
    workflow: str | None
    coverage: float
    wall_seconds: float
    critical_path_seconds: float | None
    floor_seconds: float
    queue_bound: bool
    findings: list[FindingDraft]
    replay_total: float
    headline: str

    @property
    def well_mapped(self) -> bool:
        """Below this the wall-clock-vs-floor gap is unmeasured work, not recoverable
        time, so the waterfall must not imply otherwise."""
        return self.coverage >= 0.8

    @property
    def recoverable_pct(self) -> float:
        if self.wall_seconds <= 0:
            return 0.0
        return min(100.0, (self.replay_total / self.wall_seconds) * 100.0)


def build_model(
    ctx: AuditContext, summary: dict | None, drafts: list[FindingDraft]
) -> ReportModel:
    replay_total = sum(
        d.savings.seconds_per_run for d in drafts if d.savings and d.savings.basis.is_replay
    )
    summary = summary or {}
    return ReportModel(
        repo=ctx.full_name,
        is_private=ctx.is_private,
        runs=summary.get("runs", len(ctx.runs)),
        workflow=summary.get("workflow"),
        coverage=summary.get("coverage", 0.0),
        wall_seconds=summary.get("wall_seconds", 0.0),
        critical_path_seconds=summary.get("critical_path_seconds"),
        floor_seconds=summary.get("floor_seconds", 0.0),
        queue_bound=summary.get("queue_bound", False),
        findings=drafts,
        replay_total=replay_total,
        headline=render_saving(ctx.cost, replay_total) if replay_total > 0 else "",
    )


# ─────────────────────────────────────────────────────────────── markup

_CSS = """
:root {
  --ground:#E8E8E3; --panel:#F1F1EE; --rule:#C6CAC6; --rule-hi:#A9AFAC;
  --text:#1F272D; --muted:#5E6A71; --spent:#8B979D; --recover:#0E7C86;
  --warn:#8A5A00;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ground:#131A1F; --panel:#1A232A; --rule:#2B3843; --rule-hi:#3C4C58;
    --text:#C7D1D8; --muted:#7B8994; --spent:#55666F; --recover:#3FD0D8;
    --warn:#D9A441;
  }
}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--text);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto;padding:0 clamp(16px,4vw,40px)}
.mono{font-family:ui-monospace,"SF Mono","Cascadia Mono",Menlo,monospace}
.rail{border-bottom:1px solid var(--rule);font-size:.6875rem;letter-spacing:.08em;
  text-transform:uppercase;color:var(--muted)}
.rail__in{display:flex;flex-wrap:wrap;gap:8px 20px;align-items:baseline;padding:14px 0}
.rail__mark{color:var(--text);font-weight:600;letter-spacing:.16em}
.hero{padding:clamp(28px,5vw,48px) 0 8px}
.eyebrow{font-size:.6875rem;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);margin:0 0 18px}
.verdict{font-weight:600;font-size:clamp(1.5rem,5vw,2.5rem);line-height:1.12;
  letter-spacing:-.02em;margin:0 0 10px;text-wrap:balance}
.verdict em{font-style:normal;color:var(--recover)}
.sub{margin:0;color:var(--muted);max-width:62ch}
.note{margin:16px 0 0;padding:10px 14px;border:1px solid var(--rule);border-radius:2px;
  background:var(--panel);color:var(--muted);font-size:.8125rem;max-width:70ch}
.note strong{color:var(--warn)}
.chart{margin:32px 0 0;overflow-x:auto}
.chart__in{min-width:480px}
.bars{display:grid;gap:6px}
.row{display:grid;grid-template-columns:104px 1fr;align-items:center;gap:0}
.row__k{font-size:.6875rem;color:var(--muted);text-align:right;padding-right:12px;
  text-transform:uppercase;letter-spacing:.06em}
.track{position:relative;height:16px}
.bar{position:absolute;top:3px;height:10px;border-radius:1px;background:var(--spent)}
.bar--floor{background:var(--recover)}
.hatch{position:absolute;top:3px;height:10px;border-radius:1px;
  border:1px solid var(--recover);
  background-image:repeating-linear-gradient(-45deg,var(--recover) 0 1px,transparent 1px 5px)}
.val{position:absolute;top:-1px;font-size:.6875rem;color:var(--muted);padding-left:8px}
.val--rec{color:var(--recover)}
.ledger{padding:34px 0 0}
.lhead{display:flex;align-items:baseline;gap:12px;border-top:1px solid var(--rule);
  padding-top:14px}
.ltitle{font-size:.6875rem;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);margin:0}
.f{border-bottom:1px solid var(--rule);padding:20px 0;display:grid;gap:14px;
  grid-template-columns:1fr minmax(150px,auto)}
.f__claim{font-size:1.125rem;font-weight:500;margin:0 0 8px;line-height:1.35;
  text-wrap:balance}
.f__body{margin:0 0 12px;color:var(--muted);max-width:64ch;white-space:pre-wrap}
.ev{display:flex;flex-wrap:wrap;gap:6px}
.ev__i{font-size:.6875rem;color:var(--muted);border:1px solid var(--rule);
  border-radius:2px;padding:3px 8px;background:var(--panel)}
.f__side{text-align:right;display:flex;flex-direction:column;gap:5px;align-items:flex-end}
.save{font-weight:600;font-size:1.25rem;letter-spacing:-.02em;color:var(--recover);
  line-height:1.15}
.save--range{font-size:1rem}
.basis{font-size:.6875rem;letter-spacing:.04em;color:var(--muted);
  display:inline-flex;align-items:center;gap:6px}
.sw{width:22px;height:9px;border-radius:1px;flex:none}
.sw--replay{background:var(--recover)}
.sw--proj{border:1px solid var(--recover);
  background-image:repeating-linear-gradient(-45deg,var(--recover) 0 1px,transparent 1px 5px)}
.empty{padding:40px 0;text-align:left}
.empty h2{font-size:1.5rem;margin:0 0 8px;font-weight:600}
.method{margin:32px 0 56px;padding:16px 18px;border:1px solid var(--rule);
  border-radius:2px;background:var(--panel);color:var(--muted);font-size:.8125rem;
  max-width:72ch}
.method h2{font-size:.6875rem;letter-spacing:.1em;text-transform:uppercase;
  margin:0 0 8px;color:var(--text)}
.method p{margin:0 0 8px}.method p:last-child{margin:0}
@media (max-width:640px){.f{grid-template-columns:1fr}
  .f__side{text-align:left;align-items:flex-start}.row{grid-template-columns:78px 1fr}}
"""


def _waterfall(m: ReportModel) -> str:
    """Actual vs floor, with the recoverable region hatched.

    Withheld entirely when mapping coverage is low: the gap would read as recoverable
    time when it is really jobs we could not place.
    """
    if not m.well_mapped or m.wall_seconds <= 0:
        return ""
    floor_pct = min(100.0, (m.floor_seconds / m.wall_seconds) * 100.0)
    gap = max(0.0, m.wall_seconds - m.floor_seconds)
    rows = [
        '<div class="row"><span class="row__k">actual</span><span class="track">'
        f'<span class="bar" style="left:0;width:100%"></span>'
        f'<span class="val mono" style="left:100%">{_mmss(m.wall_seconds)}</span>'
        "</span></div>",
        '<div class="row"><span class="row__k">floor</span><span class="track">'
        f'<span class="bar bar--floor" style="left:0;width:{floor_pct:.2f}%"></span>'
        f'<span class="hatch" style="left:{floor_pct:.2f}%;right:0"></span>'
        f'<span class="val val--rec mono" style="left:{floor_pct:.2f}%">'
        f"{_mmss(m.floor_seconds)}</span>"
        "</span></div>",
    ]
    label = (
        f'<p class="sub mono" style="margin-top:10px;font-size:.75rem">'
        f"hatched = {_mmss(gap)} between the slowest single job and the whole run"
        f"</p>"
    )
    return (
        '<div class="chart"><div class="chart__in"><div class="bars">'
        + "".join(rows)
        + f"</div>{label}</div></div>"
    )


def _finding_row(d: FindingDraft) -> str:
    s = d.savings
    if s is None:
        save_html = '<span class="save save--range">config bug</span>'
        basis_html = '<span class="basis"><span class="sw sw--replay"></span>config</span>'
    elif s.basis.is_replay:
        save_html = (
            f'<span class="save mono">{_mmss(s.seconds_per_run)}'
            '<span class="basis" style="font-weight:400"> /run</span></span>'
        )
        basis_html = (
            '<span class="basis"><span class="sw sw--replay"></span>'
            f"replay · measured (n={s.n_runs})</span>"
        )
    else:
        save_html = (
            f'<span class="save save--range mono">{_mmss(s.low)}–{_mmss(s.high)}'
            '<span class="basis" style="font-weight:400"> /run</span></span>'
        )
        basis_html = (
            '<span class="basis"><span class="sw sw--proj"></span>projection · estimated</span>'
        )

    chips = []
    for ev in d.evidence:
        if ev.kind == "code_range" and ev.file_path:
            loc = f"{ev.file_path.split('/')[-1]}"
            if ev.line_start:
                loc += f" : {ev.line_start}"
            chips.append(f'<span class="ev__i mono">{_e(loc)}</span>')
        elif ev.kind == "run_history" and ev.run_ids:
            chips.append(f'<span class="ev__i mono">{len(ev.run_ids)} runs cited</span>')
        elif ev.kind == "timing_series":
            n = len((ev.payload or {}).get("durations", []))
            chips.append(f'<span class="ev__i mono">{n} timings</span>')
        elif ev.kind == "counterfactual":
            chips.append('<span class="ev__i mono">counterfactual</span>')

    detail = (d.savings.detail if d.savings else "") or ""
    action = d.suggested_action or ""
    body = "\n".join(x for x in (detail, action) if x)

    return f"""<article class="f">
  <div>
    <h3 class="f__claim">{_e(d.title)}</h3>
    <p class="f__body mono" style="font-size:.8125rem">{_e(body)}</p>
    <div class="ev">{''.join(chips)}</div>
  </div>
  <div class="f__side">{save_html}{basis_html}
    <span class="basis">confidence {d.confidence:.0%}</span>
  </div>
</article>"""


def render_html(m: ReportModel) -> str:
    if m.findings:
        pct = f"{m.recoverable_pct:.0f}%" if m.replay_total > 0 else ""
        verdict = (
            f"{_mmss(m.wall_seconds)} to a signal."
            + (f"<br><em>{_mmss(m.replay_total)} of it is recoverable.</em>" if m.replay_total > 0
               else "")
        )
        sub = (
            f"{len(m.findings)} finding{'s' if len(m.findings) != 1 else ''} across "
            f"{m.runs} runs of <span class='mono'>{_e(m.workflow or 'this repo')}</span>."
            + (f" Measured recoverable: <strong>{pct}</strong> of median wall clock — "
               f"{_e(m.headline)}." if m.replay_total > 0 else "")
        )
        ledger = (
            '<section class="ledger"><div class="lhead">'
            '<h2 class="ltitle">Findings</h2>'
            f'<span class="ltitle mono">{len(m.findings)} · ranked by time recovered</span>'
            "</div>" + "".join(_finding_row(d) for d in m.findings) + "</section>"
        )
    else:
        verdict = "No recoverable waste found."
        sub = (
            f"Analysed {m.runs} runs of "
            f"<span class='mono'>{_e(m.workflow or 'this repo')}</span>. "
            f"Median wall clock {_mmss(m.wall_seconds)}. This pipeline is tight."
        )
        ledger = ""

    coverage_note = ""
    if not m.well_mapped:
        coverage_note = (
            f'<p class="note"><strong>Partial analysis.</strong> Only '
            f"{m.coverage:.0%} of observed jobs map to this workflow's configuration — "
            f"reusable workflows rename their jobs, so we cannot place them in the graph. "
            f"Scheduling analysis is withheld rather than reported against an incomplete "
            f"picture.</p>"
        )

    queue_note = ""
    if m.queue_bound:
        queue_note = (
            '<p class="note"><strong>Queue-bound.</strong> Runners wait longer than jobs '
            "run, so adding parallelism would make this pipeline slower, not faster.</p>"
        )

    return f"""<title>Cadence — {_e(m.repo)}</title>
<style>{_CSS}</style>
<header class="rail"><div class="wrap rail__in">
  <span class="rail__mark">Cadence</span><span>/</span><span>audit</span><span>·</span>
  <span class="mono">{_e(m.repo)}</span>
  <span style="margin-left:auto">{m.runs} runs read · no install</span>
</div></header>
<main class="wrap">
  <section class="hero">
    <p class="eyebrow mono">{'private' if m.is_private else 'public'} ·
      {_e(m.workflow or '')}</p>
    <h1 class="verdict">{verdict}</h1>
    <p class="sub">{sub}</p>
    {coverage_note}{queue_note}
    {_waterfall(m)}
  </section>
  {ledger}
  <section class="method">
    <h2>How these numbers were made</h2>
    <p>Read-only, from {m.runs} workflow runs through the public GitHub API. No install,
      no agent, no change to your repository.</p>
    <p><strong>Replay</strong> figures are arithmetic over timings we observed — the run is
      recomputed with the change applied. <strong>Projection</strong> figures estimate a
      state we never observed, so they are given as a range. The two are never added
      together, and only replay figures appear in any total.</p>
  </section>
</main>"""


def write_report(m: ReportModel, path) -> None:
    from pathlib import Path

    Path(path).write_text(render_html(m), encoding="utf-8")


def report_json(m: ReportModel) -> str:
    """Machine-readable twin, for the read API and the eval harness."""
    return json.dumps(
        {
            "repo": m.repo,
            "runs": m.runs,
            "workflow": m.workflow,
            "coverage": round(m.coverage, 4),
            "wall_seconds": m.wall_seconds,
            "floor_seconds": m.floor_seconds,
            "queue_bound": m.queue_bound,
            "replay_seconds_per_run": round(m.replay_total, 2),
            "recoverable_pct": round(m.recoverable_pct, 2),
            "findings": [
                {
                    "kind": d.kind,
                    "title": d.title,
                    "severity": d.severity,
                    "confidence": d.confidence,
                    "basis": d.savings.basis.value if d.savings else None,
                    "seconds_per_run": (
                        round(d.savings.seconds_per_run, 2) if d.savings else None
                    ),
                    "evidence_kinds": sorted({e.kind for e in d.evidence}),
                }
                for d in m.findings
            ],
        },
        indent=2,
    )
