"""Adapter-level deduplication — prevents repeated writes within a session."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass

from openclaw_adapter.types import OpenClawEvent, WriteDecision


@dataclass
class _DedupEntry:
    content_hash: str
    memory_type: str
    timestamp: float
    memory_ids: list[int]


class AdapterDedupe:
    """Session + 10min TTL dedup for adapter writes.

    Two dedup levels:
    1. Same session + same content_hash within 10 min → skip
    2. Periodic cleanup of entries older than 30 min
    """

    _global: dict[str, _DedupEntry] = {}
    _lock = threading.Lock()

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    def should_skip(self, event: OpenClawEvent, decision: WriteDecision) -> bool:
        content = (event.user_message or "") + (event.tool_output or "")
        h = _stable_hash(content)

        with self._lock:
            key = f"{self._session_id}:{h}"
            entry = self._global.get(key)
            if entry and (time.time() - entry.timestamp) < 600:
                return True
            return False

    def record(self, event: OpenClawEvent, decision: WriteDecision, memory_ids: list[int]) -> None:
        content = (event.user_message or "") + (event.tool_output or "")
        h = _stable_hash(content)
        key = f"{self._session_id}:{h}"

        with self._lock:
            self._global[key] = _DedupEntry(
                content_hash=h,
                memory_type=decision.memory_type or "",
                timestamp=time.time(),
                memory_ids=memory_ids,
            )
            if len(self._global) > 10_000:
                cutoff = time.time() - 1800
                self._global = {
                    k: v for k, v in self._global.items() if v.timestamp > cutoff
                }


def _stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
