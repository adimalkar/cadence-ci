"""Immutable repository inputs for fix previews, saved durably for offline evaluation.

An absent file in a complete tree is evidence. A failed/truncated lookup is not. Workflows,
the root package manifest and local composite actions are read at the tree's commit SHA.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from cadence.actions import ActionIndex, ActionManifest, resolve_actions
from cadence.detectors.cache import KNOWN_ACTIONS, install_step
from cadence.detectors.context import RootPackageJson
from cadence.models import Repo
from cadence.workflow import Step, parse_workflow

SHA = re.compile(r"^[0-9a-f]{40}$")
CACHE_DIR = Path("data/corpus")


class SourceUnavailable(ValueError):
    """Cannot establish a complete, immutable source for a preview."""


@dataclass(frozen=True, slots=True)
class FileInventory:
    ref: str
    paths: frozenset[str]
    complete: bool = False

    @property
    def pinned(self) -> bool:
        return bool(SHA.fullmatch(self.ref))


@dataclass(slots=True)
class PreviewSource:
    repo: str
    inventory: FileInventory
    workflows: dict[str, str]
    package_json: RootPackageJson = field(default_factory=RootPackageJson)
    actions: ActionIndex = field(default_factory=dict)
    ignore_file: str | None = None

    def save(self, directory: Path = CACHE_DIR) -> Path:
        """Commit-addressed, versioned artifact with atomic replacement."""
        if not self.inventory.pinned or not self.inventory.complete:
            raise SourceUnavailable("cannot save an incomplete or unpinned source")
        target = directory / self.repo.replace("/", "__") / f"{self.inventory.ref}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1, "repo": self.repo, "ref": self.inventory.ref,
            "paths": sorted(self.inventory.paths), "workflows": self.workflows,
            "package_json": asdict(self.package_json),
            "ignore_file": self.ignore_file,
            "actions": {
                use: ({"using": m.using, "inputs": sorted(m.inputs),
                       "steps": [asdict(s) for s in m.steps]} if m else None)
                for use, m in self.actions.items()
            },
        }
        # Interrupted writes leave the previous snapshot readable.
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(target)
        return target

    @classmethod
    def load(cls, path: Path) -> PreviewSource:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not SHA.fullmatch(data.get("ref", "")):
            raise SourceUnavailable("unsupported or unpinned source snapshot")
        if ".cadenceignore" in data["paths"] and data.get("ignore_file") is None:
            raise SourceUnavailable("snapshot does not include the repository's suppression policy")
        actions = {
            use: (ActionManifest(m["using"], [Step(**s) for s in m["steps"]],
                                 frozenset(m["inputs"])) if m else None)
            for use, m in data["actions"].items()
        }
        return cls(
            data["repo"], FileInventory(data["ref"], frozenset(data["paths"]), complete=True),
            data["workflows"], RootPackageJson(**data["package_json"]), actions,
            data.get("ignore_file"),
        )


class SourceProvider(Protocol):
    async def fetch_file_inventory(self, repo: Repo, ref: str | None = None) -> FileInventory: ...

    async def fetch_text_file(
        self, repo: Repo, path: str, *, ref: str | None = None,
    ) -> str | None: ...


async def capture_source(
    provider: SourceProvider, repo: Repo, *, ref: str | None = None,
    workflow_path: str | None = None,
) -> PreviewSource:
    inventory = await provider.fetch_file_inventory(repo, ref)
    if not inventory.pinned or not inventory.complete:
        raise SourceUnavailable("repository file inventory is incomplete or not commit-pinned")
    paths = sorted(p for p in inventory.paths if p.startswith(".github/workflows/")
                   and p.count("/") == 2 and p.endswith((".yml", ".yaml")))
    if workflow_path:
        if workflow_path not in paths:
            raise SourceUnavailable(f"workflow {workflow_path!r} is not in this revision")
        paths = [workflow_path]
    workflows: dict[str, str] = {}
    for path in paths:
        text = await provider.fetch_text_file(repo, path, ref=inventory.ref)
        if text is None:
            raise SourceUnavailable(f"could not read {path!r} at the pinned revision")
        workflows[path] = text

    package_json = RootPackageJson.from_text(None)
    if "package.json" in inventory.paths:
        try:
            text = await provider.fetch_text_file(repo, "package.json", ref=inventory.ref)
            package_json = (RootPackageJson.from_text(text) if text is not None
                            else RootPackageJson())
        except Exception:
            package_json = RootPackageJson()  # unknown, never absent

    # Read setup manifests too: a SHA pin must actually declare the inputs we add.
    known = KNOWN_ACTIONS - {"actions/setup-node", "actions/setup-python"}
    wanted: dict[str, None] = {}
    for path, content in workflows.items():
        wf = parse_workflow(path, content)
        for job in wf.jobs.values():
            install = install_step(job)
            if install:
                for step in job.steps:
                    if step.index < install.index and step.uses and step.action not in known:
                        wanted.setdefault(step.uses, None)
    actions = await resolve_actions(provider, repo, list(wanted), ref=inventory.ref, known=known)
    ignore = None
    if ".cadenceignore" in inventory.paths:
        ignore = await provider.fetch_text_file(repo, ".cadenceignore", ref=inventory.ref)
        if ignore is None:
            raise SourceUnavailable("could not read the repository's suppression policy")
    return PreviewSource(f"{repo.owner}/{repo.name}", inventory, workflows,
                         package_json, actions, ignore)
