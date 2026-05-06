"""Evidence builder for Feishu events."""

from __future__ import annotations

from typing import Any

from feishu_ingest.models import FeishuEvent


def build_evidence(event: FeishuEvent) -> list[dict[str, Any]]:
    """Build evidence list from a FeishuEvent (design doc §7).

    Each evidence dict contains source metadata and a sanitised snippet.
    Raw API payloads are NOT included — only metadata keys.
    """
    snippet = event.content[:200]
    return [
        {
            "source_type": f"feishu_{event.source_type}",
            "source_ref": event.source_ref,
            "source_url": event.source_url,
            "actors": event.actors,
            "timestamp": event.timestamp,
            "snippet": snippet,
            "content_hash": event._content_hash,
            "source_version": event.source_version,
        }
    ]
