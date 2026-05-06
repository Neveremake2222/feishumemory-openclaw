"""Write hook — classify OpenClaw events and write worthy ones to memory-engine."""

from __future__ import annotations

import logging
import re
from typing import Any

from memory_engine.implicit_preferences import build_observation_candidate, detect_implicit_preference_signals
from memory_engine.models import MemoryCandidate, SourceEvent
from memory_engine.workflows import (
    build_workflow_case_from_result,
    build_workflow_trace_candidate,
    derive_workflow_trace_steps,
    detect_workflow_result,
)

from openclaw_adapter.dedupe import AdapterDedupe
from openclaw_adapter.engine_client import DirectEngineClient
from openclaw_adapter.types import OpenClawEvent, WriteDecision, WriteResult

logger = logging.getLogger(__name__)

_DEFAULT_DB = "memory_engine.sqlite3"


class WriteFilter:
    """Classify whether an OpenClawEvent is worth writing to memory-engine."""

    DECISION_PATTERNS = [
        r"决定", r"确定", r"采用", r"选择",
        r"decided", r"chose", r"selected", r"adopted",
        r"will use", r"going with",
    ]
    COMPLETION_MARKERS = [
        "passed", "success", "completed", "done",
        "通过", "成功", "完成", "解决",
        "0 failed", "all passed", "exited 0",
    ]
    PREFERENCE_PATTERNS = [
        r"以后", r"以后都", r"默认", r"prefer",
        r"我喜欢", r"请用", r"不要再",
        r"from now on", r"always", r"never",
        r"每次都", r"习惯", r"倾向",
        r"建议用", r"最好用", r"不用",
        r"不要", r"别用", r"换成",
    ]
    HABIT_PATTERNS = [
        r"每周", r"每天", r"定期", r"固定",
        r"周报", r"日报", r"月报",
        r"weekly", r"daily", r"monthly",
        r"提醒我", r"自动(?:整理|生成|发送)",
        r"每到", r"惯例",
    ]

    def classify(self, event: OpenClawEvent) -> WriteDecision:
        if self._is_decision(event):
            return WriteDecision(
                action="write",
                reason="explicit decision language",
                memory_type="decision",
                confidence=0.85,
                importance=0.8,
            )
        if self._is_completed_task(event):
            return WriteDecision(
                action="write",
                reason="verified task completion",
                memory_type="task_status",
                confidence=0.80,
                importance=0.7,
            )
        if self._is_preference(event):
            return WriteDecision(
                action="write",
                reason="explicit preference expression",
                memory_type="preference",
                confidence=0.85,
                importance=0.6,
            )
        if self._is_habit(event):
            return WriteDecision(
                action="write",
                reason="habit/routine pattern detected",
                memory_type="preference",
                confidence=0.75,
                importance=0.7,
            )
        return WriteDecision(
            action="reject",
            reason="no write-worthy signal detected",
        )

    def _is_decision(self, event: OpenClawEvent) -> bool:
        text = f"{event.user_message} {event.assistant_summary or ''}"
        return any(re.search(p, text) for p in self.DECISION_PATTERNS)

    def _is_completed_task(self, event: OpenClawEvent) -> bool:
        if not event.tool_output:
            return False
        return any(m in event.tool_output for m in self.COMPLETION_MARKERS)

    def _is_preference(self, event: OpenClawEvent) -> bool:
        text = f"{event.user_message} {event.assistant_summary or ''}"
        return any(re.search(p, text) for p in self.PREFERENCE_PATTERNS)

    def _is_habit(self, event: OpenClawEvent) -> bool:
        """Detect habit/routine patterns (Direction C implicit learning)."""
        text = f"{event.user_message} {event.assistant_summary or ''}"
        return any(re.search(p, text) for p in self.HABIT_PATTERNS)


