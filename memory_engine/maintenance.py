from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import utc_now
from .ranking import _overlap_score


# ---------------------------------------------------------------------------
# Constants — promotion thresholds (from spec.md 4.3.3 / 4.4.3.2)
# ---------------------------------------------------------------------------

_PROMOTE_B_SAME_THEME = 3   # L1→L2 Direction B: same-theme decisions ≥ 3
_PROMOTE_B_CROSS_TASK = 2   # L1→L2 Direction B: cross-task recalls ≥ 2
_PROMOTE_C_CROSS_SCENE = 2  # L1→L2 Direction C: cross-scene consistency ≥ 2 projects
_PROMOTE_C_PERSISTENCE_DAYS = 7  # L1→L2 Direction C: persistence ≥ 7 days
_DEMOTE_INACTIVE_DAYS = 30  # demote after 30 days without recall
_LOW_VALUE_IMPORTANCE = 0.3  # demote threshold for importance


# ---------------------------------------------------------------------------
# Near-duplicate merge
# -------------------------------------------------------------------------

def merge_near_duplicates(conn: sqlite3.Connection, limit: int = 500) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT id, uuid, memory_type, scope, title, summary, confidence, importance
        FROM memories
        WHERE status = 'active'
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    merged = 0
    details: list[dict[str, Any]] = []
    seen: set[int] = set()

    for i, row_a in enumerate(rows):
        if row_a["id"] in seen:
            continue
        for row_b in rows[i + 1:]:
            if row_b["id"] in seen:
                continue
            if row_a["memory_type"] != row_b["memory_type"] or row_a["scope"] != row_b["scope"]:
                continue
            text_a = row_a["title"] + " " + row_a["summary"]
            text_b = row_b["title"] + " " + row_b["summary"]
            if _overlap_score(text_a, text_b) > 0.8:
                score_a = float(row_a["confidence"]) * float(row_a["importance"])
                score_b = float(row_b["confidence"]) * float(row_b["importance"])
                keep_id = int(row_a["id"]) if score_a >= score_b else int(row_b["id"])
                remove_id = int(row_b["id"]) if keep_id == int(row_a["id"]) else int(row_a["id"])
                keeper_uuid = row_a["uuid"] if keep_id == int(row_a["id"]) else row_b["uuid"]
                conn.execute(
                    "UPDATE memories SET status = 'superseded', superseded_by = ?, change_reason = ?, updated_at = ? WHERE id = ?",
                    (keeper_uuid, "compact: near-duplicate merge", utc_now(), remove_id),
                )
                seen.add(remove_id)
                merged += 1
                details.append({"action": "merged", "removed_id": remove_id, "kept_id": keep_id})

    return {"merged": merged, "details": details}


# ---------------------------------------------------------------------------
# Stale / low-value archive
# -------------------------------------------------------------------------

def archive_stale_low_value(conn: sqlite3.Connection) -> dict[str, Any]:
    now_str = utc_now()
    now_dt = datetime.fromisoformat(now_str)
    cutoff = now_dt.replace(tzinfo=None) if now_dt.tzinfo else now_dt
    cutoff_30d = (cutoff - timedelta(days=30)).isoformat()

    rows = conn.execute(
        """
        SELECT m.id, m.importance, m.confidence, m.created_at
        FROM memories m
        WHERE m.status = 'active' AND m.created_at < ?
        """,
        (cutoff_30d,),
    ).fetchall()

    archived = 0
    details: list[dict[str, Any]] = []

    for row in rows:
        if float(row["importance"]) >= 0.4 or float(row["confidence"]) >= 0.6:
            continue
        stats = get_recall_stats_for_memory(conn, int(row["id"]))
        last = stats["last_recalled_at"]
        if last is not None and last >= cutoff_30d:
            continue
        conn.execute(
            "UPDATE memories SET status = 'archived', change_reason = ?, updated_at = ? WHERE id = ?",
            ("compact: stale low value, not recently recalled", now_str, int(row["id"])),
        )
        archived += 1
        details.append({"action": "archived_stale", "memory_id": int(row["id"])})

    return {"archived": archived, "details": details}


# ---------------------------------------------------------------------------
# Expired working memory
# -------------------------------------------------------------------------

