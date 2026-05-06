from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .governance import BallotProvider, GovernanceRejected, review_workflow_strategy_candidate
from .models import EventEntry, MemoryCandidate, SourceEvent, utc_now
from .storage import insert_event, insert_event_entry, insert_memory


TRACE_KIND = "workflow_trace"
SUCCESS_CASE_KIND = "workflow_success_case"
FAILURE_CASE_KIND = "workflow_failure_case"
STRATEGY_CANDIDATE_KIND = "workflow_strategy_candidate"
WORKFLOW_SKILL_KIND = "workflow_skill"
WORKFLOW_SKILL_OUTCOME_KIND = "workflow_skill_outcome"
_SKILL_REVIEW_NEGATIVE_THRESHOLD = 2
_SKILL_ARCHIVE_NEGATIVE_THRESHOLD = 3
_SKILL_ARCHIVE_EFFECTIVENESS_THRESHOLD = 0.34
_MAX_ACTIVE_STRATEGY_CANDIDATES_PER_TASK_TYPE = 3
_STALE_WORKFLOW_SKILL_DAYS = 90

_SUCCESS_MARKERS = (
    "passed",
    "all passed",
    "0 failed",
    "success",
    "completed",
    "done",
    "exited 0",
)
_FAILURE_MARKERS = (
    "failed",
    "error",
    "exception",
    "traceback",
    "exit code 1",
    "exited 1",
)


def detect_workflow_result(
    *,
    user_message: str = "",
    tool_name: str | None = None,
    tool_output: str | None = None,
    assistant_summary: str | None = None,
) -> dict[str, str] | None:
    """Detect explicit workflow success/failure outcomes from agent execution text."""
    text = " ".join(part for part in [user_message, tool_name or "", tool_output or "", assistant_summary or ""] if part)
    lowered = text.lower()
    if not lowered:
        return None

    failure_text = re.sub(r"\b0\s+failed\b", "", lowered)
    has_failure = any(marker in failure_text for marker in _FAILURE_MARKERS)
    has_success = any(marker in lowered for marker in _SUCCESS_MARKERS)
    if not has_failure and not has_success:
        return None

    outcome = "failure" if has_failure else "success"
    return {
        "outcome": outcome,
        "task_type": _infer_task_type(lowered),
        "trigger": _compact_text(user_message or assistant_summary or tool_name or "agent workflow"),
        "summary": _compact_text(assistant_summary or tool_output or user_message),
    }


def build_workflow_case_candidate(
    *,
    kind: str,
    task_type: str,
    trigger: str,
    outcome: str,
    evidence: list[dict[str, Any]],
    steps: list[str] | None = None,
    root_cause: str | None = None,
    scope: str = "project",
    source_text: str = "",
) -> MemoryCandidate:
    """Build a procedural workflow success/failure case."""
    if kind not in {SUCCESS_CASE_KIND, FAILURE_CASE_KIND}:
        raise ValueError(f"invalid workflow case kind: {kind}")
    content: dict[str, Any] = {
        "scope": scope,
        "kind": kind,
        "task_type": task_type,
        "trigger": trigger,
        "steps": steps or [],
        "outcome": outcome,
        "source_text": source_text[:300],
    }
    if root_cause:
        content["root_cause"] = root_cause

    label = "success" if kind == SUCCESS_CASE_KIND else "failure"
    title = f"Workflow {label}: {task_type}"
    summary = f"{label.title()} workflow case for {task_type}: {outcome}"
    if root_cause:
        summary = f"{summary}. Root cause: {root_cause}"
    return MemoryCandidate(
        memory_type="procedural",
        title=title,
        summary=summary,
        content=content,
        importance=0.55 if kind == SUCCESS_CASE_KIND else 0.65,
        confidence=0.72 if kind == SUCCESS_CASE_KIND else 0.68,
        evidence=evidence,
        tags=["workflow", kind, task_type],
        change_reason=f"{kind}: {task_type}",
    )


def build_workflow_case_from_result(
    *,
    result: dict[str, str],
    evidence: list[dict[str, Any]],
    scope: str = "project",
    source_text: str = "",
) -> MemoryCandidate:
    kind = SUCCESS_CASE_KIND if result["outcome"] == "success" else FAILURE_CASE_KIND
    return build_workflow_case_candidate(
        kind=kind,
        task_type=result["task_type"],
        trigger=result["trigger"],
        outcome=result["summary"] or result["outcome"],
        evidence=evidence,
        steps=[],
        root_cause=result["summary"] if kind == FAILURE_CASE_KIND else None,
        scope=scope,
        source_text=source_text,
    )


