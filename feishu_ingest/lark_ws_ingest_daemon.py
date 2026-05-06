"""lark_ws real-time ingest daemon.

Connects to Feishu via WebSocket, receives messages, writes them through the
Feishu ingest pipeline, and optionally sends daemon-first memory replies.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from memory_engine import MemoryEngine
from memory_engine.env import load_project_env
from memory_engine.governance import GovernanceRejected, governance_ballot_provider_from_env

from feishu_ingest.adapters.base import FeishuSourceAdapter
from feishu_ingest.models import FeishuEvent
from feishu_ingest.pipeline import run_ingest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

load_project_env()
ALLOWED_CHATS = os.environ.get("ALLOWED_CHAT_IDS", "")
DB_PATH = os.environ.get("MEMORY_ENGINE_DB", "memory_engine.sqlite3")
_WORKFLOW_HINT_TTL_HOURS = 2
_TRUSTED_WORKFLOW_OUTCOME_SENDER_TYPES = {"app", "bot", "system"}
_TRUSTED_WORKFLOW_OUTCOME_KINDS = {"tool_result", "cli_result", "workflow_result"}
_SUMMARY_MIN_CONFIDENCE = 0.6
_RELATED_MIN_CONFIDENCE = 0.6


class SingleEventAdapter(FeishuSourceAdapter):
    """Adapter wrapper for sending one live event through run_ingest()."""

    def __init__(self, event: FeishuEvent) -> None:
        self._event = event

    def stream_events(self) -> Iterator[FeishuEvent]:
        yield self._event


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _ingest_event(
    engine: MemoryEngine,
    event: FeishuEvent,
    reply_client: "FeishuReplyClient | None" = None,
) -> bool:
    """Write a live FeishuEvent through the canonical ingest pipeline."""
    try:
        result = run_ingest(SingleEventAdapter(event), engine)
        if result.memory_ids:
            logger.info("Written memory_ids=%s for msg %s", result.memory_ids, event.source_ref)
            if reply_client:
                _send_memory_cards(engine, event, result.memory_ids, reply_client)
            return True
        if result.events_skipped_dup:
            logger.debug("Skipped duplicate msg %s", event.source_ref)
        elif result.events_skipped_no_candidate:
            logger.debug("Skipped msg %s: no memory candidate", event.source_ref)
        elif result.errors:
            logger.error("Failed msg %s: %s", event.source_ref, result.errors)
        return False
    except Exception as exc:
        logger.error("Ingest failed for msg %s: %s", event.source_ref, exc)
        return False


def _send_memory_cards(
    engine: MemoryEngine,
    event: FeishuEvent,
    memory_ids: list[int],
    reply_client: "FeishuReplyClient",
) -> None:
    """Send A1/C1 memory captured cards after successful ingest."""
    chat_id = event.payload.get("chat_id", "")
    if not chat_id:
        return
    for mid in memory_ids:
        row = engine.conn.execute(
            "SELECT memory_type, title, summary, confidence, logical_layer FROM memories WHERE id = ?",
            (mid,),
        ).fetchone()
        if not row:
            continue
        try:
            reply_client.send_memory_card(
                chat_id=chat_id,
                memory_type=row["memory_type"],
                title=row["title"],
                summary=row["summary"],
                confidence=row["confidence"],
                layer=row["logical_layer"],
                evidence_ref=event.source_ref,
            )
        except Exception as exc:
            logger.warning("Failed to send memory card: %s", exc)


def _push_related_memories(
    engine: MemoryEngine,
    event: FeishuEvent,
    chat_id: str,
    new_memory_ids: list[int],
    reply_client: "FeishuReplyClient",
) -> None:
    """A2: Push the single most relevant historical decision."""
    from memory_engine.models import RecallRequest

    _ = new_memory_ids
    query_terms = _important_terms(event.content)
    recall_query = " ".join(sorted(query_terms)) if query_terms else event.content
    results = engine.recall(
        RecallRequest(
            query=recall_query,
            project_id=event.project_id,
            scope="project",
        ),
        limit=10,
    )
    candidates = [
        r
        for r in results
        if r.get("memory_type") == "decision"
        and r.get("source_ref") != event.source_ref
        and r.get("score", 0) >= 0.5
        and r.get("confidence", 0) >= _RELATED_MIN_CONFIDENCE
    ]
    if query_terms:
        related = [r for r in candidates if _has_topic_overlap(query_terms, r)]
    else:
        related = candidates[:1] or _latest_decision_memory(engine, event)
    if related:
        try:
            reply_client.send_related_memories_card(chat_id, related[:1])
        except Exception as exc:
            logger.warning("Failed to send related memories card: %s", exc)


def _push_summary(
    engine: MemoryEngine,
    event: FeishuEvent,
    chat_id: str,
    reply_client: "FeishuReplyClient",
) -> None:
    """A3: Push concise memory summary: top 1 decision/status/preference."""
    from memory_engine.models import RecallRequest

    results = engine.recall(RecallRequest(query="", project_id=event.project_id), limit=10)
    if not results:
        return

    decisions = [
        r for r in results if r.get("memory_type") == "decision" and r.get("confidence", 0) >= _SUMMARY_MIN_CONFIDENCE
    ][:1]
    statuses = [
        r for r in results if r.get("memory_type") == "task_status" and r.get("confidence", 0) >= _SUMMARY_MIN_CONFIDENCE
    ][:1]
    preferences = [
        r for r in results if r.get("memory_type") == "preference" and r.get("confidence", 0) >= _SUMMARY_MIN_CONFIDENCE
    ][:1]

    from feishu_ingest.adapters.reply import _clean_display_text

    def clean_title(row: dict) -> str:
        return _clean_display_text(row.get("title", ""))[:60]

    lines = ["当前项目记忆："]
    if decisions:
        lines.append(f"- 最新决策：{clean_title(decisions[0])}")
    if statuses:
        lines.append(f"- 最新状态：{clean_title(statuses[0])}")
    if preferences:
        lines.append(f"- 相关偏好：{clean_title(preferences[0])}")
    if not decisions and not statuses and not preferences:
        lines.append("还没有可用记忆。")

    try:
        reply_client.send_text(chat_id, "\n".join(lines))
    except Exception as exc:
        logger.warning("Failed to send summary card: %s", exc)


def _check_triggers(
    engine: MemoryEngine,
    event: FeishuEvent,
    chat_id: str,
    reply_client: "FeishuReplyClient",
) -> None:
    """Check trigger words and push A2/A3/C2 replies for all messages."""
    from feishu_ingest.reply_triggers import (
        is_operation_trigger,
        is_related_trigger,
        is_summary_trigger,
        parse_preference_candidate_command,
        parse_workflow_strategy_command,
    )

    command = parse_preference_candidate_command(event.content)
    if command and _handle_preference_candidate_command(engine, event, chat_id, reply_client, command):
        return
    workflow_command = parse_workflow_strategy_command(event.content)
    if workflow_command and _handle_workflow_strategy_command(engine, event, chat_id, reply_client, workflow_command):
        return

    _record_workflow_outcomes_from_message(engine, event, chat_id)

    if is_related_trigger(event.content):
        try:
            _push_related_memories(engine, event, chat_id, [], reply_client)
        except Exception as exc:
            logger.warning("A2 push failed: %s", exc)

    if is_summary_trigger(event.content):
        try:
            _push_summary(engine, event, chat_id, reply_client)
        except Exception as exc:
            logger.warning("A3 summary failed: %s", exc)

    if is_operation_trigger(event.content):
        try:
            _push_preference_reminder(engine, event, chat_id, reply_client)
        except Exception as exc:
            logger.warning("C2 preference push failed: %s", exc)
        try:
            _push_workflow_skill_hint(engine, event, chat_id, reply_client)
        except Exception as exc:
            logger.warning("Workflow skill push failed: %s", exc)


def _handle_preference_candidate_command(
    engine: MemoryEngine,
    event: FeishuEvent,
    chat_id: str,
    reply_client: "FeishuReplyClient",
    command: tuple[str, str],
) -> bool:
    """Confirm/reject an implicit preference candidate by pattern key."""
    action, pattern_key = command
    row = engine.conn.execute(
        """
        SELECT id
        FROM memories
        WHERE memory_type = 'preference'
          AND status = 'active'
          AND content_json LIKE '%preference_candidate%'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (f"%{pattern_key}%", event.user_id, event.user_id, event.project_id, event.project_id),
    ).fetchone()
    if row is None:
        stable_review_id = _find_stable_preference_under_review(engine, event, pattern_key)
        if stable_review_id is None:
            reply_client.send_text(chat_id, f"未找到待确认偏好：{pattern_key}")
            return True
        if action == "confirm":
            result = engine.reconfirm_stable_preference(stable_review_id, user_id=event.user_id)
            reply_client.send_text(chat_id, f"已重新确认偏好：{pattern_key}（memory_id={result['stable_preference_id']}）")
            return True
        if action == "reject":
            engine.reject_stable_preference(stable_review_id, user_id=event.user_id)
            reply_client.send_text(chat_id, f"已拒绝稳定偏好：{pattern_key}")
            return True
        return False

    candidate_id = int(row["id"])
    if action == "confirm":
        try:
            result = engine.confirm_preference_candidate(candidate_id, user_id=event.user_id)
        except GovernanceRejected as exc:
            reply_client.send_text(chat_id, f"偏好确认被治理投票拒绝：{exc.decision['reason']}")
            return True
        reply_client.send_text(chat_id, f"已确认偏好：{pattern_key}（memory_id={result['stable_preference_id']}）")
        return True
    if action == "reject":
        engine.reject_preference_candidate(candidate_id, user_id=event.user_id)
        reply_client.send_text(chat_id, f"已拒绝偏好：{pattern_key}")
        return True
    return False


