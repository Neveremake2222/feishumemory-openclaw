"""Feishu event model — adapter-local DTO, bridges Feishu data to memory-engine."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

from memory_engine.models import Scope, SourceEvent


@dataclass(frozen=True)
class FeishuEvent:
    """Normalised Feishu event — adapter-local DTO, not a native engine type."""

    source_type: Literal["message", "doc", "wiki"]
    source_ref: str
    source_url: str | None
    actors: list[str]
    timestamp: str
    content: str
    scope: Scope
    project_id: str | None = None
    task_id: str | None = None
    user_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    content_hash: str | None = None
    source_version: str | None = None

    @property
    def _content_hash(self) -> str:
        if self.content_hash:
            return self.content_hash
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def to_source_event(self) -> "SourceEvent":
        """Bridge from FeishuEvent to the engine's SourceEvent."""
        from memory_engine.models import SourceEvent, SourceType

        # Map feishu source types to engine SourceType enum values
        source_type_map = {
            "feishu_message": SourceType.MESSAGE.value,
            "feishu_doc": SourceType.DOC.value,
            "feishu_wiki": SourceType.DOC.value,
        }
        engine_source_type = source_type_map.get(self.source_type, self.source_type)

        return SourceEvent(
            source_type=engine_source_type,
            source_ref=self.source_ref,
            actors=self.actors,
            timestamp=self.timestamp,
            content=self.content,
            scope=self.scope.value,
            payload=self._sanitised_payload(),
        )

    def _sanitised_payload(self) -> dict[str, Any]:
        """Strip raw API response blobs; keep metadata only."""
        return {
            "source_url": self.source_url,
            "content_hash": self._content_hash,
            "source_version": self.source_version,
            "actors": self.actors,
            **{k: v for k, v in self.payload.items() if k in ("snippet", "doc_title", "chat_title", "msg_type")},
        }