def derive_workflow_trace_steps(
    *,
    user_message: str = "",
    tool_name: str | None = None,
    tool_output: str | None = None,
    assistant_summary: str | None = None,
) -> list[dict[str, str]]:
    """Build a compact step-level workflow trace from agent execution fields."""
    steps: list[dict[str, str]] = []
    if user_message.strip():
        steps.append(
            {
                "index": str(len(steps) + 1),
                "phase": "request",
                "name": "user_request",
                "status": "observed",
                "summary": _compact_text(user_message, 220),
            }
        )
    if tool_name:
        tool_tags = _tool_trace_tags(str(tool_name))
        steps.append(
            {
                "index": str(len(steps) + 1),
                "phase": "tool_call",
                "name": str(tool_name),
                "status": "called",
                "summary": f"tool={tool_name}",
                "tool_family": tool_tags["tool_family"],
                "verification_signal": tool_tags["verification_signal"],
            }
        )
    if tool_output:
        lowered = tool_output.lower()
        failure_text = re.sub(r"\b0\s+failed\b", "", lowered)
        status = "failed" if any(marker in failure_text for marker in _FAILURE_MARKERS) else "succeeded"
        diagnostics = _tool_result_diagnostics(
            tool_name=tool_name or "tool",
            tool_output=tool_output,
            status=status,
        )
        steps.append(
            {
                "index": str(len(steps) + 1),
                "phase": "tool_result",
                "name": str(tool_name or "tool"),
                "status": status,
                "summary": _compact_text(tool_output, 220),
                "exit_code": diagnostics["exit_code"],
                "failure_type": diagnostics["failure_type"],
                "failure_signal": diagnostics["failure_signal"],
                "verification_signal": diagnostics["verification_signal"],
            }
        )
    if assistant_summary:
        steps.append(
            {
                "index": str(len(steps) + 1),
                "phase": "assistant_summary",
                "name": "assistant_summary",
                "status": "observed",
                "summary": _compact_text(assistant_summary, 220),
            }
        )
    return steps


def build_workflow_trace_candidate(
    *,
    result: dict[str, str],
    steps: list[dict[str, str]],
    evidence: list[dict[str, Any]],
    scope: str = "project",
    source_text: str = "",
) -> MemoryCandidate:
    """Build a procedural workflow trace with ordered execution steps."""
    task_type = result["task_type"]
    failed_steps = [step["index"] for step in steps if step.get("status") == "failed"]
    verification_signals = _collect_step_values(steps, "verification_signal")
    failure_signals = _collect_step_values(steps, "failure_signal")
    tool_families = _collect_step_values(steps, "tool_family")
    content = {
        "scope": scope,
        "kind": TRACE_KIND,
        "task_type": task_type,
        "trigger": result["trigger"],
        "outcome": result["outcome"],
        "summary": result["summary"],
        "steps": steps,
        "step_count": str(len(steps)),
        "failed_step_indexes": failed_steps,
        "verification_signals": verification_signals,
        "failure_signals": failure_signals,
        "tool_families": tool_families,
        "source_text": source_text[:300],
    }
    return MemoryCandidate(
        memory_type="procedural",
        title=f"Workflow trace: {task_type}",
        summary=f"Step trace for {task_type}: {result['summary'] or result['outcome']}",
        content=content,
        importance=0.5,
        confidence=0.7,
        evidence=evidence,
        tags=["workflow", TRACE_KIND, task_type],
        change_reason=f"{TRACE_KIND}: {task_type}",
    )


def derive_workflow_strategy_candidates(
    conn,
    user_id: str | None = None,
    project_id: str | None = None,
    min_success_cases: int = 2,
) -> list[dict[str, Any]]:
    """Group workflow cases into strategy candidates."""
    rows = conn.execute(
        """
        SELECT id, content_json, evidence_json, project_id, created_at
        FROM memories
        WHERE memory_type = 'procedural'
          AND status = 'active'
          AND (
              content_json LIKE ?
              OR content_json LIKE ?
              OR content_json LIKE ?
          )
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        """,
        (
            f"%{SUCCESS_CASE_KIND}%",
            f"%{FAILURE_CASE_KIND}%",
            f"%{TRACE_KIND}%",
            user_id,
            user_id,
            project_id,
            project_id,
        ),
    ).fetchall()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        content = json.loads(row["content_json"])
        kind = content.get("kind")
        if kind not in {SUCCESS_CASE_KIND, FAILURE_CASE_KIND, TRACE_KIND}:
            continue
        task_type = str(content.get("task_type") or "agent_workflow")
        grouped.setdefault(task_type, []).append(
            {
                "id": int(row["id"]),
                "project_id": row["project_id"],
                "created_at": row["created_at"],
                "content": content,
                "evidence": json.loads(row["evidence_json"]),
            }
        )

    candidates: list[dict[str, Any]] = []
    for task_type, items in grouped.items():
        successes = [item for item in items if item["content"].get("kind") == SUCCESS_CASE_KIND]
        failures = [item for item in items if item["content"].get("kind") == FAILURE_CASE_KIND]
        traces = [item for item in items if item["content"].get("kind") == TRACE_KIND]
        if len(successes) < min_success_cases:
            continue
        first_observed_at = min(item["created_at"] for item in items)
        last_observed_at = max(item["created_at"] for item in items)
        verification_signals = _collect_trace_list_values(traces, "verification_signals")
        failure_signals = _collect_trace_list_values(traces, "failure_signals")
        tool_families = _collect_trace_list_values(traces, "tool_families")
        candidates.append(
            {
                "task_type": task_type,
                "success_case_memory_ids": [item["id"] for item in successes],
                "failure_case_memory_ids": [item["id"] for item in failures],
                "trace_memory_ids": [item["id"] for item in traces],
                "success_evidence_count": len(successes),
                "failure_evidence_count": len(failures),
                "trace_evidence_count": len(traces),
                "first_observed_at": first_observed_at,
                "last_observed_at": last_observed_at,
                "title": f"Workflow strategy candidate: {task_type}",
                "summary": (
                    f"Reuse candidate for {task_type}: {len(successes)} successful cases"
                    f" and {len(failures)} failure cases observed."
                ),
                "evidence": _flatten_evidence(successes + failures + traces),
                "recommended_steps": _collect_recommended_steps(successes, traces),
                "known_limits": _collect_known_limits(failures, traces),
                "verification_signals": verification_signals,
                "failure_signals": failure_signals,
                "tool_families": tool_families,
            }
        )
    return candidates


