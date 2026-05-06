from __future__ import annotations

import json
from typing import Any


WORKFLOW_SKILL_KIND = "workflow_skill"


def evaluate_workflow_self_improvement(conn, task_type: str) -> dict[str, Any]:
    """Evaluate whether workflow skill outcomes show measurable improvement."""
    rows = conn.execute(
        """
        SELECT id, title, status, content_json, confidence, updated_at
        FROM memories
        WHERE memory_type = 'procedural'
          AND content_json LIKE '%workflow_skill%'
          AND content_json LIKE ?
        ORDER BY id
        """,
        (f"%{task_type}%",),
    ).fetchall()

    active: list[dict[str, Any]] = []
    retired_or_review: list[dict[str, Any]] = []
    for row in rows:
        try:
            content = json.loads(row["content_json"])
        except Exception:
            continue
        if content.get("kind") != WORKFLOW_SKILL_KIND or str(content.get("task_type")) != task_type:
            continue
        item = _skill_snapshot(row, content)
        if item["status"] == "active" and not item["needs_review"]:
            active.append(item)
        if item["status"] != "active" or item["needs_review"]:
            retired_or_review.append(item)

    best_active = max(active, key=lambda item: item["effectiveness_score"], default=None)
    weakest_retired = min(retired_or_review, key=lambda item: item["effectiveness_score"], default=None)
    improvement_delta = None
    status = "insufficient_evidence"
    if best_active is not None and weakest_retired is not None:
        improvement_delta = round(best_active["effectiveness_score"] - weakest_retired["effectiveness_score"], 4)
        if improvement_delta > 0:
            status = "improved"
        else:
            status = "not_improved"

    return {
        "task_type": task_type,
        "status": status,
        "active_skill_count": len(active),
        "retired_or_review_skill_count": len(retired_or_review),
        "best_active_skill": best_active,
        "weakest_retired_or_review_skill": weakest_retired,
        "improvement_delta": improvement_delta,
        "skills": active + retired_or_review,
    }


def _skill_snapshot(row, content: dict[str, Any]) -> dict[str, Any]:
    success_count = _int_value(content.get("adoption_success_count"))
    failure_count = _int_value(content.get("adoption_failure_count"))
    override_count = _int_value(content.get("override_count"))
    usage_count = _int_value(content.get("usage_count")) or success_count + failure_count + override_count
    if usage_count:
        effectiveness = round(success_count / usage_count, 4)
    else:
        effectiveness = _float_value(content.get("effectiveness_score"))
    return {
        "memory_id": int(row["id"]),
        "title": row["title"],
        "status": row["status"],
        "needs_review": content.get("needs_review") == "true",
        "archived_by_policy": content.get("archived_by_policy") == "true",
        "usage_count": usage_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "override_count": override_count,
        "effectiveness_score": effectiveness,
        "confidence": float(row["confidence"]),
        "updated_at": row["updated_at"],
    }


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