def _find_stable_preference_under_review(
    engine: MemoryEngine,
    event: FeishuEvent,
    pattern_key: str,
) -> int | None:
    rows = engine.conn.execute(
        """
        SELECT id, content_json
        FROM memories
        WHERE memory_type = 'preference'
          AND status = 'active'
          AND content_json LIKE '%stable_preference%'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        ORDER BY id DESC
        LIMIT 10
        """,
        (f"%{pattern_key}%", event.user_id, event.user_id, event.project_id, event.project_id),
    ).fetchall()
    for row in rows:
        try:
            content = json.loads(row["content_json"])
        except Exception:
            continue
        if (
            content.get("kind") == "stable_preference"
            and content.get("pattern_key") == pattern_key
            and content.get("needs_review") == "true"
        ):
            return int(row["id"])
    return None


def _handle_workflow_strategy_command(
    engine: MemoryEngine,
    event: FeishuEvent,
    chat_id: str,
    reply_client: "FeishuReplyClient",
    command: tuple[str, str],
) -> bool:
    """Confirm/reject a workflow strategy candidate by task_type."""
    action, task_type = command
    row = engine.conn.execute(
        """
        SELECT id
        FROM memories
        WHERE memory_type = 'procedural'
          AND status = 'active'
          AND content_json LIKE '%workflow_strategy_candidate%'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (f"%{task_type}%", event.user_id, event.user_id, event.project_id, event.project_id),
    ).fetchone()
    if row is None:
        skill_review_id = _find_workflow_skill_under_review(engine, event, task_type)
        if skill_review_id is None:
            reply_client.send_text(chat_id, f"No workflow strategy candidate or skill review found: {task_type}")
            return True
        if action == "confirm":
            result = engine.reconfirm_workflow_skill(skill_review_id, user_id=event.user_id)
            reply_client.send_text(
                chat_id,
                f"Reconfirmed workflow skill: {task_type}, memory_id={result['workflow_skill_id']}",
            )
            return True
        if action == "reject":
            engine.reject_workflow_skill(skill_review_id, user_id=event.user_id)
            reply_client.send_text(chat_id, f"Rejected workflow skill: {task_type}")
            return True
        return False

    candidate_id = int(row["id"])
    if action == "confirm":
        try:
            result = engine.confirm_workflow_strategy_candidate(candidate_id, user_id=event.user_id)
            reply_client.send_text(
                chat_id,
                f"Confirmed workflow strategy: {task_type}, memory_id={result['workflow_skill_id']}",
            )
        except ValueError as exc:
            reply_client.send_text(chat_id, f"Workflow strategy was not confirmed: {exc}")
        return True
    if action == "reject":
        engine.reject_workflow_strategy_candidate(candidate_id, user_id=event.user_id)
        reply_client.send_text(chat_id, f"Rejected workflow strategy: {task_type}")
        return True
    return False


def _find_workflow_skill_under_review(
    engine: MemoryEngine,
    event: FeishuEvent,
    task_type: str,
) -> int | None:
    rows = engine.conn.execute(
        """
        SELECT id, content_json
        FROM memories
        WHERE memory_type = 'procedural'
          AND status = 'active'
          AND content_json LIKE '%workflow_skill%'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        ORDER BY id DESC
        LIMIT 10
        """,
        (f"%{task_type}%", event.user_id, event.user_id, event.project_id, event.project_id),
    ).fetchall()
    for row in rows:
        try:
            content = json.loads(row["content_json"])
        except Exception:
            continue
        if (
            content.get("kind") == "workflow_skill"
            and content.get("task_type") == task_type
            and content.get("needs_review") == "true"
        ):
            return int(row["id"])
    return None


def _push_preference_reminder(
    engine: MemoryEngine,
    event: FeishuEvent,
    chat_id: str,
    reply_client: "FeishuReplyClient",
) -> None:
    """C2: Push the single most recent high-confidence preference."""
    project_id = event.project_id
    if not project_id:
        return
    query_terms = _important_terms(event.content)
    if not query_terms:
        return

    rows = engine.conn.execute(
        "SELECT id, title, summary, confidence, content_json, logical_layer FROM memories "
        "WHERE memory_type = 'preference' AND status = 'active' "
        "AND project_id = ? "
        "AND (confidence >= 0.8 OR logical_layer IN ('L2', 'L3')) "
        "AND (content_json IS NULL OR content_json NOT LIKE '%implicit_preference_observation%') "
        "AND (content_json IS NULL OR content_json NOT LIKE '%preference_candidate%') "
        "ORDER BY id DESC LIMIT 10",
        (project_id,),
    ).fetchall()
    row = next(
        (
            candidate
            for candidate in rows
            if _has_topic_overlap(query_terms, candidate)
        ),
        None,
    )
    if not row:
        return
    if _recently_pushed_preference(engine, chat_id, int(row["id"])):
        return

    text = (
        "偏好提醒：\n"
        f"- 已学习到：{row['title'][:80]}\n"
        "- 当前任务可能适用这个偏好。"
    )
    try:
        if reply_client.send_text(chat_id, text):
            _record_preference_push(engine, chat_id, int(row["id"]))
    except Exception as exc:
        logger.warning("Failed to send preference reminder: %s", exc)


def _recently_pushed_preference(engine: MemoryEngine, chat_id: str, memory_id: int) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    marker = f"preference_reminder:{chat_id}:{memory_id}"
    row = engine.conn.execute(
        """
        SELECT 1
        FROM audit_log
        WHERE action = 'push'
          AND target_type = 'memory'
          AND target_id = ?
          AND detail = ?
          AND audited_at >= ?
        LIMIT 1
        """,
        (memory_id, marker, since),
    ).fetchone()
    return row is not None


def _record_preference_push(engine: MemoryEngine, chat_id: str, memory_id: int) -> None:
    from memory_engine.models import utc_now

    engine.conn.execute(
        """
        INSERT INTO audit_log (action, target_type, target_id, actor, detail, sensitive_detections, audited_at)
        VALUES ('push', 'memory', ?, '', ?, 0, ?)
        """,
        (memory_id, f"preference_reminder:{chat_id}:{memory_id}", utc_now()),
    )
    engine.conn.commit()


def _push_workflow_skill_hint(
    engine: MemoryEngine,
    event: FeishuEvent,
    chat_id: str,
    reply_client: "FeishuReplyClient",
) -> None:
    """Push a reusable workflow skill from the daemon side and remember it for outcome linkage."""
    from memory_engine.models import RecallRequest

    project_id = event.project_id
    if not project_id:
        return
    query_terms = _important_terms(event.content)
    results = engine.recall(
        RecallRequest(
            query=event.content,
            project_id=project_id,
            task_id=event.task_id,
            intent="workflow",
        ),
        limit=10,
    )
    skill = next(
        (
            row
            for row in results
            if row.get("memory_type") == "procedural"
            and row.get("content", {}).get("kind") == "workflow_skill"
            and row.get("content", {}).get("needs_review") != "true"
            and row.get("confidence", 0) >= 0.6
            and (not query_terms or _has_topic_overlap(query_terms, row))
        ),
        None,
    )
    if not skill:
        return
    if _workflow_hint_is_active(engine, chat_id, event, int(skill["id"])):
        return

    text = (
        "Workflow skill:\n"
        f"- {str(skill.get('title', ''))[:80]}\n"
        "- Use this only if it fits the current task; the daemon will track explicit success/failure follow-up."
    )
    try:
        if reply_client.send_text(chat_id, text):
            _record_workflow_hint(engine, chat_id, event, int(skill["id"]))
    except Exception as exc:
        logger.warning("Failed to send workflow skill hint: %s", exc)


def _record_workflow_outcomes_from_message(engine: MemoryEngine, event: FeishuEvent, chat_id: str) -> None:
    """Link Feishu-observed execution outcomes to recently pushed workflow skills."""
    from memory_engine.workflows import detect_workflow_result

    if not _is_trusted_workflow_outcome_source(event):
        return

    workflow_result = detect_workflow_result(
        user_message=event.content,
        tool_output=event.content,
        assistant_summary=event.content,
    )
    if workflow_result is None:
        return

    skill_ids = _active_workflow_hints(engine, chat_id, event)
    if not skill_ids:
        return
    for skill_id in skill_ids:
        try:
            engine.record_workflow_skill_outcome(
                skill_id,
                outcome=workflow_result["outcome"],
                summary=workflow_result["summary"] or event.content[:180],
                evidence=[
                    {
                        "source_type": event.source_type,
                        "source_ref": event.source_ref,
                        "actor": event.user_id or "",
                        "excerpt": event.content[:200],
                    }
                ],
                project_id=event.project_id,
                task_id=event.task_id,
                user_id=event.user_id,
            )
            logger.info("Recorded workflow skill outcome for memory_id=%s from msg=%s", skill_id, event.source_ref)
        except Exception as exc:
            logger.warning("Failed to record workflow skill outcome for memory_id=%s: %s", skill_id, exc)


def _is_trusted_workflow_outcome_source(event: FeishuEvent) -> bool:
    payload = event.payload or {}
    sender_type = str(payload.get("sender_type", "")).lower()
    if sender_type in _TRUSTED_WORKFLOW_OUTCOME_SENDER_TYPES:
        return True

    source_kind = str(payload.get("source_kind") or payload.get("source_role") or "").lower()
    if source_kind in _TRUSTED_WORKFLOW_OUTCOME_KINDS:
        return True

    trusted_actor_ids = {
        item.strip()
        for item in os.environ.get("WORKFLOW_OUTCOME_TRUSTED_ACTOR_IDS", "").split(",")
        if item.strip()
    }
    return bool(event.user_id and event.user_id in trusted_actor_ids)


def _workflow_hint_prefix(chat_id: str, event: FeishuEvent) -> str:
    return f"workflow_skill_hint:{chat_id}:{event.project_id or ''}:{event.task_id or ''}:"


def _workflow_hint_marker(chat_id: str, event: FeishuEvent, memory_id: int) -> str:
    return f"{_workflow_hint_prefix(chat_id, event)}{memory_id}"


def _record_workflow_hint(engine: MemoryEngine, chat_id: str, event: FeishuEvent, memory_id: int) -> None:
    from memory_engine.models import utc_now

    engine.conn.execute(
        """
        INSERT INTO audit_log (action, target_type, target_id, actor, detail, sensitive_detections, audited_at)
        VALUES ('push', 'memory', ?, ?, ?, 0, ?)
        """,
        (memory_id, event.user_id or "", _workflow_hint_marker(chat_id, event, memory_id), utc_now()),
    )
    engine.conn.commit()


def _workflow_hint_is_active(engine: MemoryEngine, chat_id: str, event: FeishuEvent, memory_id: int) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(hours=_WORKFLOW_HINT_TTL_HOURS)).isoformat()
    row = engine.conn.execute(
        """
        SELECT 1
        FROM audit_log
        WHERE action = 'push'
          AND target_type = 'memory'
          AND target_id = ?
          AND detail = ?
          AND audited_at >= ?
        LIMIT 1
        """,
        (memory_id, _workflow_hint_marker(chat_id, event, memory_id), since),
    ).fetchone()
    return row is not None


def _active_workflow_hints(engine: MemoryEngine, chat_id: str, event: FeishuEvent) -> list[int]:
    since = (datetime.now(timezone.utc) - timedelta(hours=_WORKFLOW_HINT_TTL_HOURS)).isoformat()
    prefix = _workflow_hint_prefix(chat_id, event)
    rows = engine.conn.execute(
        """
        SELECT target_id, MAX(audited_at) AS pushed_at
        FROM audit_log
        WHERE action = 'push'
          AND target_type = 'memory'
          AND detail IS NOT NULL
          AND substr(detail, 1, ?) = ?
          AND audited_at >= ?
        GROUP BY target_id
        ORDER BY pushed_at DESC
        LIMIT 5
        """,
        (len(prefix), prefix, since),
    ).fetchall()
    return [int(row["target_id"]) for row in rows]


_STOP_TERMS = {
    "what",
    "was",
    "the",
    "previous",
    "decision",
    "decided",
    "before",
    "last",
    "time",
    "which",
    "one",
    "\u4e4b\u524d",
    "\u51b3\u5b9a",
    "\u65b9\u6848",
    "\u6709\u5173",
    "\u6709\u5173\u5417",
    "\u8fd9\u4e2a",
    "\u90a3\u4e2a",
    "\u6211\u4eec",
    "之前",
    "上次",
    "那个",
    "决定",
    "决策",
    "是什么",
    "什么",
    "入口",
}

_DOMAIN_TERMS = (
    "openclaw",
    "daemon",
    "api",
    "sqlite",
    "postgresql",
    "fastapi",
    "pytest",
    "飞书",
    "主动回复",
    "回复入口",
    "托管版",
    "工具",
    "方案",
    "报表",
    "用户中心",
)


def _important_terms(text: str) -> set[str]:
    terms = set(re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}", text.lower()))
    terms.update(term for term in _DOMAIN_TERMS if term in text.lower())
    terms.update(
        term
        for term in re.findall(r"[\u4e00-\u9fff]{2,}", text)
        if 2 <= len(term) <= 6
    )
    return {term for term in terms if term not in _STOP_TERMS}


def _has_topic_overlap(query_terms: set[str], memory: dict) -> bool:
    """Require at least one concrete topic token before pushing A2 related memory."""
    if not query_terms:
        return False
    if hasattr(memory, "keys"):
        memory_text = " ".join(str(memory[key]) for key in memory.keys()).lower()
    else:
        memory_text = " ".join(
            str(memory.get(key, ""))
            for key in ("title", "summary", "content")
        ).lower()
    return any(term.lower() in memory_text for term in query_terms)


def _latest_decision_memory(engine: MemoryEngine, event: FeishuEvent) -> list[dict]:
    """Return the latest decision for generic historical-context questions."""
    row = engine.conn.execute(
        """
        SELECT m.id, m.memory_type, m.title, m.summary, m.confidence, m.logical_layer, e.source_ref
        FROM memories m
        LEFT JOIN events e ON e.id = m.source_event_id
        WHERE m.status = 'active'
          AND m.memory_type = 'decision'
          AND m.confidence >= ?
          AND (? IS NULL OR m.project_id = ?)
        ORDER BY m.updated_at DESC, m.id DESC
        LIMIT 1
        """,
        (_RELATED_MIN_CONFIDENCE, event.project_id, event.project_id),
    ).fetchone()
    if row is None or row["source_ref"] == event.source_ref:
        return []
    return [
        {
            "id": row["id"],
            "memory_type": row["memory_type"],
            "title": row["title"],
            "summary": row["summary"],
            "confidence": row["confidence"],
            "logical_layer": row["logical_layer"],
            "source_ref": row["source_ref"],
        }
    ]


def main() -> None:
    from feishu_ingest.adapters.lark_ws import LarkWsAdapter
    from feishu_ingest.project_registry import ProjectRegistry

    try:
        app_id = _required_env("LARK_APP_ID")
        app_secret = _required_env("LARK_APP_SECRET")
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    registry_path = os.environ.get("PROJECT_REGISTRY_PATH", "config/project_registry.json")
    if os.path.exists(registry_path):
        try:
            registry = ProjectRegistry.load(registry_path)
            ProjectRegistry.configure(registry)
            logger.info("Loaded project registry: %d projects from %s", len(registry.get_all_projects()), registry_path)
        except Exception as exc:
            logger.warning("Failed to load project registry: %s", exc)
    else:
        logger.info("No project registry at %s (scope inference uses legacy maps)", registry_path)

    allowed = set(ALLOWED_CHATS.split(",")) if ALLOWED_CHATS else None

    reply_enabled = os.environ.get("MEMORY_REPLY_ENABLED", "true").lower() in ("true", "1", "yes")
    reply_client = None
    if reply_enabled:
        try:
            from feishu_ingest.adapters.reply import FeishuReplyClient

            reply_client = FeishuReplyClient(app_id, app_secret)
            logger.info("Memory reply client enabled (daemon-first Feishu replies active)")
        except Exception as exc:
            logger.warning(
                "Failed to initialize reply client: %s "
                "(Feishu proactive replies disabled; OpenClaw API remains optional/passive)",
                exc,
            )
    else:
        logger.info(
            "Memory reply client disabled by MEMORY_REPLY_ENABLED=false "
            "(daemon will ingest only; OpenClaw hosted mode will not auto-run TOOLS.md)"
        )

    logger.info("Starting lark_ws ingest daemon (app_id=%s)", app_id)
    logger.info("Writing to memory-engine DB at %s", DB_PATH)
    governance_ballot_provider = governance_ballot_provider_from_env()
    if governance_ballot_provider is not None:
        logger.info("Governance ballot CLI provider enabled by environment")

    adapter = LarkWsAdapter(
        app_id=app_id,
        app_secret=app_secret,
        allowed_chat_ids=allowed,
        queue_size=200,
    )

    logger.info("Listening for Feishu messages...")
    try:
        with MemoryEngine(DB_PATH, governance_ballot_provider=governance_ballot_provider) as engine:
            for event in adapter.stream_events():
                chat_id = event.payload.get("chat_id", "")
                content_preview = (event.content or "")[:60]
                logger.info("Received: chat=%s sender=%s content=%s", chat_id, event.user_id, content_preview)

                registry = ProjectRegistry.get_instance()
                if registry and chat_id and not registry.project_for_chat(chat_id):
                    pid = registry.register_chat(chat_id)
                    try:
                        registry.save(registry_path)
                        logger.info("Auto-registered new chat %s as project %s", chat_id[:16], pid)
                    except Exception as exc:
                        logger.error("Failed to save registry after auto-register: %s", exc)

                _ingest_event(engine, event, reply_client=reply_client)

                if reply_client and chat_id:
                    _check_triggers(engine, event, chat_id, reply_client)
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down")
    finally:
        adapter.close()
        logger.info("Adapter closed")


if __name__ == "__main__":
    main()
