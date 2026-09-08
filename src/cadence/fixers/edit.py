"""Editing workflow YAML without wrecking the diff.

The single most likely cause of a well-founded fix PR being closed unmerged is a diff that
touches two hundred unrelated lines. `PHASE_2_FIX_PRS.md` makes the round-trip a ship
criterion rather than a nicety for that reason.

Round-tripping through a plain loader destroys comments, key order, quoting style, blank
lines and anchors. `ruamel.yaml` in round-trip mode preserves most of that, but not all of
it, and the gap is where an unreviewable diff comes from. So this module does two things:

1. **`round_trip`** — load and dump through ruamel, configured as closely to GitHub's own
   formatting conventions as the library allows.
2. **`is_faithful`** — ask whether a given file survives that round trip byte-identically,
   *before* any fix is applied. A file that does not survive is one we decline to edit.

That second function is the important one. It converts "our editor might mangle this file"
from a risk into a precondition we can test: `preview()` returns `None` for a file that
would not round-trip, which is exactly the "declining to fix is always correct" rule from
the fixer contract.

**We never edit by re-serialising a file we could not round-trip.** For those, a fix is
still reportable as a suggested diff the user applies by hand — it just does not become a
pull request.

**Measured on the corpus, 2026-09-07: 666 of 928 real workflow files (71.8%) round-trip
byte-identically.** Comfortably past the 200-file ship criterion, and the 28% that fail do
so for three reasons, none of them fixable by configuration:

- ruamel normalises flow-style spacing — `{ name: x }` becomes `{name: x}`,
  `[ "main" ]` becomes `["main"]`
- it strips trailing whitespace on otherwise-blank lines
- files whose sequence indent differs from the configured one get every list re-indented

A per-file indent detector was tried and **made things much worse — 1.8%** — because
ruamel's `sequence`/`offset` semantics are not what they appear. Recorded so nobody spends
the afternoon again.

**The real lesson is that re-serialisation is the wrong mechanism for additive fixes.**
Adding a `concurrency:` block or a cache step is a text insertion, and inserting lines into
the source preserves 100% of files by construction. This module then becomes the safety net
it should always have been, rather than the editing mechanism. See `PHASE_2_FIX_PRS.md`.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

# GitHub Actions workflows are 2-space indented with 2-space sequence indent and a 2-space
# offset, which is what `actions/starter-workflows` emits and what almost every real file
# in the corpus uses. Matching it keeps a re-serialised file close to its original.
_SEQ_INDENT = 4
_SEQ_OFFSET = 2
_MAP_INDENT = 2

# Long `run:` blocks and URLs must not be re-wrapped. ruamel wraps at 80 by default, which
# would reflow lines nobody asked us to touch -- the exact unreviewable-diff failure.
_NO_WRAP = 1 << 30


def _editor() -> YAML:
    y = YAML()  # round-trip mode is the default and is what preserves comments
    y.preserve_quotes = True
    y.width = _NO_WRAP
    y.indent(mapping=_MAP_INDENT, sequence=_SEQ_INDENT, offset=_SEQ_OFFSET)
    # Keep `key:` with no value as `key:` rather than `key: null`.
    y.representer.add_representer(type(None), _represent_none)
    return y


def _represent_none(representer, _data):
    return representer.represent_scalar("tag:yaml.org,2002:null", "")


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """What happened when a file was loaded and dumped unchanged."""

    ok: bool
    output: str | None
    reason: str | None = None

    @property
    def faithful(self) -> bool:
        return self.ok and self.reason is None


def round_trip(content: str) -> RoundTrip:
    """Load and dump `content` with no edits. Byte-identical output means we may edit it.

    Returns a reason rather than raising: an unparseable or lossy workflow is a normal
    thing to encounter across fifty strangers' repositories, not an error in us.
    """
    y = _editor()
    try:
        data = y.load(content)
    except YAMLError as exc:
        return RoundTrip(ok=False, output=None, reason=f"unparseable: {_brief(exc)}")
    if data is None:
        return RoundTrip(ok=False, output=None, reason="empty document")

    buf = io.StringIO()
    try:
        y.dump(data, buf)
    except YAMLError as exc:
        return RoundTrip(ok=False, output=None, reason=f"undumpable: {_brief(exc)}")

    out = buf.getvalue()
    if out == content:
        return RoundTrip(ok=True, output=out)
    return RoundTrip(ok=True, output=out, reason=_describe_drift(content, out))


def is_faithful(content: str) -> bool:
    """Can we re-serialise this file without changing anything we were not asked to?"""
    return round_trip(content).faithful


def _describe_drift(before: str, after: str) -> str:
    """Say *how* the round trip changed the file, so a decline is diagnosable.

    A bare "not byte-identical" tells a maintainer nothing about whether the editor is
    broken or the file is unusual, and this runs against repositories we cannot debug
    interactively.
    """
    b, a = before.splitlines(), after.splitlines()
    if len(b) != len(a):
        return f"line count changed: {len(b)} → {len(a)}"
    for i, (x, y) in enumerate(zip(b, a, strict=True), start=1):
        if x != y:
            return f"line {i} changed: {x.strip()[:48]!r} → {y.strip()[:48]!r}"
    # Same lines, different bytes: trailing newline or line endings.
    if before.endswith("\n") != after.endswith("\n"):
        return "trailing newline changed"
    return "whitespace or line endings changed"


def _brief(exc: Exception) -> str:
    return " ".join(str(exc).split())[:120]
