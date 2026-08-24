"""Parse GitHub Actions workflow YAML into something detectors can reason about.

Two things make this more than a `yaml.safe_load`:

  * **Line numbers.** Every finding cites a `code_range`, so each job and step carries the
    line it was declared on. ruamel's round-trip loader attaches `.lc` to every node;
    plain PyYAML would throw that away and leave findings unable to point at anything.
  * **Job identity.** The YAML job *key* is not the runtime job *name* -- a job can
    override it with `name:`, and matrix legs get a ` (ubuntu-latest, 20)` suffix appended
    at runtime. Mapping config to observed timings has to account for both, which is what
    `runtime_names` exists for.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

# `on:` is the one key YAML 1.1 turns into a boolean (on/off/yes/no are booleans there).
# ruamel defaults to 1.2 where this is fixed, but workflows in the wild are parsed by
# GitHub as YAML 1.2 too -- we normalise both spellings rather than assume.
_ON_KEYS = ("on", True)

_EXPR = re.compile(r"\$\{\{(.+?)\}\}", re.DOTALL)
# Mirrors the stripping ingest applies when deriving `name_base`.
_MATRIX_SUFFIX = re.compile(r"^(?P<base>.+?)\s*\([^()]*\)\s*$")


@dataclass(slots=True)
class Step:
    index: int
    line: int
    uses: str | None = None
    run: str | None = None
    name: str | None = None
    with_: dict[str, Any] = field(default_factory=dict)

    @property
    def action(self) -> str | None:
        """`actions/cache` from `actions/cache@v4` -- version-independent identity."""
        if not self.uses:
            return None
        return self.uses.split("@", 1)[0].strip()


@dataclass(slots=True)
class Job:
    key: str
    line: int
    name: str | None = None
    needs: list[str] = field(default_factory=list)
    runs_on: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    matrix: dict[str, list[Any]] = field(default_factory=dict)
    services: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    # `jobs.<key>.uses:` — this job calls a reusable workflow instead of running steps.
    # GitHub names the resulting runtime jobs `<caller> / <inner>`, which matches nothing
    # in the calling file. Left unhandled this is 25.7% of all observed jobs.
    uses: str | None = None

    @property
    def is_reusable_call(self) -> bool:
        return self.uses is not None

    @property
    def has_dynamic_name(self) -> bool:
        """`name:` is entirely an expression, e.g. `${{ matrix.name || matrix.python }}`.

        Nothing static survives, so the runtime name (`3.10`, `Windows`, `PyPy`) is
        unpredictable from config and can never be matched literally. Common in Python
        matrix projects.
        """
        if not self.name:
            return False
        return not _EXPR.sub("", self.name).strip()

    @property
    def runtime_names(self) -> set[str]:
        """Names that identify this config job *exactly* — matched before any stripping.

        Runtime name is `name:` if set, else the key.
        """
        names = {self.key}
        if self.name:
            # A templated name ("test ${{ matrix.os }}") cannot be matched literally;
            # keep the static prefix, which is what survives at runtime.
            static = _EXPR.sub("", self.name).strip()
            if static:
                names.add(static)
        return names

    @property
    def runtime_names_stripped(self) -> set[str]:
        """The same names with a trailing parenthetical removed — what `name_base` holds.

        Ingest cannot tell a matrix suffix from a hand-written name: GitHub renders both
        as `foo (bar)`. So `name: "cargo test (linux)"` is stored with
        `name_base = "cargo test"`, identical to what a real matrix leg would produce.
        Matching on this is therefore *ambiguous* -- two config jobs can share a stripped
        name -- and is only used as a fallback after an exact match fails.
        """
        out: set[str] = set()
        for name in self.runtime_names:
            match = _MATRIX_SUFFIX.match(name)
            out.add(match.group("base").strip() if match else name)
        return out

    @property
    def matrix_leg_count(self) -> int:
        n = 1
        for values in self.matrix.values():
            if isinstance(values, list) and values:
                n *= len(values)
        return n


@dataclass(slots=True)
class Workflow:
    path: str
    name: str | None
    jobs: dict[str, Job]
    concurrency: dict[str, Any] | None
    on: dict[str, Any] | list[Any] | str | None
    parse_error: str | None = None

    @property
    def cancel_in_progress(self) -> bool:
        """True only when concurrency actually cancels.

        `cancel-in-progress` may be an expression string; anything other than a literal
        true is treated as not-cancelling, because we cannot evaluate it and claiming a
        saving we cannot verify is worse than staying quiet.
        """
        if not self.concurrency:
            return False
        return self.concurrency.get("cancel-in-progress") is True

    def job_for_runtime_name(self, name: str | None, name_base: str | None = None) -> Job | None:
        """Resolve an observed job to its config job.

        Exact (verbatim) match first: it is unambiguous, and it is the only thing that
        can separate `cargo test (linux)` from `cargo test (linux, release)` -- two
        distinct jobs whose stripped names are identical.

        The stripped fallback exists for genuine matrix legs, where the runtime suffix
        really was generated. It is only accepted when exactly one config job claims
        that stripped name; a tie means we cannot tell which job we are looking at, and
        guessing would silently attribute one job's timings to another.
        """
        if name:
            for job in self.jobs.values():
                if name in job.runtime_names:
                    return job

        if name_base:
            matches = [j for j in self.jobs.values() if name_base in j.runtime_names_stripped]
            if len(matches) == 1:
                return matches[0]

        # Reusable-workflow call: GitHub renders `<caller> / <inner>`. The caller is one
        # node in *this* graph -- the inner workflow's own jobs are that node's internal
        # structure, not siblings of it -- so every `X / *` job resolves to caller X.
        for candidate in (name, name_base):
            if not candidate or " / " not in candidate:
                continue
            caller = candidate.split(" / ", 1)[0].strip()
            for job in self.jobs.values():
                if caller in job.runtime_names or caller in job.runtime_names_stripped:
                    return job

        # Last resort, by elimination: a job whose `name:` is a pure expression cannot be
        # matched literally, so an otherwise-unmatched observation belongs to it -- but
        # only when exactly one such job exists in the workflow. More than one and we
        # cannot tell them apart, so we decline rather than guess.
        dynamic = [j for j in self.jobs.values() if j.has_dynamic_name]
        if len(dynamic) == 1:
            return dynamic[0]
        return None


def _line_of(node: Any, key: Any, default: int = 1) -> int:
    """1-based line for `key` within a ruamel mapping, or `default`."""
    lc = getattr(node, "lc", None)
    if lc is None:
        return default
    try:
        data = lc.data
        if data and key in data:
            return int(data[key][0]) + 1
    except (AttributeError, TypeError, KeyError, IndexError):
        pass
    line = getattr(lc, "line", None)
    return int(line) + 1 if line is not None else default


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _parse_steps(raw_steps: Any) -> list[Step]:
    steps: list[Step] = []
    if not isinstance(raw_steps, list):
        return steps
    for i, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            continue
        steps.append(
            Step(
                index=i,
                line=_line_of(raw_steps, i),
                uses=raw.get("uses"),
                run=raw.get("run"),
                name=raw.get("name"),
                with_=dict(raw.get("with") or {}),
            )
        )
    return steps


def _parse_matrix(strategy: Any) -> dict[str, list[Any]]:
    if not isinstance(strategy, dict):
        return {}
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict):
        return {}
    out: dict[str, list[Any]] = {}
    for key, values in matrix.items():
        # `include`/`exclude` are matrix modifiers, not dimensions -- counting them as
        # dimensions would inflate the leg count.
        if key in ("include", "exclude"):
            continue
        if isinstance(values, list):
            out[str(key)] = list(values)
    return out


def parse_workflow(path: str, content: str) -> Workflow:
    """Parse one workflow file. Never raises -- a malformed workflow yields a Workflow
    with `parse_error` set, so one bad file cannot abort a repo's whole audit."""
    yaml = YAML()
    yaml.preserve_quotes = True
    try:
        doc = yaml.load(io.StringIO(content))
    except (YAMLError, Exception) as exc:  # noqa: BLE001 - third-party parser, any error
        return Workflow(path=path, name=None, jobs={}, concurrency=None, on=None,
                        parse_error=str(exc)[:500])

    if not isinstance(doc, dict):
        return Workflow(path=path, name=None, jobs={}, concurrency=None, on=None,
                        parse_error="workflow root is not a mapping")

    on_value = None
    for key in _ON_KEYS:
        if key in doc:
            on_value = doc[key]
            break

    concurrency = doc.get("concurrency")
    if isinstance(concurrency, str):
        # Shorthand `concurrency: group-name` -- a group with no cancel-in-progress.
        concurrency = {"group": concurrency}
    elif not isinstance(concurrency, dict):
        concurrency = None

    jobs: dict[str, Job] = {}
    raw_jobs = doc.get("jobs")
    if isinstance(raw_jobs, dict):
        for key, raw in raw_jobs.items():
            if not isinstance(raw, dict):
                continue
            services = raw.get("services")
            jobs[str(key)] = Job(
                key=str(key),
                line=_line_of(raw_jobs, key),
                name=raw.get("name"),
                needs=_as_list(raw.get("needs")),
                runs_on=_as_list(raw.get("runs-on")),
                steps=_parse_steps(raw.get("steps")),
                matrix=_parse_matrix(raw.get("strategy")),
                services=list(services.keys()) if isinstance(services, dict) else [],
                raw=dict(raw),
                uses=raw.get("uses"),
            )

    return Workflow(
        path=path,
        name=doc.get("name"),
        jobs=jobs,
        concurrency=concurrency,
        on=on_value,
    )
