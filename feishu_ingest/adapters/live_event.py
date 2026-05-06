"""Live event adapter — streams Feishu IM messages in real-time via lark-cli event bus.

Uses `lark-cli event consume im.message.receive_v1` to subscribe to real-time
message events. The command outputs NDJSON to stdout; this adapter parses each
line, deduplicates, filters, and yields normalised FeishuEvent objects.

Requires: im.message.receive_v1 event enabled in Feishu developer console.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterator

from memory_engine.models import Scope

from feishu_ingest.adapters.base import FeishuSourceAdapter
from feishu_ingest.adapters.lark_cli import BackgroundProcess, CommandRunner, SetupError
from feishu_ingest.adapters.message_utils import normalize_message_content, with_inferred_scope
from feishu_ingest.models import FeishuEvent

logger = logging.getLogger(__name__)

_EVENT_CONSUME_CMD = ["lark-cli", "event", "consume", "im.message.receive_v1", "--quiet"]


class LiveEventAdapter(FeishuSourceAdapter):
    """Live event adapter: streams Feishu IM messages via lark-cli event bus.

    Usage:
        runner = SubprocessCommandRunner()
        adapter = LiveEventAdapter(
            command_runner=runner,
            allowed_chat_ids={"oc_xxx"},  # optional filter
        )
        for event in adapter.stream_events():  # blocks until close()
            ...
    """

    def __init__(
        self,
        *,
        command_runner: CommandRunner,
        allowed_chat_ids: set[str] | None = None,
        reconnect_delay: float = 2.0,
        max_reconnect_attempts: int = 10,
    ) -> None:
        self._runner = command_runner
        self._allowed_chat_ids = allowed_chat_ids
        self._reconnect_delay = reconnect_delay
        self._max_attempts = max_reconnect_attempts
        self._closed = False
        self._seen: set[tuple[str, str]] = set()

    def _check_auth(self) -> None:
        status = self._runner.check_auth()
        if not status.get("authenticated"):
            raise SetupError(f"lark-cli not authenticated: {status}")

    def stream_events(self) -> Iterator[FeishuEvent]:
        """Yield live FeishuEvent objects. Blocks until close() or max reconnect failures."""
        self._check_auth()
        attempts = 0
        while not self._closed:
            try:
                yield from self._consume_stream()
                # Stream ended cleanly (no more events from the process)
                break
            except Exception as exc:
                attempts += 1
                if self._closed or attempts > self._max_attempts:
                    logger.error(
                        "LiveEventAdapter stopped after %d failures: %s", attempts, exc
                    )
                    break
                logger.warning(
                    "LiveEventAdapter reconnecting in %ss (attempt %d): %s",
                    self._reconnect_delay, attempts, exc,
                )
                time.sleep(self._reconnect_delay)

    def _consume_stream(self) -> Iterator[FeishuEvent]:
        proc = self._runner.run_background(list(_EVENT_CONSUME_CMD))
        try:
            for line in proc.stdout_lines():
                if self._closed:
                    break
                if not line.strip():
                    continue
                event = _parse_event(line)
                if event is None:
                    continue
                chat_id = event.payload.get("chat_id", "")
                if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
                    continue
                key = (event.source_ref, event._content_hash)
                if key in self._seen:
                    logger.debug("Skipping duplicate: %s", event.source_ref)
                    continue
                self._seen.add(key)
                yield event
        finally:
            proc.terminate()

    def close(self) -> None:
        """Signal the stream to stop."""
        self._closed = True


def _parse_event(raw: str) -> FeishuEvent | None:
    """Parse a single NDJSON line from lark-cli event consume output."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if data.get("type") != "im.message.receive_v1":
        return None

    msg = data.get("event", {})
    if not msg:
        return None

    message_id = msg.get("message_id") or msg.get("id")
    content = normalize_message_content(msg.get("content", ""))
    sender_id = msg.get("sender_id", "")

    if not message_id or not content:
        return None

    chat_id = msg.get("chat_id", "")

    event = FeishuEvent(
        source_type="message",
        source_ref=message_id,
        source_url=None,
        actors=[sender_id] if sender_id else [],
        timestamp=_normalize_timestamp(msg.get("create_time", "")),
        content=content,
        scope=Scope.USER,
        project_id=None,
        task_id=None,
        user_id=sender_id or None,
        payload={
            "chat_id": chat_id,
            "chat_type": msg.get("chat_type", ""),
            "msg_type": msg.get("message_type", "text"),
            "sender_type": msg.get("sender_type", ""),
        },
        content_hash=None,
        source_version=None,
    )
    return with_inferred_scope(event)


def _normalize_timestamp(raw: str | int | None) -> str:
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    try:
        ts = int(raw)
        if ts > 1_000_000_000_000:
            ts = ts // 1000
        if ts > 1_000_000_000:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        pass
    if isinstance(raw, str) and "T" in raw:
        return raw
    return str(raw)
