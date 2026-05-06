"""Project Resolver — resolve project_id for OpenClaw adapter.

When OpenClaw calls /recall or /write without an explicit project_id,
this module attempts to resolve it from available signals:
    1. Explicit project_id (always wins)
    2. Workspace ID
    3. Current working directory (repo path)
    4. Open file paths

Uses ProjectRegistry (loaded from config/project_registry.json) as the
machine-readable project identity source.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_registry = None


def _get_registry():
    """Get ProjectRegistry. Prefer singleton, then try lazy-load from file."""
    global _registry

    # 1. Check singleton (set by daemon startup or tests)
    from feishu_ingest.project_registry import ProjectRegistry
    instance = ProjectRegistry.get_instance()
    if instance:
        return instance

    # 2. Lazy-load from file (only once)
    if _registry is None:
        try:
            registry_path = os.environ.get(
                "PROJECT_REGISTRY_PATH",
                "config/project_registry.json",
            )
            if os.path.exists(registry_path):
                _registry = ProjectRegistry.load(registry_path)
                logger.info("project_resolver loaded registry from %s", registry_path)
        except Exception:
            logger.debug("project_resolver: registry not available")
    return _registry


def resolve_project_id(
    *,
    explicit: str | None = None,
    workspace_id: str | None = None,
    cwd: str | None = None,
    open_files: list[str] | None = None,
) -> str | None:
    """Resolve project_id from available signals (priority order).

    Returns None if no project can be determined.
    """
    # 1. Explicit always wins
    if explicit:
        return explicit

    registry = _get_registry()
    if not registry:
        return None

    # 2. Workspace ID
    if workspace_id:
        pid = registry.project_for_workspace(workspace_id)
        if pid:
            return pid

    # 3. CWD repo path
    if cwd:
        pid = registry.project_for_repo_path(cwd)
        if pid:
            return pid

    # 4. Open files path
    if open_files:
        for f in open_files:
            pid = registry.project_for_repo_path(f)
            if pid:
                return pid

    return None


def reset() -> None:
    """Reset cached registry — useful for testing."""
    global _registry
    _registry = None
