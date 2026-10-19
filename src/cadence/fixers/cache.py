"""Add cache inputs to an existing npm/pip setup action, or explain why not.

No new action, install command or permission is introduced. Source bytes and dependency
files must come from the same complete, commit-pinned snapshot. Edits are line insertions;
both their semantic effect and the detector's cached predicate are verified afterwards.
"""

from __future__ import annotations

import io
import math
import re
import shlex
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from cadence.detectors.base import FindingDraft
from cadence.detectors.cache import DETECTOR_VERSION, cache_state, install_step
from cadence.fixers.base import Diff, FixResult
from cadence.source import PreviewSource
from cadence.suppress import collect
from cadence.workflow import Job, Step, Workflow, parse_workflow

# A preview that proposes a PR should clear a higher bar than a report finding (5s).
# Provisional product threshold, not a measured maintainer preference (CAVEATS 63).
MIN_SAVINGS_LOW_SECONDS = 30.0
_DEPLOY = re.compile(r"deploy|release|publish|production|\bprod\b", re.I)
_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")
_NPM = re.compile(r"npm (ci|install)(?:\s+--(?:ignore-scripts|no-audit|no-fund|prefer-offline))*$")
_CACHE_ENV = {"PIP_CACHE_DIR", "PIP_NO_CACHE_DIR", "PIP_CONFIG_FILE", "NPM_CONFIG_CACHE"}
_PREFIX_ACTIONS = {"actions/setup-node", "actions/setup-python"}


class CacheFixer:
    id = "cache.setup"
    version = 1
    applies_to = "no_dependency_cache"

    def __init__(self, source: PreviewSource):
        self.source = source

    def confidence(self, workflow: Workflow, finding: FindingDraft) -> float:
        return 0.9

    def preview(self, workflow: Workflow, content: str, finding: FindingDraft) -> FixResult:
        if finding.kind != self.applies_to:
            return FixResult(None, f"applies to {self.applies_to}, not {finding.kind}")
        source = self.source
        if not source.inventory.pinned or not source.inventory.complete:
            return FixResult(None, "repository files are not complete and commit-pinned")
        if (workflow.path not in source.inventory.paths
                or source.workflows.get(workflow.path) != content):
            return FixResult(None, "workflow bytes do not match the repository-file snapshot")
        fresh = parse_workflow(workflow.path, content)
        if fresh.parse_error:
            return FixResult(None, "workflow does not parse")
        workflow = fresh  # all decisions use the verified bytes, not a caller's stale parse
        if ".cadenceignore" in source.inventory.paths and source.ignore_file is None:
            return FixResult(None, "repository suppression policy was not read")
        prefix = f"{self.applies_to}:{workflow.path}:"
        key = finding.dedupe_key.removeprefix(prefix)
        if not finding.dedupe_key.startswith(prefix) or key not in workflow.jobs:
            return FixResult(None, "finding does not identify a job in this workflow")
        if finding.detector_version != DETECTOR_VERSION:
            return FixResult(None, "finding uses an older detector; re-audit before previewing")
        try:
            suppression = next((r for r in collect(source.workflows, source.ignore_file)
                                if r.matches(finding.kind, finding.dedupe_key)), None)
        except ValueError as exc:
            return FixResult(None, f"suppression policy cannot be read: {exc}")
        if suppression:
            return FixResult(None, f"suppressed by repository policy: {suppression.reason}")
        saving = finding.savings
        if (saving is None or saving.basis.is_replay or not math.isfinite(saving.low)
                or not math.isfinite(saving.high) or saving.high < saving.low
                or saving.low < MIN_SAVINGS_LOW_SECONDS):
            return FixResult(None, "projected lower-bound saving is below 30 seconds per run")
        job = workflow.jobs[key]
        install = install_step(job)
        if install is None:
            return FixResult(None, "job has no isolated dependency-install step")
        if not any(e.kind == "code_range" and e.file_path == workflow.path
                   and e.line_start == install.line and (e.payload or {}).get("job") == key
                   for e in finding.evidence):
            return FixResult(None, "finding evidence does not identify the current install step")
        state = cache_state(job, install, source.package_json, source.actions)
        if state != "uncached":
            return FixResult(None, f"job's cache state is {state}; no cache will be added")

        try:
            doc = YAML().load(io.StringIO(content))
            raw_job = doc["jobs"][key]
            raw_install = raw_job["steps"][install.index]
            problem = _unsafe_context(workflow, job, install, doc, raw_install)
            if problem:
                return FixResult(None, problem)
            plan = _dependencies(install.run or "", source.inventory.paths)
            if isinstance(plan, str):
                return FixResult(None, plan)
            ecosystem, paths = plan
            action = "actions/setup-node" if ecosystem == "npm" else "actions/setup-python"
            setups = [s for s in job.steps if s.index < install.index and s.action == action]
            if len(setups) != 1:
                return FixResult(None, f"requires one existing {action} before the install")
            setup = setups[0]
            manifest = source.actions.get(setup.uses or "")
            if (manifest is None or manifest.using is None
                    or not {"cache", "cache-dependency-path"} <= manifest.inputs):
                return FixResult(None, "setup cache inputs could not be verified at its ref")
            if any(k in setup.with_ for k in ("cache", "cache-dependency-path")):
                return FixResult(None, "setup already declares cache inputs; preserve its choice")
            problem = _checkout_context(job, setup, install)
            if problem:
                return FixResult(None, problem)
            raw_setup = raw_job["steps"][setup.index]
            if raw_setup.get("if") is not None:
                return FixResult(None, "setup action is conditional; cache availability is unknown")
            if any(str(k).upper() in _CACHE_ENV for k in (raw_setup.get("env") or {})):
                return FixResult(None, "setup action overrides the cache environment")
            addition = {"cache": ecosystem, "cache-dependency-path": "\n".join(paths)}
            at, lines = _insertion(content, raw_setup, ecosystem, paths)
            newline = "\r\n" if "\r\n" in content else "\n"
            original = content.splitlines(keepends=True)
            after = "".join(original[:at] + [line + newline for line in lines] + original[at:])
            problem = _verify(doc, workflow, after, key, setup.index, addition, source)
            if problem:
                return FixResult(None, f"verification failed: {problem}")
        except Exception as exc:
            # A strange YAML shape declines; it never yields a partially verified edit.
            return FixResult(None, f"unsupported workflow layout: {str(exc)[:160]}")

        return FixResult(Diff(
            workflow.path, content, after,
            f"Cache {ecosystem} downloads in {key}, keyed on {', '.join(paths)}. "
            "The dependency install still runs on every execution.",
            f"Remove the added cache and cache-dependency-path inputs from {action} in {key}.",
        ))