def expire_old_working(conn: sqlite3.Connection) -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = conn.execute(
        """
        SELECT id FROM memories
        WHERE status = 'active'
          AND memory_layer = 'working'
          AND created_at < ?
          AND promotion_candidate = 0
        LIMIT 50
        """,
        (cutoff,),
    ).fetchall()

    expired = 0
    details: list[dict[str, Any]] = []
    now_str = utc_now()
    for row in rows:
        conn.execute(
            "UPDATE memories SET status = 'archived', change_reason = ?, updated_at = ? WHERE id = ?",
            ("compact: expired working memory", now_str, int(row["id"])),
        )
        expired += 1
        details.append({"action": "expired_working", "memory_id": int(row["id"])})
    return {"expired": expired, "details": details}


# ---------------------------------------------------------------------------
# Recall stats helper (standalone, used by promotion/demotion logic)
# -------------------------------------------------------------------------

def get_recall_stats_for_memory(conn: sqlite3.Connection, memory_id: int) -> dict[str, Any]:
    """Query recall_log for usage signals for a specific memory."""
    row = conn.execute(
        """
        SELECT COUNT(*) as recall_count,
               SUM(CASE WHEN was_returned = 1 THEN 1 ELSE 0 END) as returned_count,
               AVG(raw_score) as avg_score,
               MAX(recalled_at) as last_recalled_at,
               COUNT(DISTINCT query) as unique_queries,
               COUNT(DISTINCT task_id) as unique_tasks
        FROM recall_log
        WHERE memory_id = ? AND was_returned IN (0, 1)
        """,
        (memory_id,),
    ).fetchone()

    # Distinct projects for which this memory was returned
    project_rows = conn.execute(
        """
        SELECT COUNT(DISTINCT project_id) as distinct_projects
        FROM recall_log
        WHERE memory_id = ? AND was_returned = 1 AND project_id IS NOT NULL
        """,
        (memory_id,),
    ).fetchone()

    return {
        "recall_count": int(row["recall_count"]) if row["recall_count"] else 0,
        "returned_count": int(row["returned_count"]) if row["returned_count"] else 0,
        "avg_score": round(float(row["avg_score"]), 4) if row["avg_score"] else 0.0,
        "last_recalled_at": row["last_recalled_at"],
        "unique_queries": int(row["unique_queries"]) if row["unique_queries"] else 0,
        "unique_tasks": int(row["unique_tasks"]) if row["unique_tasks"] else 0,
        "distinct_projects": int(project_rows["distinct_projects"]) if project_rows["distinct_projects"] else 0,
    }


# ---------------------------------------------------------------------------
# L1 → L2 promotion
# -------------------------------------------------------------------------