def write(
    event: OpenClawEvent,
    db_path: str = _DEFAULT_DB,
    client: DirectEngineClient | None = None,
) -> WriteResult:
    """Write an OpenClaw event to memory-engine if worthwhile.

    Fail-open: always returns WriteResult, never raises.
    """
    decision = WriteFilter().classify(event)
    implicit_signals = detect_implicit_preference_signals(
        f"{event.user_message} {event.assistant_summary or ''}"
    )
    workflow_result = detect_workflow_result(
        user_message=event.user_message,
        tool_name=event.tool_name,
        tool_output=event.tool_output,
        assistant_summary=event.assistant_summary,
    )
    if decision.action == "reject":
        if not implicit_signals and workflow_result is None:
            return WriteResult(
                action="reject",
                written=False,
                memory_ids=[],
                skip_reason=decision.reason,
            )
        if implicit_signals:
            decision = WriteDecision(
                action="write",
                reason="implicit preference observation",
                memory_type="preference",
                confidence=0.4,
                importance=0.3,
            )
        else:
            decision = WriteDecision(
                action="write",
                reason="workflow result observation",
                memory_type="procedural",
                confidence=0.65,
                importance=0.6,
            )

    dedupe = AdapterDedupe(session_id=event.session_id or "default")
    if dedupe.should_skip(event, decision):
        return WriteResult(
            action="skip",
            written=False,
            memory_ids=[],
            skip_reason="dedupe: recent identical write",
        )

    source_event = _build_source_event(event)
    candidates = _build_candidates(
        event,
        decision,
        implicit_signals=implicit_signals if decision.reason == "implicit preference observation" else None,
        workflow_result=workflow_result,
    )

    try:
        workflow_outcome_memory_ids: list[int] = []
        if client is not None:
            result = client.write(
                event=source_event,
                candidates=candidates,
                project_id=event.project_id,
                task_id=event.task_id,
                user_id=event.user_id,
            )
            workflow_outcome_memory_ids = _record_recalled_workflow_skill_outcomes(
                client,
                event,
                workflow_result,
            )
        else:
            with DirectEngineClient(db_path) as c:
                result = c.write(
                    event=source_event,
                    candidates=candidates,
                    project_id=event.project_id,
                    task_id=event.task_id,
                    user_id=event.user_id,
                )
                workflow_outcome_memory_ids = _record_recalled_workflow_skill_outcomes(
                    c,
                    event,
                    workflow_result,
                )
        dedupe.record(event, decision, result.get("memory_ids", []))
        return WriteResult(
            action="write",
            written=True,
            memory_ids=result.get("memory_ids", []),
            conflict_detected=bool(result.get("conflicts")),
            workflow_outcome_memory_ids=workflow_outcome_memory_ids,
        )
    except Exception:
        logger.exception("write failed")
        return WriteResult(
            action="reject",
            written=False,
            memory_ids=[],
            skip_reason="engine write exception",
        )


def _infer_scope(event: OpenClawEvent) -> str:
    if event.project_id:
        return "project"
    if event.task_id:
        return "task"
    if event.user_id:
        return "user"
    return "session"


def _build_source_event(event: OpenClawEvent) -> SourceEvent:
    scope = _infer_scope(event)
    return SourceEvent(
        source_type="event",
        source_ref=f"openclaw:{event.session_id or 'unknown'}:{event.timestamp}",
        actors=[event.user_id] if event.user_id else [],
        timestamp=event.timestamp or "1970-01-01T00:00:00+00:00",
        content=event.user_message,
        scope=scope,
    )


def _build_candidates(
    event: OpenClawEvent,
    decision: WriteDecision,
    *,
    implicit_signals: list | None = None,
    workflow_result: dict[str, str] | None = None,
) -> list[MemoryCandidate]:
    content_text = f"{event.user_message}\n{event.tool_output or ''}"

    if decision.memory_type == "decision":
        title = _extract_decision_title(event.user_message)
        reason = _extract_reason(event.user_message)
        conclusion = _extract_conclusion(event.user_message)
        parts = [f"决策: {title}"]
        if reason:
            parts.append(f"理由: {reason}")
        if conclusion:
            parts.append(f"结论: {conclusion}")
        summary = "\n".join(parts)
    elif decision.memory_type == "task_status":
        title = _extract_task_title(event)
        summary = f"Task completed: {title}"
    elif decision.memory_type == "preference":
        title = _extract_preference_title(event.user_message)
        scope_hint = "个人偏好" if decision.reason == "explicit preference expression" else "工作习惯"
        summary = f"[{scope_hint}] {event.user_message[:150]}"
    else:
        title = content_text[:50]
        summary = content_text[:200]

    evidence = [
        {
            "source_type": "openclaw_outcome",
            "source_ref": f"session:{event.session_id}",
            "actor": event.user_id or "agent",
            "excerpt": content_text[:200],
        }
    ]

    scope = _infer_scope(event)

    candidates: list[MemoryCandidate] = []
    if decision.reason not in {"implicit preference observation", "workflow result observation"}:
        candidates.append(
            MemoryCandidate(
                memory_type=decision.memory_type or "semantic",
                title=title,
                summary=summary,
                content={"scope": scope},
                importance=decision.importance,
                confidence=decision.confidence,
                evidence=evidence,
                tags=["openclaw"],
                change_reason=f"write_hook: {decision.reason}",
            )
        )

    for signal in implicit_signals or []:
        candidates.append(
            build_observation_candidate(
                signal=signal,
                source_text=event.user_message,
                content_meta={"scope": scope, "source_type": "openclaw_event"},
                evidence=evidence,
                observed_at=event.timestamp or "1970-01-01T00:00:00+00:00",
            )
        )

    if workflow_result is not None:
        steps = derive_workflow_trace_steps(
            user_message=event.user_message,
            tool_name=event.tool_name,
            tool_output=event.tool_output,
            assistant_summary=event.assistant_summary,
        )
        candidates.append(
            build_workflow_trace_candidate(
                result=workflow_result,
                steps=steps,
                evidence=evidence,
                scope=scope,
                source_text=content_text,
            )
        )
        candidates.append(
            build_workflow_case_from_result(
                result=workflow_result,
                evidence=evidence,
                scope=scope,
                source_text=content_text,
            )
        )

    return candidates


