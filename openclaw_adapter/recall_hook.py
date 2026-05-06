"""Recall hook — query memory-engine and return Markdown injection snippet."""

from __future__ import annotations

import logging
from pathlib import Path

from openclaw_adapter.engine_client import DirectEngineClient
from openclaw_adapter.injection import format_injection
from openclaw_adapter.types import OpenClawContext

logger = logging.getLogger(__name__)

_DEFAULT_DB = "memory_engine.sqlite3"


class RecallOutput:
    """Container for recall results with metadata."""

    __slots__ = ("injection_md", "results", "query")

    def __init__(self, injection_md: str, results: list[dict], query: str) -> None:
        self.injection_md = injection_md
        self.results = results
        self.query = query


def recall(
    ctx: OpenClawContext,
    limit: int = 5,
    db_path: str = _DEFAULT_DB,
    client: DirectEngineClient | None = None,
    memory_layer: str | None = None,
) -> RecallOutput:
    """Recall relevant memories from memory-engine.

    Returns a RecallOutput with Markdown snippet, raw results, and query.
    Fail-open: returns empty RecallOutput on any error.
    """
    try:
        if client is not None:
            return _do_recall(ctx, limit, client, memory_layer)

        with DirectEngineClient(db_path) as c:
            return _do_recall(ctx, limit, c, memory_layer)
    except Exception:
        logger.exception("recall failed")
        return RecallOutput(injection_md="", results=[], query="")


def _do_recall(ctx: OpenClawContext, limit: int, client: DirectEngineClient, memory_layer: str | None = None) -> RecallOutput:
    query = _build_query(ctx)
    results = client.recall(
        query=query,
        user_id=ctx.user_id,
        project_id=ctx.project_id,
        task_id=ctx.task_id,
        limit=limit,
        memory_layer=memory_layer,
    )
    filtered = _filter_already_recalled(results, ctx.already_recalled_ids)
    injection_md = format_injection(filtered)
    return RecallOutput(injection_md=injection_md, results=filtered, query=query)


def _build_query(ctx: OpenClawContext) -> str:
    """Build a recall query from OpenClaw context."""
    parts = [ctx.latest_message]
    if ctx.current_task:
        parts.append(f"task: {ctx.current_task}")
    if ctx.project_id:
        parts.append(f"project: {ctx.project_id}")
    if ctx.open_files:
        names = [Path(f).stem for f in ctx.open_files]
        parts.append(f"files: {', '.join(names)}")
    return " | ".join(parts)


def _filter_already_recalled(
    results: list[dict],
    already_recalled_ids: tuple[str, ...],
) -> list[dict]:
    """Remove memories recalled in the last 30 min."""
    id_set = set(already_recalled_ids)
    if not id_set:
        return results
    return [r for r in results if str(r.get("id", r.get("uuid", ""))) not in id_set]