def promote_l1_to_l2(
    conn: sqlite3.Connection,
    memory_id: int,
) -> dict[str, Any] | None:
    """
    Check if a L1 memory meets promotion criteria and update its logical_layer to L2.

    Direction B (decision/task_status): same-theme decisions ≥ 3, OR cross-task recalls ≥ 2.
    Direction C (preference): cross-scene consistency ≥ 2 projects, OR persistence ≥ 7 days.

    Returns a dict with promotion details if promoted, None if not eligible.
    Does NOT commit — caller is responsible for transaction.
    """
    row = conn.execute(
        "SELECT * FROM memories WHERE id = ? AND status = 'active' AND logical_layer = 'L1'",
        (memory_id,),
    ).fetchone()
    if not row:
        return None

    confidence = float(row["confidence"])
    if confidence < 0.6:
        return None

    stats = get_recall_stats_for_memory(conn, memory_id)
    mem_type = row["memory_type"]

    # Determine direction: B (decision/task_status) vs C (preference)
    if mem_type in ("decision", "task_status"):
        # Direction B: same-theme decisions ≥ 3
        same_theme_count = stats["returned_count"]
        # Direction B: cross-task recalls ≥ 2
        cross_task_count = stats["unique_tasks"]

        if same_theme_count >= _PROMOTE_B_SAME_THEME:
            _do_promote_l2(conn, memory_id, "B", "same_theme_decisions_3", confidence >= 0.6)
            return _build_result(memory_id, "L1", "L2", "B", "same_theme_decisions_3", confidence >= 0.6)
        if cross_task_count >= _PROMOTE_B_CROSS_TASK:
            _do_promote_l2(conn, memory_id, "B", "cross_task_2", confidence >= 0.6)
            return _build_result(memory_id, "L1", "L2", "B", "cross_task_2", confidence >= 0.6)

    elif mem_type == "preference":
        # Direction C: cross-scene consistency ≥ 2 projects
        if stats["distinct_projects"] >= _PROMOTE_C_CROSS_SCENE:
            _do_promote_l2(conn, memory_id, "C", "cross_scene_2", confidence >= 0.6)
            return _build_result(memory_id, "L1", "L2", "C", "cross_scene_2", confidence >= 0.6)

        # Direction C: persistence ≥ 7 days
        created = datetime.fromisoformat(row["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created).total_seconds() / 86400
        if age_days >= _PROMOTE_C_PERSISTENCE_DAYS:
            _do_promote_l2(conn, memory_id, "C", f"persistent_{_PROMOTE_C_PERSISTENCE_DAYS}d", confidence >= 0.6)
            return _build_result(memory_id, "L1", "L2", "C", f"persistent_{_PROMOTE_C_PERSISTENCE_DAYS}d", confidence >= 0.6)

    return None


def _do_promote_l2(
    conn: sqlite3.Connection,
    memory_id: int,
    direction: str,
    trigger: str,
    confidence_passed: bool,
) -> None:
    now = utc_now()
    conn.execute(
        "UPDATE memories SET logical_layer = 'L2', last_reviewed_at = ?, updated_at = ? WHERE id = ?",
        (now, now, memory_id),
    )


# ---------------------------------------------------------------------------
# L2 → L3 promotion
# -------------------------------------------------------------------------

def promote_l2_to_l3(
    conn: sqlite3.Connection,
    memory_id: int,
) -> dict[str, Any] | None:
    """
    Check if a L2 memory meets L2→L3 criteria.

    Direction B: multi-role reference (actors ≥ 2 in source event), OR
                 task_status memory with workflow keywords in summary.
    Direction C: no evidence conflict AND pending user feedback (Phase 3 requirement).

    Returns a dict with promotion details if promoted, None if not eligible.
    Does NOT commit — caller is responsible for transaction.
    """
    row = conn.execute(
        "SELECT * FROM memories WHERE id = ? AND status = 'active' AND logical_layer = 'L2'",
        (memory_id,),
    ).fetchone()
    if not row:
        return None

    confidence = float(row["confidence"])
    if confidence < 0.6:
        return None

    mem_type = row["memory_type"]

    if mem_type in ("decision", "task_status"):
        # Direction B: multi-role reference
        if row["source_event_id"] is not None:
            event_row = conn.execute(
                "SELECT actors_json FROM events WHERE id = ?",
                (int(row["source_event_id"]),),
            ).fetchone()
            if event_row:
                try:
                    actors = json.loads(event_row["actors_json"])
                    if isinstance(actors, list) and len(actors) >= 2:
                        _do_promote_l3(conn, memory_id, "B", "multi_role_reference", confidence >= 0.6)
                        return _build_result(memory_id, "L2", "L3", "B", "multi_role_reference", confidence >= 0.6)
                except (ValueError, TypeError):
                    pass

        # Direction B: task_status with workflow keywords
        if mem_type == "task_status":
            summary_lower = row["summary"].lower()
            workflow_keywords = {
                "\u6d41\u7a0b",
                "\u6d41\u7a0b\u56fe",
                "\u6b65\u9aa4",
                "\u6807\u51c6\u64cd\u4f5c",
                "step",
                "workflow",
                "sop",
            }
            if any(kw in summary_lower for kw in workflow_keywords):
                _do_promote_l3(conn, memory_id, "B", "workflow_keyword", confidence >= 0.6)
                return _build_result(memory_id, "L2", "L3", "B", "workflow_keyword", confidence >= 0.6)

    elif mem_type == "habit_rule":
        # Direction C: no evidence conflict, but pending feedback (Phase 3 requirement)
        # Check there are no EVIDENCE_CONFLICT audit entries for this memory
        conflict_entry = conn.execute(
            """
            SELECT id FROM audit_log
            WHERE target_id = ? AND action IN ('write', 'update')
              AND detail LIKE '%evidence_conflict%'
            LIMIT 1
            """,
            (memory_id,),
        ).fetchone()
        if not conflict_entry:
            # No conflict found; requires Phase 3 user feedback signal to confirm
            result = _build_result(memory_id, "L2", "L3", "C", "pending_feedback", confidence >= 0.6)
            result["pending_feedback"] = True
            return result

    return None


def _do_promote_l3(
    conn: sqlite3.Connection,
    memory_id: int,
    direction: str,
    trigger: str,
    confidence_passed: bool,
) -> None:
    now = utc_now()
    conn.execute(
        "UPDATE memories SET logical_layer = 'L3', last_reviewed_at = ?, updated_at = ? WHERE id = ?",
        (now, now, memory_id),
    )


# ---------------------------------------------------------------------------
# Demotion
# -------------------------------------------------------------------------

def demote_low_value(
    conn: sqlite3.Connection,
    memory_id: int,
) -> dict[str, Any] | None:
    """
    Check if a memory should be demoted based on inactivity or low value.

    Conditions (any one triggers demotion):
    - last_recalled_at is None OR older than 30 days (regardless of returned_count)
    - importance < 0.3 AND returned_count == 0

    Returns a dict with demotion details if demoted, None if not eligible.
    Does NOT commit — caller is responsible for transaction.
    """
    row = conn.execute(
        "SELECT * FROM memories WHERE id = ? AND status = 'active'",
        (memory_id,),
    ).fetchone()
    if not row:
        return None

    stats = get_recall_stats_for_memory(conn, memory_id)
    last_recalled = stats["last_recalled_at"]
    importance = float(row["importance"])
    mem_type = row["memory_type"]
    if mem_type == "preference":
        try:
            content = json.loads(row["content_json"] or "{}")
        except Exception:
            content = {}
        if content.get("kind") == "stable_preference":
            return None
    if mem_type == "procedural":
        try:
            content = json.loads(row["content_json"] or "{}")
        except Exception:
            content = {}
        if content.get("kind") == "workflow_skill":
            return None
    created_at = datetime.fromisoformat(row["created_at"])
    if created_at.tzinfo:
        created_at = created_at.replace(tzinfo=None)

    now_str = utc_now()
    now_dt = datetime.fromisoformat(now_str)
    if now_dt.tzinfo:
        now_dt = now_dt.replace(tzinfo=None)
    cutoff_dt = now_dt - timedelta(days=_DEMOTE_INACTIVE_DAYS)
    cutoff = cutoff_dt.isoformat()

    demoted_reason = ""

    # Condition 2: preference-specific decay — checked before generic long_inactive
    # so the more specific reason is attributed first.
    if mem_type == "preference" and last_recalled is not None and last_recalled < cutoff:
        demoted_reason = "preference_decay_30d"
    # Condition 3: low value with zero usage after enough time to observe usage.
    elif importance < _LOW_VALUE_IMPORTANCE and stats["returned_count"] == 0 and created_at < cutoff_dt:
        demoted_reason = "low_value_no_usage"
    # Condition 4: long-term inactive after at least one historical recall.
    elif last_recalled is not None and last_recalled < cutoff:
        demoted_reason = "long_inactive"
    # Condition 5: never recalled and old enough to be considered stale.
    elif last_recalled is None and created_at < cutoff_dt:
        demoted_reason = "long_inactive"

    if not demoted_reason:
        return None

    conn.execute(
        "UPDATE memories SET status = 'archived', change_reason = ?, updated_at = ? WHERE id = ?",
        (f"demote: {demoted_reason}", now_str, memory_id),
    )

    return {
        "memory_id": memory_id,
        "action": "demoted",
        "reason": demoted_reason,
        "last_recalled_at": last_recalled,
        "returned_count": stats["returned_count"],
    }


# ---------------------------------------------------------------------------
# Shared helpers
# -------------------------------------------------------------------------

def _build_result(
    memory_id: int,
    from_layer: str,
    to_layer: str,
    direction: str,
    trigger: str,
    confidence_passed: bool,
) -> dict[str, Any]:
    return {
        "memory_id": memory_id,
        "from_layer": from_layer,
        "to_layer": to_layer,
        "direction": direction,
        "trigger": trigger,
        "confidence_passed": confidence_passed,
        "timestamp": utc_now(),
    }