def _record_recalled_workflow_skill_outcomes(
    client: DirectEngineClient,
    event: OpenClawEvent,
    workflow_result: dict[str, str] | None,
) -> list[int]:
    """Best-effort outcome feedback for workflow skills recalled before execution."""
    if workflow_result is None or not event.recalled_memory_ids:
        return []

    recorded: list[int] = []
    evidence = [
        {
            "source_type": "openclaw_outcome",
            "source_ref": f"session:{event.session_id}",
            "actor": event.user_id or "agent",
            "excerpt": (event.tool_output or event.assistant_summary or event.user_message)[:200],
        }
    ]
    for raw_id in event.recalled_memory_ids:
        try:
            skill_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        try:
            result = client.record_workflow_skill_outcome(
                skill_id,
                outcome=workflow_result["outcome"],
                summary=workflow_result["summary"] or workflow_result["outcome"],
                evidence=evidence,
                project_id=event.project_id,
                task_id=event.task_id,
                user_id=event.user_id,
            )
        except ValueError:
            continue
        except Exception:
            logger.exception("workflow skill outcome recording failed")
            continue
        outcome_id = result.get("outcome_memory_id") if isinstance(result, dict) else None
        if outcome_id is not None:
            recorded.append(int(outcome_id))
    return recorded

_REASON_PATTERNS = [
    r"因为(.+)", r"主要是(.+)", r"原因是(.+)",
    r"考虑(?:到)?(.+)", r"基于(.+)",
    r"because\s+(.+)", r"since\s+(.+)", r"due to\s+(.+)",
    r"the reason\s+(.+)",
]
_CONCLUSION_PATTERNS = [
    r"所以(.+)", r"因此(.+)", r"综上(.+)",
    r"最终(.+)", r"结论是(.+)",
    r"therefore\s+(.+)", r"in conclusion\s+(.+)",
    r"so\s+(.+)",
]


def _extract_decision_title(message: str) -> str:
    patterns = [
        r"决定\s*(?:用|采用|选择)?(.+)",
        r"确定\s*(?:用|采用|选择)?(.+)",
        r"采用\s*(.+)",
        r"选择\s*(.+)",
        r"chose\s+(.+)", r"selected\s+(.+)", r"adopted\s+(.+)",
        r"going\s+with\s+(.+)",
        r"decided\s+(?:to\s+)?(.+)",
    ]
    for p in patterns:
        m = re.search(p, message, re.IGNORECASE)
        if m:
            return m.group(1).strip()[:100]
    return message[:60]


def _extract_reason(message: str) -> str:
    for p in _REASON_PATTERNS:
        m = re.search(p, message, re.IGNORECASE)
        if m:
            return m.group(1).strip()[:120]
    return ""


def _extract_conclusion(message: str) -> str:
    for p in _CONCLUSION_PATTERNS:
        m = re.search(p, message, re.IGNORECASE)
        if m:
            return m.group(1).strip()[:120]
    return ""


def _extract_task_title(event: OpenClawEvent) -> str:
    return event.tool_name or "task"


def _extract_preference_title(message: str) -> str:
    patterns = [r"以后(.+)", r"prefer(.+)", r"我喜欢(.+)", r"请用(.+)"]
    for p in patterns:
        m = re.search(p, message)
        if m:
            return m.group(1).strip()[:60]
    return message[:50]
