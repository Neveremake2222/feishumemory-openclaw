"""Scope inference for Feishu events."""

from __future__ import annotations

import re
from typing import Any

from memory_engine.models import Scope

from feishu_ingest.models import FeishuEvent

_PROJECT_CHAT_MAP: dict[str, str] = {}
_DOC_PROJECT_MAP: dict[str, str] = {}

_PROJECT_TAG_RE = re.compile(r"#project:(\S+)")
_TASK_TAG_RE = re.compile(r"#task:(\S+)")


def configure_project_chat(mapping: dict[str, str]) -> None:
    """Set chat-id → project-id mapping for scope inference."""
    global _PROJECT_CHAT_MAP
    _PROJECT_CHAT_MAP = dict(mapping)


def configure_doc_project(mapping: dict[str, str]) -> None:
    """Set doc-id → project-id mapping for scope inference."""
    global _DOC_PROJECT_MAP
    _DOC_PROJECT_MAP = dict(mapping)


def infer_scope(event: FeishuEvent) -> Scope:
    """Deterministic scope inference from Feishu event metadata.

    Rules (design doc §8):
      - message + direct → USER
      - message + group + #project:<id> → PROJECT
      - message + group + #task:<id> → SESSION
      - message + group + known chat_id → PROJECT
      - doc/wiki + known doc_id → PROJECT
      - doc/wiki + personal → USER
      - fallback → USER
    """
    if event.source_type == "message":
        # Check for explicit tags in content
        if _PROJECT_TAG_RE.search(event.content):
            return Scope.PROJECT
        if _TASK_TAG_RE.search(event.content):
            return Scope.SESSION
        # Check configured chat map via source_ref prefix or payload chat_id
        chat_id = event.payload.get("chat_id")
        if chat_id and chat_id in _PROJECT_CHAT_MAP:
            return Scope.PROJECT
        # Check project registry
        if chat_id:
            from feishu_ingest.project_registry import ProjectRegistry
            registry = ProjectRegistry.get_instance()
            if registry and registry.project_for_chat(chat_id):
                return Scope.PROJECT
        # Direct message with user scope stays USER
        if event.scope == Scope.USER:
            return Scope.USER
        # Already has explicit scope from fixture
        return event.scope

    if event.source_type in ("doc", "wiki"):
        doc_id = event.source_ref
        if doc_id in _DOC_PROJECT_MAP:
            return Scope.PROJECT
        # Check project registry for doc/wiki
        from feishu_ingest.project_registry import ProjectRegistry
        registry = ProjectRegistry.get_instance()
        if registry:
            if registry.project_for_doc(doc_id) or registry.project_for_wiki(doc_id):
                return Scope.PROJECT
        # Personal doc
        if event.scope == Scope.USER:
            return Scope.USER
        # Org policy doc
        if event.scope == Scope.ORGANIZATION:
            return Scope.ORGANIZATION
        return Scope.USER

    return Scope.USER


def infer_project_id(event: FeishuEvent) -> str | None:
    """Extract project_id from event metadata, configured mappings, or project registry."""
    # Explicit project_id on event
    if event.project_id:
        return event.project_id
    # Tag in content
    m = _PROJECT_TAG_RE.search(event.content)
    if m:
        return m.group(1)
    # Legacy chat map (backwards compat)
    chat_id = event.payload.get("chat_id")
    if chat_id and chat_id in _PROJECT_CHAT_MAP:
        return _PROJECT_CHAT_MAP[chat_id]
    # Registry chat_id lookup
    from feishu_ingest.project_registry import ProjectRegistry
    registry = ProjectRegistry.get_instance()
    if registry:
        if chat_id:
            pid = registry.project_for_chat(chat_id)
            if pid:
                return pid
        # Registry doc/wiki lookup
        pid = registry.project_for_doc(event.source_ref)
        if pid:
            return pid
        pid = registry.project_for_wiki(event.source_ref)
        if pid:
            return pid
    # Legacy doc map
    if event.source_ref in _DOC_PROJECT_MAP:
        return _DOC_PROJECT_MAP[event.source_ref]
    return None


def infer_task_id(event: FeishuEvent) -> str | None:
    """Extract task_id from event metadata or content tags."""
    if event.task_id:
        return event.task_id
    m = _TASK_TAG_RE.search(event.content)
    if m:
        return m.group(1)
    return None