def _unsafe_context(
    workflow: Workflow, job: Job, install: Step, doc: dict, raw_install: dict,
) -> str | None:
    events = ({workflow.on} if isinstance(workflow.on, str)
              else set(workflow.on or []))
    if events & {"pull_request_target", "workflow_run", "release", "workflow_call"}:
        return "privileged, release or reusable workflow; cache policy needs manual review"
    push = workflow.on.get("push") if isinstance(workflow.on, dict) else None
    if isinstance(push, dict) and any(k in push for k in ("tags", "tags-ignore")):
        return "tag-triggered workflow; cache policy needs manual review"
    if any(_DEPLOY.search(str(v or "")) for v in
           (workflow.path, workflow.name, job.key, job.name)) or job.raw.get("environment"):
        return "deployment or publishing job; cache policy needs manual review"
    for permissions in (doc.get("permissions"), job.raw.get("permissions")):
        if permissions == "write-all" or (isinstance(permissions, dict)
                and any(v == "write" for v in permissions.values())):
            return "workflow or job has write permissions; cache policy needs manual review"
    for raw in (doc, job.raw):
        if (raw.get("defaults") or {}).get("run", {}).get("working-directory"):
            return "default working-directory is set; only workspace-root installs are supported"
    if raw_install.get("working-directory") or raw_install.get("if") is not None:
        return "install has a working-directory or condition; workspace context is uncertain"
    for raw in (doc, job.raw, raw_install):
        if any(str(k).upper() in _CACHE_ENV for k in (raw.get("env") or {})):
            return "custom cache environment is set; preserve its policy"
    if "self-hosted" in job.runs_on:
        return "self-hosted runner may retain downloads already; cache policy needs manual review"
    return None


def _checkout_context(job: Job, setup: Step, install: Step) -> str | None:
    checkouts = [s for s in job.steps if s.index < install.index and s.action == "actions/checkout"]
    if len(checkouts) != 1 or checkouts[0].index >= setup.index:
        return "requires one root checkout before setup so dependency files exist before caching"
    checkout = checkouts[0]
    if any(k in checkout.with_ for k in
           ("path", "repository", "ref", "sparse-checkout", "filter")):
        return "checkout selects another ref, repository or file layout; source match is uncertain"
    raw_checkout = job.raw["steps"][checkout.index]
    if raw_checkout.get("if") is not None:
        return "checkout is conditional; dependency files may not exist before setup"
    # Commands/custom actions can move, generate or replace dependency files after the
    # checkout. First version supports the direct checkout -> setup -> install shape.
    for step in job.steps:
        if (step.index < install.index and step is not checkout
                and (step.run or step.action not in _PREFIX_ACTIONS)):
            return "intervening command or action can change dependency files; manual review needed"
    return None


def _path(value: str, files: frozenset[str]) -> str:
    if (not _PATH.fullmatch(value) or value.startswith("/") or ".." in value.split("/")):
        raise ValueError("dependency path is not a literal workspace-relative file")
    path = value.removeprefix("./")
    if path not in files:
        raise ValueError(f"dependency file {path!r} is not present at this revision")
    return path