def materialize_workflow_strategy_candidates(
    conn,
    user_id: str | None = None,
    project_id: str | None = None,
    min_success_cases: int = 2,
) -> list[int]:
    """Persist workflow strategy candidates, suppressing active duplicates."""
    inserted_ids: list[int] = []
    for candidate in derive_workflow_strategy_candidates(
        conn,
        user_id=user_id,
        project_id=project_id,
        min_success_cases=min_success_cases,
    ):
        if _active_strategy_candidate_exists(conn, candidate, user_id=user_id, project_id=project_id):
            continue

        content = {
            "scope": "project" if project_id else "user",
            "kind": STRATEGY_CANDIDATE_KIND,
            "task_type": candidate["task_type"],
            "success_evidence_count": str(candidate["success_evidence_count"]),
            "failure_evidence_count": str(candidate["failure_evidence_count"]),
            "first_observed_at": candidate["first_observed_at"],
            "last_observed_at": candidate["last_observed_at"],
            "success_case_memory_ids": ",".join(str(mid) for mid in candidate["success_case_memory_ids"]),
            "failure_case_memory_ids": ",".join(str(mid) for mid in candidate["failure_case_memory_ids"]),
            "trace_memory_ids": ",".join(str(mid) for mid in candidate["trace_memory_ids"]),
            "recommended_steps": candidate["recommended_steps"],
            "known_limits": candidate["known_limits"],
            "verification_signals": candidate["verification_signals"],
            "failure_signals": candidate["failure_signals"],
            "tool_families": candidate["tool_families"],
            "needs_confirmation": "true",
            "confirmed": "false",
        }
        evidence = candidate["evidence"] or [{"source_ref": "workflow-review"}]
        event_id = insert_event(
            conn,
            SourceEvent(
                source_type="event",
                source_ref=_strategy_source_ref(candidate, user_id=user_id, project_id=project_id),
                actors=[user_id] if user_id else [],
                timestamp=utc_now(),
                content=candidate["summary"],
                scope=content["scope"],
                payload={"kind": STRATEGY_CANDIDATE_KIND, "task_type": candidate["task_type"]},
            ),
            project_id=project_id,
            task_id=None,
            user_id=user_id,
        )
        memory_id = insert_memory(
            conn,
            MemoryCandidate(
                memory_type="procedural",
                title=candidate["title"],
                summary=candidate["summary"],
                content=content,
                importance=0.62,
                confidence=_strategy_confidence(candidate),
                evidence=evidence,
                tags=["workflow", STRATEGY_CANDIDATE_KIND, candidate["task_type"]],
                change_reason=f"{STRATEGY_CANDIDATE_KIND}: {candidate['task_type']}",
            ),
            event_id=event_id,
            project_id=project_id,
            task_id=None,
            user_id=user_id,
        )
        insert_event_entry(
            conn,
            EventEntry(
                source_event_id=event_id,
                event_time=utc_now(),
                entry_type="workflow",
                subject=project_id or user_id or "workflow",
                relation="synthesized_workflow_strategy",
                object=candidate["task_type"],
                qualifiers={
                    "memory_id": memory_id,
                    "memory_type": "procedural",
                    "content_kind": STRATEGY_CANDIDATE_KIND,
                    "success_evidence_count": candidate["success_evidence_count"],
                    "failure_evidence_count": candidate["failure_evidence_count"],
                    "trace_evidence_count": candidate["trace_evidence_count"],
                    "verification_signals": candidate["verification_signals"],
                    "failure_signals": candidate["failure_signals"],
                },
                project_id=project_id,
                task_id=None,
                user_id=user_id,
                confidence=_strategy_confidence(candidate),
            ),
        )
        inserted_ids.append(memory_id)
    return inserted_ids


def prune_workflow_strategy_candidate_branches(
    conn,
    user_id: str | None = None,
    project_id: str | None = None,
    max_active: int = _MAX_ACTIVE_STRATEGY_CANDIDATES_PER_TASK_TYPE,
) -> list[int]:
    """Archive lower-value active workflow strategy candidates beyond the per-task cap."""
    rows = conn.execute(
        """
        SELECT id, content_json, confidence, created_at, project_id
        FROM memories
        WHERE memory_type = 'procedural'
          AND status = 'active'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        """,
        (f"%{STRATEGY_CANDIDATE_KIND}%", user_id, user_id, project_id, project_id),
    ).fetchall()

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        content = json.loads(row["content_json"])
        if content.get("kind") != STRATEGY_CANDIDATE_KIND:
            continue
        key = (row["project_id"] or "", str(content.get("task_type") or "agent_workflow"))
        grouped.setdefault(key, []).append(
            {
                "id": int(row["id"]),
                "confidence": float(row["confidence"]),
                "created_at": row["created_at"],
            }
        )

    archived_ids: list[int] = []
    now = utc_now()
    for items in grouped.values():
        if len(items) <= max_active:
            continue
        ranked = sorted(
            items,
            key=lambda item: (item["confidence"], item["created_at"], item["id"]),
            reverse=True,
        )
        for item in ranked[max_active:]:
            conn.execute(
                """
                UPDATE memories
                SET status = 'archived',
                    updated_at = ?,
                    change_reason = ?
                WHERE id = ?
                """,
                (now, f"archived by workflow strategy branch limit max_active={max_active}", item["id"]),
            )
            archived_ids.append(item["id"])
    return archived_ids


