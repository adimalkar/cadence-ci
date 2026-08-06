# Frontend — Design Direction

**The rule: no element without a job.** This is the product's own spine applied to pixels.
The database refuses a finding that cites no evidence; the interface refuses a component
that serves no decision. If an element is not evidence, an action on evidence, or the
navigation required to reach either, it does not ship.

---

## Who this is for

OSS maintainers and platform engineers. People who live in terminals, read raw logs, and
have been burned by dashboards that assert things they cannot check. They do not want to be
sold to. They want to know whether the number is true.

**The primary screen has one job:** convince a stranger, in fifteen seconds, that these
numbers are real — then let them verify any of them.

That framing kills a lot of conventional UI before it gets drawn. No onboarding carousel.
No "welcome back." No engagement metrics. A maintainer who never logs in again but merged
one fix PR is a total success.

---

## Direction: engineering drawing

Not a metaphor — a lineage. Critical-path method comes from 1950s operations research, and
critical path is literally what Cadence computes. The drafting vocabulary is the correct
notation for what we're showing:

| Convention | What it means here |
|---|---|
| **Dimension line** `\|←— 4:12 —→\|` | The correct way to annotate a span of time |
| **Hatching** (diagonal fill) | Drafting notation for *material to be removed* — i.e. recoverable time |
| **Hairline rules + tick marks** | Time axis, read precisely rather than approximately |
| **Annotation callout** | A finding is a correction pencilled onto someone else's drawing |

Drafting conventions are meaningful by definition. Nothing on a technical drawing is there
because it looked nice. That is exactly the constraint we want.

**Rejected directions and why:** cream-and-serif editorial (says "magazine," not
"instrument"); near-black with one acid accent (the default dev-tool look — and it collides
with CI status colors); broadsheet columns (density without precision).

---

## Color

Every value is reasoned. The critical constraint:

> **Green, red, and amber are reserved for CI status and may never mean anything else.**
> A maintainer reads green as "passed." Using it for "saved time" would collide with the
> one color vocabulary this audience already has burned in.

So the accent for recoverable time must sit outside the status palette entirely. Cyan reads
as *measurement*, not *verdict* — and it is the one hue absent from every CI interface.

| Token | Hex | Role |
|---|---|---|
| `--ground` | `#131A1F` | Deep slate ink. Cool, not pure black. |
| `--panel` | `#1A232A` | Raised surface |
| `--rule` | `#2B3843` | Hairlines, grid, tick marks |
| `--text` | `#C7D1D8` | Body |
| `--muted` | `#7B8994` | Labels, axis numerals |
| `--spent` | `#55666F` | Time you actually spend — deliberately unglamorous |
| **`--recover`** | **`#3FD0D8`** | **Recoverable time. The one bright thing on the page.** |
| `--pass` / `--fail` | `#5FBF7F` / `#E06A5C` | CI status only. Never repurposed. |

Light theme inverts ground/panel and darkens the cyan to `#0E8C93` for contrast; the
semantics never change.

---

## Type

The product is numbers, so the display face is **monospace at large size with tight
tracking** — tabular figures by default, and columns of durations that align without
coaxing. Using mono for display rather than captions is the unusual move, and it is the
right one here: it makes the numerals the personality of the page instead of decorating
around them.

Body is a humanist sans, kept quiet. Type does two jobs and no more.

```
display   ui-monospace, "SF Mono", "Cascadia Mono", Menlo, monospace
          weights 500/600 · tracking -0.02em at large sizes
body      system-ui, -apple-system, "Segoe UI", Roboto, sans-serif
```

Scale is tight — five sizes, no more. A report that needs eight type sizes is a report
that hasn't decided what matters.

---

## The signature element: the dimension-line waterfall

The hero is not a big number with a gradient. It is **the shape of your pipeline, with the
ghost of what it could be drawn underneath.**

```
  actual                                     11:37
  ├──────────────────────────────────────────────┤
  ▓▓▓▓▓▓▓ Determine changes
    ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ cargo fuzz build      8:43  ← critical path
    ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ cargo test ×4
    ▓▓▓▓▓▓▓▓▓▓▓ benchmarks
    … 34 more jobs

  floor (longest single job)          8:43
  ├──────────────────────────────┤▨▨▨▨▨▨▨▨▨▨▨▨▨▨▨
                                  └─ 2:54 recoverable
```

Every engineer who has watched a slow pipeline has stared at this shape. Showing it back to
them with the recoverable region hatched is the entire pitch, and it needs no copy to land.

**Interaction:** hovering a job bar reveals its queue time as a separate leading segment —
because queue and execution are different problems with opposite fixes, and the product
would be lying to merge them.

---

## The visual grammar that carries the product's credibility rule

`PRODUCT.md` §6 says replay and projection must never blend into one number. That rule has
to be *visible*, not just documented:

| | Replay | Projection |
|---|---|---|
| Basis | Arithmetic over observed step timings | Estimate of an unobserved state |
| Bar | Solid fill | Hatched fill |
| Number | Point value — `2:54` | Range — `3:06 – 4:48` |
| Label | `measured across 1,412 runs` | `estimated · 340 comparable repos` |

A user should be able to tell, at a glance and without reading, which claims are arithmetic
and which are inference. This is the single most important detail in the interface.

---

## Screen inventory

Four screens. Each earns its place or it isn't built.

### 1. Audit report — *the cold-pitch artifact*
Public, no auth, shareable URL. This is what gets linked from an issue comment to a
maintainer who has never heard of Cadence. Hero waterfall, then findings ranked by
recovered time. Every row: claim, evidence link, saving, basis, action.

Empty state is a real outcome, not a failure: *"No recoverable waste found across 1,412
runs. Your pipeline is tight."* Say it plainly and stop.

### 2. Findings console — *the working surface*
Authenticated. The list, filtered and sortable. Suppress with a reason. Open a fix PR.
Mark a finding wrong — that button is the continuous eval stream, so it is prominent, not
buried in a menu.

### 3. Trends — *why the install survives month one*
Flaky cost over time, feedback-loop decomposition, duration regressions with the
introducing commit linked. One screen, not a dashboard suite.

### 4. Public calibration — *the trust artifact*
Predicted vs realized savings per rule, replay and projection reported separately.
Published whether or not the number flatters us. Nobody else in this category publishes
their own error rate.

---

## What we will not build

- Vanity metrics — "runs analyzed," "repos scanned," any number that changes nothing
- Sparklines that aren't read, gauges that aren't compared, donuts at all
- A settings page for preferences nobody has expressed
- Onboarding flow — the report *is* the onboarding
- Dark/light toggle as a feature; it follows the system and says nothing about it
- Any chart where a sentence would be clearer

---

## Quality floor

Responsive to mobile — maintainers read GitHub issues on phones, and this link arrives in
one. Visible keyboard focus. `prefers-reduced-motion` respected. Every number reachable by
screen reader as text, not as a chart artifact. Hatching is distinguishable from solid fill
without relying on color alone.

---

## Build sequencing

Frontend is not a phase at the end. The report page is required by week 8 because it *is*
the cold-pitch artifact, so it interleaves. See [`ROADMAP.md`](ROADMAP.md):

| Thread | Weeks | Screen |
|---|---|---|
| F0 — tokens + waterfall component | 8 | The shared primitive |
| F1 — audit report | 8–10 | Screen 1 |
| F2 — findings console | 12–13 | Screen 2 |
| F3 — trends | 21–22 | Screen 3 |
| F4 — public calibration | 24 | Screen 4 |