def _dependencies(script: str, files: frozenset[str]) -> tuple[str, list[str]] | str:
    script = script.strip()
    if _NPM.fullmatch(script):
        if "package.json" not in files:
            return "npm install has no committed root package.json"
        lock = next((p for p in ("npm-shrinkwrap.json", "package-lock.json") if p in files), None)
        return ("npm", [lock]) if lock else "npm caching requires a committed npm lockfile"
    # Shell evaluation and directory changes are deliberately outside this first fixer.
    if any(c in script for c in "$`;&|<>"):
        return "install command is dynamic or compound; dependency paths are uncertain"
    commands = re.sub(r"\\\s*\n", " ", script).splitlines()
    dependencies: set[str] = set()
    for command in commands:
        if not command.strip() or command.lstrip().startswith("#"):
            continue
        tokens = shlex.split(command)
        if tokens[:2] == ["pip", "install"]:
            args = tokens[2:]
        elif len(tokens) >= 4 and re.fullmatch(r"python[\d.]*", tokens[0]) \
                and tokens[1:4] == ["-m", "pip", "install"]:
            args = tokens[4:]
        else:
            return "only literal npm or pip dependency installs are supported"
        pos = 0
        while pos < len(args):
            arg = args[pos]
            if arg in {"--group"} or arg.startswith("--group="):
                if arg == "--group":
                    pos += 1
                    if pos >= len(args):
                        return "pip dependency group name is missing"
                    group = args[pos]
                else:
                    group = arg.split("=", 1)[1]
                if not re.fullmatch(r"[A-Za-z0-9_-]+", group):
                    return "only literal dependency groups in the root pyproject are supported"
                dependencies.add(_path("pyproject.toml", files))
            elif arg in {"-r", "--requirement", "-c", "--constraint"}:
                pos += 1
                if pos >= len(args):
                    return "pip requirement/constraint argument is missing its path"
                dependencies.add(_path(args[pos], files))
            elif arg.startswith(("--requirement=", "--constraint=")):
                dependencies.add(_path(arg.split("=", 1)[1], files))
            elif arg.startswith(("-r", "-c")) and len(arg) > 2:
                dependencies.add(_path(arg[2:], files))
            elif arg in {"-e", "--editable", "--upgrade", "-U", "--pre",
                         "--prefer-binary", "--no-build-isolation", "--disable-pip-version-check"}:
                pass
            elif arg == "." or re.fullmatch(r"\.\[[A-Za-z0-9_,.-]+\]", arg):
                project = [p for p in ("pyproject.toml", "setup.cfg", "setup.py") if p in files]
                if not project:
                    return "local pip project has no committed dependency manifest"
                dependencies.update(project)
            elif not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.=!-]*(\[[A-Za-z0-9_,.-]+\])?", arg):
                return "pip arguments include an unsupported path, option or cache override"
            pos += 1
    return ("pip", sorted(dependencies)) if dependencies else \
        "pip install does not identify committed requirements or a local project manifest"


def _insertion(
    content: str, step: CommentedMap, ecosystem: str, paths: list[str],
) -> tuple[int, list[str]]:
    if step.fa.flow_style() or step.anchor.value or step.merge:
        raise ValueError("setup step uses flow style, anchors or YAML merges")
    source_lines = content.splitlines()
    if "with" in step:
        mapping = step["with"]
        if (not isinstance(mapping, CommentedMap) or not mapping or mapping.fa.flow_style()
                or mapping.anchor.value or mapping.merge):
            raise ValueError("setup inputs are not an independent block mapping")
        line, column = step.lc.key("with")
        if not re.fullmatch(r"\s*with:\s*(#.*)?", source_lines[line]):
            raise ValueError("with header is not a plain block mapping")
        indent = " " * mapping.lc.col
        at = line + 1
        header: list[str] = []
    else:
        line, column = step.lc.key("uses")
        at = line + 1
        header = [" " * column + "with:"]
        indent = " " * (column + 2)
    lines = header + [indent + f"cache: {ecosystem}"]
    if len(paths) == 1:
        lines.append(indent + f"cache-dependency-path: '{paths[0]}'")
    else:
        lines.append(indent + "cache-dependency-path: |-")
        lines.extend(indent + "  " + p for p in paths)
    return at, lines


def _plain(value: Any) -> Any:
    # Rebuild EACH branch, breaking alias sharing before changing the expected target.
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _verify(
    before: dict, workflow: Workflow, after: str, key: str, index: int,
    addition: dict[str, str], source: PreviewSource,
) -> str | None:
    after_wf = parse_workflow(workflow.path, after)
    if after_wf.parse_error:
        return "result is not valid workflow YAML"
    expected = _plain(before)
    expected["jobs"][key]["steps"][index].setdefault("with", {}).update(addition)
    actual = _plain(YAML().load(io.StringIO(after)))
    if actual != expected:
        return "edit changed semantics outside the two cache inputs"
    job = after_wf.jobs[key]
    install = install_step(job)
    if install is None or cache_state(
        job, install, source.package_json, source.actions,
    ) != "cached":
        return "detector would still consider the job uncached"
    return None