def mark_stale_workflow_skills_for_review(
    conn,
    user_id: str | None = None,
    project_id: str | None = None,
    stale_days: int = _STALE_WORKFLOW_SKILL_DAYS,
) -> list[int]:
    """Mark long-unused workflow skills for review without archiving them."""
    now = utc_now()
    cutoff_dt = _parse_time(now) - timedelta(days=stale_days)
    rows = conn.execute(
        """
        SELECT *
        FROM memories
        WHERE memory_type = 'procedural'
          AND status = 'active'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        """,
        (f"%{WORKFLOW_SKILL_KIND}%", user_id, user_id, project_id, project_id),
    ).fetchall()

    marked_ids: list[int] = []
    for row in rows:
        content = json.loads(row["content_json"])
        if content.get("kind") != WORKFLOW_SKILL_KIND or content.get("needs_review") == "true":
            continue
        if _workflow_skill_freshness_anchor(row["created_at"], content) >= cutoff_dt:
            continue

        task_type = str(content.get("task_type") or "agent_workflow")
        content["needs_review"] = "true"
        content["review_reason"] = "workflow skill stale or long unused"
        content["decay_reviewed_at"] = now
        content["decay_stale_days"] = str(stale_days)
        confidence = min(float(row["confidence"]), 0.6)
        event_id = _workflow_skill_review_event(
            conn,
            row,
            now=now,
            user_id=user_id,
            action="mark_stale_for_review",
            task_type=task_type,
        )
        conn.execute(
            """
            UPDATE memories
            SET content_json = ?,
                confidence = ?,
                updated_at = ?,
                change_reason = ?
            WHERE id = ?
            """,
            (
                json.dumps(content, ensure_ascii=True),
                confidence,
                now,
                "workflow skill stale or long unused",
                int(row["id"]),
            ),
        )
        _insert_workflow_skill_review_entry(
            conn,
            row,
            event_id=event_id,
            event_time=now,
            relation="workflow_skill_marked_stale_for_review",
            content=content,
            user_id=user_id,
            confidence=confidence,
        )
        marked_ids.append(int(row["id"]))
    return marked_ids


