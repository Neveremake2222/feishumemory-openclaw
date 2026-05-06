"""Fixture-file adapter for feishu-ingest MVP 1."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterator

from memory_engine.models import Scope

from feishu_ingest.adapters.base import FeishuSourceAdapter
from feishu_ingest.models import FeishuEvent

logger = logging.getLogger(__name__)


class FixtureAdapter(FeishuSourceAdapter):
    """Read Feishu events from a JSONL fixture file."""

    def __init__(self, fixture_path: str | Path) -> None:
        self.fixture_path = Path(fixture_path)

    def stream_events(self) -> Iterator[FeishuEvent]:
        if not self.fixture_path.exists():
            raise FileNotFoundError(f"Fixture file not found: {self.fixture_path}")

        with self.fixture_path.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                stripped = raw.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                try:
                    event = _parse_line(stripped, lineno)
                    if event is not None:
                        yield event
                except Exception as exc:
                    logger.error("Failed to parse fixture line %d: %s", lineno, exc)
                    continue


def _parse_line(raw: str, lineno: int) -> FeishuEvent:
    """Parse one JSONL line into a FeishuEvent."""
    data = json.loads(raw)

    scope_val = data.get("scope", "user")
    scope = _resolve_scope(scope_val)

    return FeishuEvent(
        source_type=data["source_type"],
        source_ref=data["source_ref"],
        source_url=data.get("source_url"),
        actors=data.get("actors", []),
        timestamp=data["timestamp"],
        content=data["content"],
        scope=scope,
        project_id=data.get("project_id"),
        task_id=data.get("task_id"),
        user_id=data.get("user_id"),
        payload=data.get("payload", {}),
        content_hash=data.get("content_hash"),
        source_version=data.get("source_version"),
    )


def _resolve_scope(value: str) -> Scope:
    try:
        return Scope(value)
    except ValueError:
        return Scope.USER
