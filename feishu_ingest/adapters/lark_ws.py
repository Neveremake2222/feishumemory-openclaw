"""WebSocket long-connection adapter using lark-oapi SDK.

Uses `lark.ws.Client` to establish a persistent WebSocket connection to Feishu's
event bus and receive real-time `im.message.receive_v1` events.

Requires:
- `pip install lark-oapi`
- `im.message.receive_v1` event enabled in the Feishu developer console
  (after the SDK connection is running, switch to "使用长连接接收事件" mode)
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Any, Iterator

from memory_engine.models import Scope

from feishu_ingest.adapters.base import FeishuSourceAdapter
from feishu_ingest.adapters.lark_cli import SetupError
from feishu_ingest.adapters.message_utils import normalize_message_content, with_inferred_scope
from feishu_ingest.models import FeishuEvent

logger = logging.getLogger(__name__)

_LARK_IMPORT_ERROR: Exception | None = None


def _load_lark() -> Any:
    global _LARK_IMPORT_ERROR
    try:
        import lark_oapi as lark
    except ImportError as exc:
        _LARK_IMPORT_ERROR = exc
        raise SetupError("lark-oapi not installed; run: pip install lark-oapi") from exc
    return lark


class LarkWsAdapter(FeishuSourceAdapter):
    """WebSocket long-connection adapter using lark-oapi SDK.

    Uses `lark.ws.Client` for persistent WebSocket connection to Feishu's
    event bus. Events are received via SDK callback and bridged to the
    `Iterator[FeishuEvent]` interface via a thread-safe queue.

    Usage:
        adapter = LarkWsAdapter(
            app_id="cli_xxx",
            app_secret="...",
            allowed_chat_ids={"oc_xxx"},  # optional filter
        )
        for event in adapter.stream_events():
            process(event)
        adapter.close()

    Setup sequence:
    1. Run this code (starts the WebSocket connection)
    2. In Feishu developer console, go to Event Subscriptions
    3. Switch to "使用长连接接收事件" (Use long connection to receive events)
    4. Save and publish a new app version
    """

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        allowed_chat_ids: set[str] | None = None,
        queue_size: int = 100,
        log_level: Any = None,
        auto_reconnect: bool = True,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._allowed_chat_ids = allowed_chat_ids
        self._queue_size = queue_size
        self._log_level = log_level
        self._auto_reconnect = auto_reconnect

        self._q: queue.Queue[FeishuEvent | None] = queue.Queue(maxsize=queue_size)
        self._stop_consumer = threading.Event()
        self._stop_signal_sent = False

    def _build_event_handler(self) -> Any:
        """Build the EventDispatcherHandler with im.message.receive_v1 registered."""
        lark = _load_lark()

        def on_im_message_receive(data: Any) -> None:
            event = _sdk_event_to_feishu_event(data)
            if event is None:
                return
            chat_id = event.payload.get("chat_id", "")
            if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
                logger.debug("Skipping message from disallowed chat: %s", chat_id)
                return
            try:
                self._q.put_nowait(event)
            except queue.Full:
                logger.warning("Event queue full; dropping event %s", event.source_ref)

        builder = lark.EventDispatcherHandler.builder("", "")
        return builder.register_p2_im_message_receive_v1(on_im_message_receive).build()

    def stream_events(self) -> Iterator[FeishuEvent]:
        """Yield live FeishuEvent objects from the WebSocket stream.

        Starts the lark.ws.Client in a background thread and yields events
        as they arrive. Blocks until close() is called.
        """
        if not self._q.empty():
            while True:
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    return
                if item is None:
                    return
                yield item

        lark = _load_lark()
        log_level = self._log_level if self._log_level is not None else lark.LogLevel.WARNING
        event_handler = self._build_event_handler()
        client = lark.ws.Client(
            self._app_id,
            self._app_secret,
            log_level=log_level,
            event_handler=event_handler,
            auto_reconnect=self._auto_reconnect,
        )

        conn_thread = threading.Thread(target=client.start, name="lark-ws-client", daemon=True)
        conn_thread.start()

        logger.info("LarkWsAdapter connected (thread %s)", conn_thread.name)

        try:
            while not self._stop_consumer.is_set():
                try:
                    item = self._q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is None:
                    break
                yield item
        finally:
            self._stop_consumer.set()
            try:
                client.stop()
            except Exception:
                pass
            logger.info("LarkWsAdapter stream closed")

    def close(self) -> None:
        """Signal the event stream to stop."""
        self._stop_consumer.set()
        if not self._stop_signal_sent:
            self._stop_signal_sent = True
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass


def _sdk_event_to_feishu_event(data: Any) -> FeishuEvent | None:
    """Convert an lark-oapi P2ImMessageReceiveV1 object to FeishuEvent."""
    try:
        return _convert_sdk_event(data)
    except Exception as exc:
        logger.error("Failed to convert SDK event: %s", exc)
        return None


def _convert_sdk_event(data: Any) -> FeishuEvent | None:
    """Unwrap the SDK event object and extract FeishuEvent fields.

    The P2ImMessageReceiveV1 object has:
      data.event.message   → EventMessage {message_id, chat_id, content, ...}
      data.event.sender    → EventSender  {sender_id, sender_type, ...}
    """
    inner = getattr(data, "event", None)
    if inner is None:
        return None

    msg = getattr(inner, "message", None)
    sender_obj = getattr(inner, "sender", None)

    if msg is None:
        return None

    message_id = _get_field(msg, "message_id", "")
    chat_id = _get_field(msg, "chat_id", "")
    content_raw = _get_field(msg, "content", "")
    content = _extract_text_content(content_raw)
    create_time = _get_field(msg, "create_time", "")
    message_type = _get_field(msg, "message_type", "text")
    chat_type = _get_field(msg, "chat_type", "")

    sender_id = ""
    sender_type = ""
    if sender_obj is not None:
        sender_type = _get_field(sender_obj, "sender_type", "")
        raw_sender_id = _get_field(sender_obj, "sender_id", "")
        # sender_id is a UserId object with open_id/union_id/user_id fields
        if hasattr(raw_sender_id, "open_id"):
            sender_id = getattr(raw_sender_id, "open_id", "") or ""
        elif isinstance(raw_sender_id, str):
            sender_id = raw_sender_id

    if not message_id or not content:
        return None

    event = FeishuEvent(
        source_type="message",
        source_ref=str(message_id),
        source_url=None,
        actors=[str(sender_id)] if sender_id else [],
        timestamp=_normalize_timestamp(create_time),
        content=content,
        scope=Scope.USER,
        project_id=None,
        task_id=None,
        user_id=str(sender_id) if sender_id else None,
        payload={
            "chat_id": str(chat_id),
            "chat_type": str(chat_type),
            "msg_type": str(message_type),
            "sender_type": str(sender_type),
        },
        content_hash=None,
        source_version=None,
    )
    return with_inferred_scope(event)


def _get_field(obj: Any, name: str, default: Any) -> Any:
    """Safely get a protobuf message field, returning default if absent."""
    val = getattr(obj, name, default)
    if val is None:
        return default
    return val


def _extract_text_content(raw: str | dict | Any) -> str:
    """Extract human-readable text from a message content field."""
    return normalize_message_content(raw)


def _normalize_timestamp(raw: str | int | float | None) -> str:
    """Normalize timestamp to ISO 8601 UTC."""
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    try:
        ts = float(raw)
        if ts > 1_000_000_000_000:
            ts = ts / 1000
        if ts > 1_000_000_000:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        pass
    if isinstance(raw, str) and "T" in raw:
        return raw
    return str(raw)