def confirm_workflow_strategy_candidate(
    conn,
    candidate_id: int,
    user_id: str | None = None,
    ballot_provider: BallotProvider | None = None,
) -> int:
    """Promote a workflow strategy candidate into a stable workflow skill."""
    row = conn.execute(
        """
        SELECT *
        FROM memories
        WHERE id = ?
          AND memory_type = 'procedural'
          AND status = 'active'
        """,
        (candidate_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"unknown workflow strategy candidate: {candidate_id}")

    content = json.loads(row["content_json"])
    if content.get("kind") != STRATEGY_CANDIDATE_KIND:
        raise ValueError(f"memory {candidate_id} is not a workflow strategy candidate")

    governance = review_workflow_strategy_candidate(conn, candidate_id, ballot_provider=ballot_provider)
    if governance["decision"] != "approve":
        raise GovernanceRejected(governance)

    now = utc_now()
    skill_content = dict(content)
    skill_content.update(
        {
            "kind": WORKFLOW_SKILL_KIND,
            "confirmed": "true",
            "needs_confirmation": "false",
            "confirmed_at": now,
            "confirmed_by": user_id or row["user_id"] or "",
            "derived_from_candidate_id": str(candidate_id),
        }
    )
    task_type = str(skill_content.get("task_type") or "agent_workflow")
    event_id = insert_event(
        conn,
        SourceEvent(
            source_type="event",
            source_ref=f"workflow-skill-confirmation:{candidate_id}",
            actors=[user_id or row["user_id"]] if (user_id or row["user_id"]) else [],
            timestamp=now,
            content=f"Confirmed workflow strategy candidate {candidate_id}: {task_type}",
            scope=skill_content.get("scope", row["scope"]),
            payload={"kind": WORKFLOW_SKILL_KIND, "candidate_id": candidate_id, "task_type": task_type},
        ),
        project_id=row["project_id"],
        task_id=row["task_id"],
        user_id=user_id or row["user_id"],
    )
    skill_id = insert_memory(
        conn,
        MemoryCandidate(
            memory_type="procedural",
            title=row["title"].replace("Workflow strategy candidate", "Workflow skill"),
            summary=row["summary"],
            content=skill_content,
            importance=max(float(row["importance"]), 0.75),
            confidence=max(float(row["confidence"]), 0.8),
            evidence=json.loads(row["evidence_json"]),
            tags=_skill_tags(json.loads(row["tags_json"]), task_type),
            replaces_memory_id=candidate_id,
            change_reason=f"confirmed workflow strategy candidate {candidate_id}",
        ),
        event_id=event_id,
        project_id=row["project_id"],
        task_id=row["task_id"],
        user_id=user_id or row["user_id"],
    )
    insert_event_entry(
        conn,
        EventEntry(
            source_event_id=event_id,
            event_time=now,
            entry_type="workflow",
            subject=row["project_id"] or row["user_id"] or "workflow",
            relation="confirmed_workflow_skill",
            object=task_type,
            qualifiers={
                "memory_id": skill_id,
                "memory_type": "procedural",
                "content_kind": WORKFLOW_SKILL_KIND,
                "derived_from_candidate_id": candidate_id,
                **_workflow_behavior_qualifiers(
                    relation="confirmed_workflow_skill",
                    target_memory_id=skill_id,
                    task_type=task_type,
                    content=skill_content,
                ),
            },
            project_id=row["project_id"],
            task_id=row["task_id"],
            user_id=user_id or row["user_id"],
            confidence=max(float(row["confidence"]), 0.8),
        ),
    )
    conn.execute(
        """
        UPDATE memories
        SET status = 'archived',
            updated_at = ?,
            change_reason = ?
        WHERE id = ?
        """,
        (now, f"confirmed into workflow skill {skill_id}", candidate_id),
    )
    conn.execute("UPDATE memories SET logical_layer = 'L2' WHERE id = ?", (skill_id,))
    return skill_id


def reject_workflow_strategy_candidate(conn, candidate_id: int, user_id: str | None = None) -> int:
    """Archive a workflow strategy candidate rejected by the user."""
    row = conn.execute(
        """
        SELECT content_json
        FROM memories
        WHERE id = ?
          AND memory_type = 'procedural'
          AND status = 'active'
        """,
        (candidate_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"unknown workflow strategy candidate: {candidate_id}")

    content = json.loads(row["content_json"])
    if content.get("kind") != STRATEGY_CANDIDATE_KIND:
        raise ValueError(f"memory {candidate_id} is not a workflow strategy candidate")

    conn.execute(
        """
        UPDATE memories
        SET status = 'archived',
            updated_at = ?,
            change_reason = ?
        WHERE id = ?
        """,
        (utc_now(), f"user rejected workflow strategy candidate by {user_id or 'unknown'}", candidate_id),
    )
    return candidate_id


def reconfirm_workflow_skill(conn, skill_id: int, user_id: str | None = None) -> int:
    """Re-confirm a workflow skill that was marked for review."""
    row = conn.execute(
        """
        SELECT *
        FROM memories
        WHERE id = ?
          AND memory_type = 'procedural'
          AND status = 'active'
        """,
        (skill_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"unknown workflow skill: {skill_id}")

    content = json.loads(row["content_json"])
    if content.get("kind") != WORKFLOW_SKILL_KIND:
        raise ValueError(f"memory {skill_id} is not a workflow skill")

    now = utc_now()
    task_type = str(content.get("task_type") or "agent_workflow")
    content["confirmed"] = "true"
    content["needs_confirmation"] = "false"
    content["needs_review"] = "false"
    content["reconfirmed_at"] = now
    content["reconfirmed_by"] = user_id or row["user_id"] or ""
    content.pop("review_reason", None)
    confidence = max(float(row["confidence"]), 0.75)

    event_id = _workflow_skill_review_event(
        conn,
        row,
        now=now,
        user_id=user_id,
        action="reconfirm",
        task_type=task_type,
    )
    conn.execute(
        """
        UPDATE memories
        SET content_json = ?,
            confidence = ?,
            updated_at = ?,
            change_reason = ?
        WHERE id = ?
        """,
        (
            json.dumps(content, ensure_ascii=True),
            confidence,
            now,
            f"workflow skill reconfirmed by {user_id or 'unknown'}",
            skill_id,
        ),
    )
    _insert_workflow_skill_review_entry(
        conn,
        row,
        event_id=event_id,
        event_time=now,
        relation="reconfirmed_workflow_skill",
        content=content,
        user_id=user_id,
        confidence=confidence,
    )
    return skill_id


def reject_workflow_skill(conn, skill_id: int, user_id: str | None = None) -> int:
    """Archive a workflow skill rejected during review."""
    row = conn.execute(
        """
        SELECT *
        FROM memories
        WHERE id = ?
          AND memory_type = 'procedural'
          AND status = 'active'
        """,
        (skill_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"unknown workflow skill: {skill_id}")

    content = json.loads(row["content_json"])
    if content.get("kind") != WORKFLOW_SKILL_KIND:
        raise ValueError(f"memory {skill_id} is not a workflow skill")

    now = utc_now()
    task_type = str(content.get("task_type") or "agent_workflow")
    content["confirmed"] = "false"
    content["needs_review"] = "false"
    content["rejected_at"] = now
    content["rejected_by"] = user_id or row["user_id"] or ""
    content["rejection_reason"] = "user rejected workflow skill during review"
    content.pop("review_reason", None)
    confidence = min(float(row["confidence"]), 0.4)

    event_id = _workflow_skill_review_event(
        conn,
        row,
        now=now,
        user_id=user_id,
        action="reject",
        task_type=task_type,
    )
    conn.execute(
        """
        UPDATE memories
        SET content_json = ?,
            confidence = ?,
            status = 'archived',
            updated_at = ?,
            change_reason = ?
        WHERE id = ?
        """,
        (
            json.dumps(content, ensure_ascii=True),
            confidence,
            now,
            f"workflow skill rejected by {user_id or 'unknown'}",
            skill_id,
        ),
    )
    _insert_workflow_skill_review_entry(
        conn,
        row,
        event_id=event_id,
        event_time=now,
        relation="rejected_workflow_skill",
        content=content,
        user_id=user_id,
        confidence=confidence,
    )
    return skill_id


def record_workflow_skill_outcome(
    conn,
    skill_id: int,
    *,
    outcome: str,
    summary: str,
    evidence: list[dict[str, Any]],
    user_id: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
) -> int:
    """Record explicit adoption feedback for a stable workflow skill."""
    normalized = _normalize_skill_outcome(outcome)
    row = conn.execute(
        """
        SELECT *
        FROM memories
        WHERE id = ?
          AND memory_type = 'procedural'
          AND status = 'active'
        """,
        (skill_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"unknown workflow skill: {skill_id}")

    skill_content = json.loads(row["content_json"])
    if skill_content.get("kind") != WORKFLOW_SKILL_KIND:
        raise ValueError(f"memory {skill_id} is not a workflow skill")

    now = utc_now()
    task_type = str(skill_content.get("task_type") or "agent_workflow")
    event_id = insert_event(
        conn,
        SourceEvent(
            source_type="event",
            source_ref=f"workflow-skill-outcome:{skill_id}:{now}",
            actors=[user_id or row["user_id"]] if (user_id or row["user_id"]) else [],
            timestamp=now,
            content=summary,
            scope=skill_content.get("scope", row["scope"]),
            payload={"kind": WORKFLOW_SKILL_OUTCOME_KIND, "skill_id": skill_id, "outcome": normalized},
        ),
        project_id=project_id or row["project_id"],
        task_id=task_id or row["task_id"],
        user_id=user_id or row["user_id"],
    )
    memory_id = insert_memory(
        conn,
        MemoryCandidate(
            memory_type="procedural",
            title=f"Workflow skill outcome: {task_type} {normalized}",
            summary=summary,
            content={
                "scope": skill_content.get("scope", row["scope"]),
                "kind": WORKFLOW_SKILL_OUTCOME_KIND,
                "workflow_skill_id": str(skill_id),
                "task_type": task_type,
                "outcome": normalized,
                "observed_at": now,
            },
            importance=0.6 if normalized == "failure" else 0.45,
            confidence=0.72,
            evidence=evidence or [{"source_ref": f"workflow-skill:{skill_id}"}],
            tags=["workflow", WORKFLOW_SKILL_OUTCOME_KIND, normalized, task_type],
            change_reason=f"{WORKFLOW_SKILL_OUTCOME_KIND}: {normalized}",
        ),
        event_id=event_id,
        project_id=project_id or row["project_id"],
        task_id=task_id or row["task_id"],
        user_id=user_id or row["user_id"],
    )
    insert_event_entry(
        conn,
        EventEntry(
            source_event_id=event_id,
            event_time=now,
            entry_type="workflow",
            subject=row["project_id"] or row["user_id"] or "workflow",
            relation=_outcome_relation(normalized),
            object=task_type,
            qualifiers={
                "memory_id": memory_id,
                "workflow_skill_id": skill_id,
                "memory_type": "procedural",
                "content_kind": WORKFLOW_SKILL_OUTCOME_KIND,
                "outcome": normalized,
                **_workflow_behavior_qualifiers(
                    relation=_outcome_relation(normalized),
                    target_memory_id=skill_id,
                    task_type=task_type,
                    content=skill_content,
                    outcome=normalized,
                ),
            },
            project_id=project_id or row["project_id"],
            task_id=task_id or row["task_id"],
            user_id=user_id or row["user_id"],
            confidence=0.72,
        ),
    )
    _update_skill_effectiveness(conn, row, skill_content, normalized, now)
    return memory_id


def _infer_task_type(text: str) -> str:
    if re.search(r"pytest|unit test|tests?|benchmark", text):
        return "test_verification_workflow"
    if re.search(r"lint|typecheck|format", text):
        return "quality_gate_workflow"
    if re.search(r"implement|code|patch|refactor|fix", text):
        return "code_change_workflow"
    return "agent_workflow"


def _tool_trace_tags(tool_name: str) -> dict[str, str]:
    lowered = tool_name.lower()
    if "pytest" in lowered or "test" in lowered:
        return {"tool_family": "test", "verification_signal": "tests"}
    if "ruff" in lowered or "lint" in lowered:
        return {"tool_family": "lint", "verification_signal": "lint"}
    if "mypy" in lowered or "pyright" in lowered or "typecheck" in lowered:
        return {"tool_family": "typecheck", "verification_signal": "types"}
    if "benchmark" in lowered:
        return {"tool_family": "benchmark", "verification_signal": "benchmark"}
    return {"tool_family": "tool", "verification_signal": ""}


def _tool_result_diagnostics(*, tool_name: str, tool_output: str, status: str) -> dict[str, str]:
    lowered = tool_output.lower()
    tags = _tool_trace_tags(tool_name)
    exit_code = ""
    if re.search(r"\b(exit code|exited)\s*0\b", lowered) or re.search(r"\b0\s+failed\b", lowered):
        exit_code = "0"
    elif re.search(r"\b(exit code|exited)\s*1\b", lowered) or re.search(r"\b[1-9]\d*\s+failed\b", lowered):
        exit_code = "1"

    failure_type = ""
    failure_signal = ""
    if status == "failed":
        if "traceback" in lowered or "exception" in lowered:
            failure_type = "exception"
            failure_signal = _first_matching_line(tool_output, ("traceback", "exception"))
        elif re.search(r"\b[1-9]\d*\s+failed\b", lowered):
            failure_type = "test_failure"
            failure_signal = _first_matching_line(tool_output, ("failed", "failure"))
        elif "error" in lowered:
            failure_type = "tool_error"
            failure_signal = _first_matching_line(tool_output, ("error",))
        else:
            failure_type = "unknown_failure"
            failure_signal = _compact_text(tool_output, 120)

    return {
        "exit_code": exit_code,
        "failure_type": failure_type,
        "failure_signal": failure_signal,
        "verification_signal": tags["verification_signal"],
    }


def _first_matching_line(text: str, needles: tuple[str, ...]) -> str:
    for line in text.splitlines():
        lowered = line.lower()
        if any(needle in lowered for needle in needles):
            return _compact_text(line, 160)
    return _compact_text(text, 160)


def _collect_step_values(steps: list[dict[str, str]], field: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for step in steps:
        value = str(step.get(field, "")).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _compact_text(text: str, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _flatten_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for item in items:
        for entry in item["evidence"]:
            source_ref = str(entry.get("source_ref", ""))
            if source_ref and source_ref in seen_refs:
                continue
            if source_ref:
                seen_refs.add(source_ref)
            evidence.append(entry)
    return evidence[:12]


def _collect_recommended_steps(successes: list[dict[str, Any]], traces: list[dict[str, Any]] | None = None) -> list[str]:
    steps: list[str] = []
    seen: set[str] = set()
    for item in successes:
        for step in item["content"].get("steps") or []:
            step_text = str(step).strip()
            if not step_text or step_text in seen:
                continue
            seen.add(step_text)
            steps.append(step_text)
            if len(steps) >= 8:
                return steps
    for signal in _collect_trace_list_values(traces or [], "verification_signals"):
        step_text = _verification_step(signal)
        if not step_text or step_text in seen:
            continue
        seen.add(step_text)
        steps.append(step_text)
        if len(steps) >= 8:
            return steps
    for family in _collect_trace_list_values(traces or [], "tool_families"):
        step_text = _tool_family_step(family)
        if not step_text or step_text in seen:
            continue
        seen.add(step_text)
        steps.append(step_text)
        if len(steps) >= 8:
            return steps
    return steps


def _collect_known_limits(failures: list[dict[str, Any]], traces: list[dict[str, Any]] | None = None) -> list[str]:
    limits: list[str] = []
    seen: set[str] = set()
    for item in failures:
        for value in (item["content"].get("root_cause"), item["content"].get("outcome")):
            if not value:
                continue
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            limits.append(text)
            if len(limits) >= 6:
                return limits
    for value in _collect_trace_list_values(traces or [], "failure_signals"):
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        limits.append(text)
        if len(limits) >= 6:
            return limits
    return limits


def _collect_trace_list_values(traces: list[dict[str, Any]], field: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for item in traces:
        raw_values = item["content"].get(field) or []
        if not isinstance(raw_values, list):
            continue
        for raw in raw_values:
            value = str(raw).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            values.append(value)
            if len(values) >= 8:
                return values
    return values


def _verification_step(signal: str) -> str:
    mapping = {
        "tests": "run the relevant tests before claiming completion",
        "lint": "run the relevant lint check before claiming completion",
        "types": "run the relevant type check before claiming completion",
        "benchmark": "run the relevant benchmark before claiming completion",
    }
    return mapping.get(signal, f"verify with {signal}")


def _tool_family_step(family: str) -> str:
    mapping = {
        "test": "inspect test output before finalizing",
        "lint": "inspect lint output before finalizing",
        "typecheck": "inspect type-check output before finalizing",
        "benchmark": "inspect benchmark output before finalizing",
    }
    return mapping.get(family, "")


def _skill_tags(tags: list[str], task_type: str) -> list[str]:
    stable = [tag for tag in tags if tag != STRATEGY_CANDIDATE_KIND]
    for tag in ("workflow_skill", WORKFLOW_SKILL_KIND, task_type):
        if tag not in stable:
            stable.append(tag)
    return stable


def _normalize_skill_outcome(outcome: str) -> str:
    normalized = outcome.strip().lower()
    aliases = {
        "adopted_success": "success",
        "success": "success",
        "succeeded": "success",
        "adopted_failure": "failure",
        "failure": "failure",
        "failed": "failure",
        "override": "override",
        "overridden": "override",
    }
    if normalized not in aliases:
        raise ValueError(f"invalid workflow skill outcome: {outcome}")
    return aliases[normalized]


def _outcome_relation(outcome: str) -> str:
    if outcome == "success":
        return "workflow_skill_succeeded"
    if outcome == "failure":
        return "workflow_skill_failed"
    return "workflow_skill_overridden"


def _workflow_skill_review_event(
    conn,
    row,
    *,
    now: str,
    user_id: str | None,
    action: str,
    task_type: str,
) -> int:
    return insert_event(
        conn,
        SourceEvent(
            source_type="event",
            source_ref=f"workflow-skill-{action}:{int(row['id'])}",
            actors=[user_id or row["user_id"]] if (user_id or row["user_id"]) else [],
            timestamp=now,
            content=f"Workflow skill {action} during review: {task_type}",
            scope=row["scope"],
            payload={
                "kind": WORKFLOW_SKILL_KIND,
                "workflow_skill_id": int(row["id"]),
                "task_type": task_type,
                "action": action,
            },
        ),
        project_id=row["project_id"],
        task_id=row["task_id"],
        user_id=user_id or row["user_id"],
    )


def _insert_workflow_skill_review_entry(
    conn,
    row,
    *,
    event_id: int,
    event_time: str,
    relation: str,
    content: dict[str, Any],
    user_id: str | None,
    confidence: float,
) -> int:
    return insert_event_entry(
        conn,
        EventEntry(
            source_event_id=event_id,
            event_time=event_time,
            entry_type="workflow",
            subject=row["project_id"] or row["user_id"] or "workflow",
            relation=relation,
            object=str(content.get("task_type") or "agent_workflow"),
            qualifiers={
                "memory_id": int(row["id"]),
                "memory_type": "procedural",
                "content_kind": WORKFLOW_SKILL_KIND,
                "task_type": content.get("task_type", ""),
                "usage_count": content.get("usage_count", ""),
                "effectiveness_score": content.get("effectiveness_score", ""),
                **_workflow_behavior_qualifiers(
                    relation=relation,
                    target_memory_id=int(row["id"]),
                    task_type=str(content.get("task_type") or "agent_workflow"),
                    content=content,
                ),
            },
            project_id=row["project_id"],
            task_id=row["task_id"],
            user_id=user_id or row["user_id"],
            confidence=confidence,
        ),
    )


def _update_skill_effectiveness(conn, row, content: dict[str, Any], outcome: str, observed_at: str) -> None:
    success_count = _int_content(content, "adoption_success_count")
    failure_count = _int_content(content, "adoption_failure_count")
    override_count = _int_content(content, "override_count")
    if outcome == "success":
        success_count += 1
    elif outcome == "failure":
        failure_count += 1
    else:
        override_count += 1

    usage_count = success_count + failure_count + override_count
    effectiveness = round(success_count / usage_count, 4) if usage_count else 0.0
    content.update(
        {
            "usage_count": str(usage_count),
            "adoption_success_count": str(success_count),
            "adoption_failure_count": str(failure_count),
            "override_count": str(override_count),
            "effectiveness_score": str(effectiveness),
            "last_outcome_at": observed_at,
        }
    )

    negative_count = failure_count + override_count
    confidence = float(row["confidence"])
    change_reason = row["change_reason"]
    status = row["status"]
    if negative_count >= _SKILL_REVIEW_NEGATIVE_THRESHOLD and effectiveness < 0.5:
        content["needs_review"] = "true"
        content["review_reason"] = "workflow skill negative outcome evidence observed"
        confidence = min(confidence, 0.6)
        change_reason = "workflow skill negative outcome evidence observed"
    if negative_count >= _SKILL_ARCHIVE_NEGATIVE_THRESHOLD and effectiveness < _SKILL_ARCHIVE_EFFECTIVENESS_THRESHOLD:
        content["archived_by_policy"] = "true"
        content["archive_reason"] = "workflow skill repeated negative outcomes"
        status = "archived"
        confidence = min(confidence, 0.4)
        change_reason = "workflow skill archived after repeated negative outcomes"

    conn.execute(
        """
        UPDATE memories
        SET content_json = ?,
            confidence = ?,
            status = ?,
            updated_at = ?,
            change_reason = ?
        WHERE id = ?
        """,
        (json.dumps(content, ensure_ascii=True), confidence, status, observed_at, change_reason, int(row["id"])),
    )


def _workflow_behavior_qualifiers(
    *,
    relation: str,
    target_memory_id: int,
    task_type: str,
    content: dict[str, Any],
    outcome: str = "",
) -> dict[str, str | int]:
    action = {
        "confirmed_workflow_skill": "confirm",
        "reconfirmed_workflow_skill": "reconfirm",
        "rejected_workflow_skill": "reject",
        "workflow_skill_marked_stale_for_review": "mark_review",
        "workflow_skill_succeeded": "record_outcome",
        "workflow_skill_failed": "record_outcome",
        "workflow_skill_overridden": "record_outcome",
    }.get(relation, relation)
    normalized_outcome = outcome
    if not normalized_outcome:
        if action == "reject":
            normalized_outcome = "archived"
        elif action in {"confirm", "reconfirm"}:
            normalized_outcome = "active"
    correction = ""
    if action == "reject":
        correction = content.get("rejection_reason", "user rejected workflow skill")
    elif action == "reconfirm":
        correction = "user reconfirmed workflow skill"
    elif action == "mark_review":
        correction = content.get("review_reason", "workflow skill requires review")
    elif outcome in {"failure", "override"}:
        correction = content.get("review_reason", "")
    return {
        "context_key": task_type,
        "action": action,
        "target_memory_id": target_memory_id,
        "correction": correction,
        "outcome": normalized_outcome,
        "polarity": "negative" if normalized_outcome in {"failure", "override", "archived"} else "positive",
    }


def _workflow_skill_freshness_anchor(created_at: str, content: dict[str, Any]) -> datetime:
    candidates = [
        _parse_time(created_at),
        _parse_time(str(content.get("confirmed_at", ""))),
        _parse_time(str(content.get("reconfirmed_at", ""))),
        _parse_time(str(content.get("last_outcome_at", ""))),
    ]
    return max(candidates)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _int_content(content: dict[str, Any], key: str) -> int:
    try:
        return int(content.get(key, "0") or 0)
    except (TypeError, ValueError):
        return 0


def _active_strategy_candidate_exists(
    conn,
    candidate: dict[str, Any],
    user_id: str | None,
    project_id: str | None,
) -> bool:
    rows = conn.execute(
        """
        SELECT content_json
        FROM memories
        WHERE memory_type = 'procedural'
          AND status = 'active'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        """,
        (f"%{STRATEGY_CANDIDATE_KIND}%", user_id, user_id, project_id, project_id),
    ).fetchall()
    for row in rows:
        content = json.loads(row["content_json"])
        if content.get("kind") == STRATEGY_CANDIDATE_KIND and content.get("task_type") == candidate["task_type"]:
            return True
    return False


def _strategy_source_ref(candidate: dict[str, Any], user_id: str | None, project_id: str | None) -> str:
    owner = user_id or "all-users"
    scope = project_id or "all-projects"
    return f"workflow-review:{owner}:{scope}:{candidate['task_type']}"


def _strategy_confidence(candidate: dict[str, Any]) -> float:
    confidence = 0.4
    confidence += min(float(candidate["success_evidence_count"]) * 0.08, 0.32)
    confidence -= min(float(candidate["failure_evidence_count"]) * 0.04, 0.16)
    return round(max(0.25, min(confidence, 0.78)), 4)
