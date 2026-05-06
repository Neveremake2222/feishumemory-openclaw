"""Feishu message reply client.

The daemon uses this client to send daemon-first memory replies back to Feishu
group chats.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_LARK_IMPORT_ERROR: Exception | None = None


def _load_lark() -> Any:
    global _LARK_IMPORT_ERROR
    try:
        import lark_oapi as lark
    except ImportError as exc:
        _LARK_IMPORT_ERROR = exc
        raise
    return lark


def _clean_display_text(text: str) -> str:
    """Remove JSON artifact wrappers from Feishu rich-text content."""
    if not text:
        return ""
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return stripped

    if isinstance(parsed, list):
        parts: list[str] = []
        for item in parsed:
            if isinstance(item, list):
                for sub in item:
                    if isinstance(sub, dict):
                        t = sub.get("text") or sub.get("content") or ""
                        if t:
                            parts.append(str(t))
            elif isinstance(item, dict):
                t = item.get("text") or item.get("content") or ""
                if t:
                    parts.append(str(t))
        return "\n".join(parts) if parts else stripped
    if isinstance(parsed, dict):
        t = parsed.get("text") or parsed.get("content") or ""
        return str(t) if t else stripped
    return stripped


def _build_client(app_id: str, app_secret: str) -> Any:
    lark = _load_lark()
    return lark.Client.builder().app_id(app_id).app_secret(app_secret).build()


class FeishuReplyClient:
    """Send Feishu messages using lark-oapi SDK."""

    def __init__(self, app_id: str, app_secret: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._client = _build_client(app_id, app_secret)

    def send_text(self, chat_id: str, text: str, parent_id: str | None = None) -> bool:
        """Send a plain text message to a Feishu chat."""
        _ = parent_id
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        content = json.dumps({"text": text})
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(content)
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )

        try:
            resp = self._client.im.v1.message.create(req)
            if not resp.success():
                logger.warning("Failed to send message to chat %s: %s %s", chat_id[:16], resp.code, resp.msg)
                return False
            return True
        except Exception as exc:
            logger.error("Exception sending message to chat %s: %s", chat_id[:16], exc)
            return False

    def send_memory_card(
        self,
        chat_id: str,
        memory_type: str,
        title: str,
        summary: str,
        confidence: float,
        layer: str,
        evidence_ref: str,
        parent_id: str | None = None,
    ) -> bool:
        """Send a memory captured card (A1/C1)."""
        _ = (memory_type, confidence, layer, evidence_ref)
        clean_title = _clean_display_text(title)
        clean_summary = _clean_display_text(summary)
        display_summary = clean_summary[:100] + "..." if len(clean_summary) > 100 else clean_summary
        display_text = display_summary or clean_title

        text = "\n".join(
            [
                "已记录",
                display_text[:120],
            ]
        )
        return self.send_text(chat_id, text, parent_id=parent_id)

    def send_related_memories_card(
        self,
        chat_id: str,
        memories: list[dict[str, Any]],
        parent_id: str | None = None,
    ) -> bool:
        """Send related historical memories card (A2)."""
        if not memories:
            return False

        lines = ["相关记忆："]
        for memory in memories:
            title = _clean_display_text(memory.get("title", ""))[:100]
            lines.append(f"- {title}")
        return self.send_text(chat_id, "\n".join(lines), parent_id=parent_id)

    def send_summary_card(
        self,
        chat_id: str,
        decisions: list[dict],
        task_statuses: list[dict],
        preferences: list[dict],
        project_id: str,
    ) -> bool:
        """Send structured memory summary (A3)."""
        lines = ["当前项目记忆："]

        if decisions:
            lines.append(f"决策（{len(decisions)}）：")
            for i, memory in enumerate(decisions, 1):
                title = _clean_display_text(memory.get("title", ""))[:60]
                lines.append(f"{i}. {title}")

        if task_statuses:
            lines.append(f"任务状态（{len(task_statuses)}）：")
            for i, memory in enumerate(task_statuses, 1):
                title = _clean_display_text(memory.get("title", ""))[:60]
                lines.append(f"{i}. {title}")

        if preferences:
            lines.append(f"偏好（{len(preferences)}）：")
            for i, memory in enumerate(preferences, 1):
                title = _clean_display_text(memory.get("title", ""))[:60]
                lines.append(f"{i}. {title}")

        if not decisions and not task_statuses and not preferences:
            lines.append("还没有可用记忆。")

        return self.send_text(chat_id, "\n".join(lines))
