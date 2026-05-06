"""Project Registry 鈥?machine-readable project identity source.

Loads config/project_registry.json and provides lookup interfaces for:
    - chat_id   -> project_id   (feishu group chats)
    - doc_id    -> project_id   (feishu documents)
    - wiki_id   -> project_id   (feishu wiki nodes)
    - repo_path -> project_id   (local code repositories)
    - workspace -> project_id   (openclaw workspace)

Usage:
    from feishu_ingest.project_registry import ProjectRegistry

    registry = ProjectRegistry.load("config/project_registry.json")
    ProjectRegistry.configure(registry)

    # Then scope.py infer functions will automatically query it.
    project_id = registry.project_for_chat("oc_xxx")
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectRegistryProject:
    project_id: str
    name: str
    aliases: tuple[str, ...] = ()
    chat_ids: tuple[str, ...] = ()
    doc_ids: tuple[str, ...] = ()
    wiki_ids: tuple[str, ...] = ()
    repo_paths: tuple[str, ...] = ()
    openclaw_workspace_ids: tuple[str, ...] = ()
    default_scope: str = "project"
    status: str = "active"


class ProjectRegistry:
    """Project identity registry.

    Load once, configure once, query everywhere (via ProjectRegistry.get_instance()).
    """

    _instance: ProjectRegistry | None = None

    def __init__(self, projects: list[ProjectRegistryProject]) -> None:
        self._projects: dict[str, ProjectRegistryProject] = {p.project_id: p for p in projects}

        # Inverted maps: id -> project_id
        self._chat_to_project: dict[str, str] = {}
        self._doc_to_project: dict[str, str] = {}
        self._wiki_to_project: dict[str, str] = {}
        self._repo_to_project: dict[str, str] = {}  # resolved absolute path -> project_id
        self._workspace_to_project: dict[str, str] = {}

        for p in projects:
            for cid in p.chat_ids:
                self._chat_to_project[cid] = p.project_id
            for did in p.doc_ids:
                self._doc_to_project[did] = p.project_id
            for wid in p.wiki_ids:
                self._wiki_to_project[wid] = p.project_id
            for rp in p.repo_paths:
                resolved = str(Path(rp).resolve())
                self._repo_to_project[resolved] = p.project_id
            for ws in p.openclaw_workspace_ids:
                self._workspace_to_project[ws] = p.project_id

        self._path: str | None = None

    # -- query interfaces -------------------------------------------------------

    def project_for_chat(self, chat_id: str) -> str | None:
        """Return project_id for a Feishu chat_id, or None if not registered."""
        return self._chat_to_project.get(chat_id)

    def project_for_doc(self, doc_id: str) -> str | None:
        """Return project_id for a Feishu doc_id, or None if not registered."""
        return self._doc_to_project.get(doc_id)

    def project_for_wiki(self, wiki_id: str) -> str | None:
        """Return project_id for a Feishu wiki_id, or None if not registered."""
        return self._wiki_to_project.get(wiki_id)

    def project_for_repo_path(self, path: str | Path) -> str | None:
        """Return project_id for a local repo path, or None if not registered.

        Checks exact match first, then prefix match (so /workspace/agent/feishumemory
        matches /workspace/agent/feishumemory/memory_engine/engine.py).
        """
        p = str(Path(path).resolve())
        if p in self._repo_to_project:
            return self._repo_to_project[p]
        for registered_path, pid in self._repo_to_project.items():
            if p.startswith(registered_path + os.sep):
                return pid
        return None

    def project_for_workspace(self, workspace_id: str) -> str | None:
        """Return project_id for an OpenClaw workspace_id, or None if not registered."""
        return self._workspace_to_project.get(workspace_id)

    def get_all_projects(self) -> list[ProjectRegistryProject]:
        """Return all registered projects."""
        return list(self._projects.values())

    def get_project(self, project_id: str) -> ProjectRegistryProject | None:
        """Return project config by project_id."""
        return self._projects.get(project_id)

    def register_chat(
        self,
        chat_id: str,
        project_id: str | None = None,
        name: str | None = None,
    ) -> str:
        """Register a new chat_id, creating a project if needed. Returns project_id.

        Idempotent: if chat_id is already registered, returns existing project_id.
        """
        existing = self._chat_to_project.get(chat_id)
        if existing:
            return existing

        pid = project_id or f"auto_{chat_id}"

        if pid in self._projects:
            # Append chat_id to existing project
            p = self._projects[pid]
            new_chat_ids = (*p.chat_ids, chat_id)
            self._projects[pid] = ProjectRegistryProject(
                project_id=p.project_id,
                name=p.name,
                aliases=p.aliases,
                chat_ids=new_chat_ids,
                doc_ids=p.doc_ids,
                wiki_ids=p.wiki_ids,
                repo_paths=p.repo_paths,
                openclaw_workspace_ids=p.openclaw_workspace_ids,
                default_scope=p.default_scope,
                status=p.status,
            )
        else:
            # Create new project
            self._projects[pid] = ProjectRegistryProject(
                project_id=pid,
                name=name or f"椋炰功缇よ亰 {chat_id[:12]}",
                chat_ids=(chat_id,),
            )

        self._chat_to_project[chat_id] = pid
        return pid

    def save(self, path: str | Path | None = None) -> None:
        """Persist current in-memory registry state to JSON file.

        Uses atomic write (write to .tmp, then rename) to avoid corruption.
        """
        if path is None:
            if self._path is None:
                raise ValueError("No registry path: pass path or load from a file first")
            path = self._path
        path = Path(path)

        data = {
            "version": 1,
            "projects": [
                {
                    "project_id": p.project_id,
                    "name": p.name,
                    "aliases": list(p.aliases),
                    "chat_ids": list(p.chat_ids),
                    "doc_ids": list(p.doc_ids),
                    "wiki_ids": list(p.wiki_ids),
                    "repo_paths": list(p.repo_paths),
                    "openclaw_workspace_ids": list(p.openclaw_workspace_ids),
                    "default_scope": p.default_scope,
                    "status": p.status,
                }
                for p in self._projects.values()
            ],
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        self._path = str(path)

    # -- singleton lifecycle ----------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "ProjectRegistry":
        """Load registry from a JSON file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Project registry not found: {path}")

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        projects = [
            ProjectRegistryProject(
                project_id=p["project_id"],
                name=p.get("name", p["project_id"]),
                aliases=tuple(p.get("aliases", [])),
                chat_ids=tuple(p.get("chat_ids", [])),
                doc_ids=tuple(p.get("doc_ids", [])),
                wiki_ids=tuple(p.get("wiki_ids", [])),
                repo_paths=tuple(p.get("repo_paths", [])),
                openclaw_workspace_ids=tuple(p.get("openclaw_workspace_ids", [])),
                default_scope=p.get("default_scope", "project"),
                status=p.get("status", "active"),
            )
            for p in data.get("projects", [])
        ]

        if not projects:
            raise ValueError("project_registry.json contains no projects")

        # Check for duplicate keys
        _check_duplicates(projects)

        registry = cls(projects)
        registry._path = str(path)
        return registry
        """Load registry from a JSON file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Project registry not found: {path}")

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        projects = [
            ProjectRegistryProject(
                project_id=p["project_id"],
                name=p.get("name", p["project_id"]),
                aliases=tuple(p.get("aliases", [])),
                chat_ids=tuple(p.get("chat_ids", [])),
                doc_ids=tuple(p.get("doc_ids", [])),
                wiki_ids=tuple(p.get("wiki_ids", [])),
                repo_paths=tuple(p.get("repo_paths", [])),
                openclaw_workspace_ids=tuple(p.get("openclaw_workspace_ids", [])),
                default_scope=p.get("default_scope", "project"),
                status=p.get("status", "active"),
            )
            for p in data.get("projects", [])
        ]

        if not projects:
            raise ValueError("project_registry.json contains no projects")

        # Check for duplicate keys
        _check_duplicates(projects)

        registry = cls(projects)
        registry._path = str(path)
        return registry

    @classmethod
    def get_instance(cls) -> ProjectRegistry | None:
        """Return the configured singleton instance, or None if not configured."""
        return cls._instance

    @classmethod
    def configure(cls, registry: ProjectRegistry) -> None:
        """Set the global singleton (call once at startup)."""
        cls._instance = registry

    @classmethod
    def reset(cls) -> None:
        """Reset singleton 鈥?useful for testing."""
        cls._instance = None


def _check_duplicates(projects: list[ProjectRegistryProject]) -> None:
    """Raise ValueError if any id appears in multiple projects."""
    seen_chat: dict[str, str] = {}
    seen_doc: dict[str, str] = {}
    seen_wiki: dict[str, str] = {}
    seen_workspace: dict[str, str] = {}
    seen_repo: dict[str, str] = {}

    for p in projects:
        for cid in p.chat_ids:
            if cid in seen_chat:
                raise ValueError(f"chat_id '{cid}' belongs to both '{seen_chat[cid]}' and '{p.project_id}'")
            seen_chat[cid] = p.project_id
        for did in p.doc_ids:
            if did in seen_doc:
                raise ValueError(f"doc_id '{did}' belongs to both '{seen_doc[did]}' and '{p.project_id}'")
            seen_doc[did] = p.project_id
        for wid in p.wiki_ids:
            if wid in seen_wiki:
                raise ValueError(f"wiki_id '{wid}' belongs to both '{seen_wiki[wid]}' and '{p.project_id}'")
            seen_wiki[wid] = p.project_id
        for ws in p.openclaw_workspace_ids:
            if ws in seen_workspace:
                raise ValueError(f"workspace_id '{ws}' belongs to both '{seen_workspace[ws]}' and '{p.project_id}'")
            seen_workspace[ws] = p.project_id
        for rp in p.repo_paths:
            resolved = str(Path(rp).resolve())
            if resolved in seen_repo and seen_repo[resolved] != p.project_id:
                raise ValueError(f"repo_path '{rp}' belongs to both '{seen_repo[resolved]}' and '{p.project_id}'")
            seen_repo[resolved] = p.project_id
