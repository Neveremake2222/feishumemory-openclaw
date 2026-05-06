"""Shared helpers for Feishu/Lark message adapter normalization."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from memory_engine.models import Scope

from feishu_ingest.models import FeishuEvent
from feishu_ingest.scope import infer_project_id, infer_scope, infer_task_id


def normalize_message_content(raw: Any) -> str:
    """Extract human-readable text from common Feishu message content shapes."""
    if isinstance(raw, dict):
        return str(raw.get("text") or raw.get("content") or raw.get("title") or "")
    if not isinstance(raw, str):
        return str(raw) if raw is not None else ""

    stripped = raw.strip()
    if not stripped:
        return ""
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped

    if isinstance(parsed, dict):
        return str(parsed.get("text") or parsed.get("content") or parsed.get("title") or stripped)

    # Feishu rich text / code block format: [{"tag": "code_block", "text": "..."}]
    # May also be wrapped in another list: [[{...}]]
    if isinstance(parsed, list):
        parts: list[str] = []
        for item in parsed:
            if isinstance(item, list):
                for sub in item:
                    if isinstance(sub, dict):
                        text = sub.get("text") or sub.get("content") or ""
                        if text:
                            parts.append(str(text))
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
        return "\n".join(parts) if parts else stripped

    return stripped


def with_inferred_scope(event: FeishuEvent) -> FeishuEvent:
    """Return an event whose scope/project/task fields follow shared inference rules."""
    return replace(
        event,
        scope=infer_scope(event),
        project_id=infer_project_id(event),
        task_id=infer_task_id(event),
    )
