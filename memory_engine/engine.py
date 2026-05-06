from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from .models import EventEntry, MemoryCandidate, MemoryLayer, RecallContext, RecallRequest, SourceEvent, utc_now
from .guard import AuditAction, scan_and_mask
from .governance import BallotProvider, GovernanceRejected, review_memory_promotion, review_preference_candidate
from .conflicts import classify_conflict
from .maintenance import (
    archive_stale_low_value,
    demote_low_value,
    expire_old_working,
    get_recall_stats_for_memory,
    merge_near_duplicates,
    promote_l1_to_l2,
    promote_l2_to_l3,
)
from .implicit_preferences import (
    CANDIDATE_KIND,
    OBSERVATION_KIND,
    confirm_preference_candidate as confirm_implicit_preference_candidate,
    mark_stale_stable_preferences_for_review,
    materialize_preference_candidates,
    prune_preference_candidate_branches,
    reconfirm_stable_preference as reconfirm_implicit_stable_preference,
    reject_preference_candidate as reject_implicit_preference_candidate,
    reject_stable_preference as reject_implicit_stable_preference,
)
from .workflows import (
    FAILURE_CASE_KIND,
    STRATEGY_CANDIDATE_KIND,
    SUCCESS_CASE_KIND,
    TRACE_KIND,
    WORKFLOW_SKILL_KIND,
    WORKFLOW_SKILL_OUTCOME_KIND,
    confirm_workflow_strategy_candidate as confirm_workflow_strategy,
    materialize_workflow_strategy_candidates,
    mark_stale_workflow_skills_for_review,
    prune_workflow_strategy_candidate_branches,
    record_workflow_skill_outcome as record_workflow_outcome,
    reconfirm_workflow_skill as reconfirm_workflow_skill_review,
    reject_workflow_strategy_candidate as reject_workflow_strategy,
    reject_workflow_skill as reject_workflow_skill_review,
)
from .storage import init_db, insert_event, insert_event_entry, insert_memory
from .self_improvement import evaluate_workflow_self_improvement
from .ranking import (
    _MIN_SCORE,
    _TOP_K,
    _WEIGHT_CONFIDENCE,
    _WEIGHT_FRESHNESS,
    _WEIGHT_IMPORTANCE,
    _WEIGHT_RELEVANCE,
    _compute_lexical_stats,
    _freshness_score,
    _lexical_score,
    _mmr_diversity,
    _overlap_score,
    _tokenize,
)


# ---------------------------------------------------------------------------
# #4 Write gate thresholds
# ---------------------------------------------------------------------------

_GATE_MIN_SUMMARY_LEN = 5
_GATE_DUPLICATE_OVERLAP = 0.85
_GATE_LOW_CONFIDENCE = 0.3
_SENSITIVE_FIELD_NAMES = {"api_key", "apikey", "access_token", "secret_key", "password", "passwd", "pwd", "token"}
_SINGLE_INTENT_RELATIVE_SCORE_CUTOFF = 0.65
_SINGLE_INTENT_SCORE_FILTERS = {
    "decision_support",
    "project_summary",
    "risk_scan",
    "deadline_lookup",
    "stakeholder_lookup",
}
_BROAD_CONTEXT_INTENTS = {
    "context",
    "context_assembly",
    "context_recovery",
    "decision_support",
    "fact_lookup",
    "multi_hop",
    "project_summary",
    "recovery",
    "risk_assessment",
    "risk_scan",
    "synthesis",
    "task_status",
}

# Keep CJK query terms as escapes so Windows console encoding cannot corrupt them.
_HISTORY_QUERY_TERMS = {
    "history", "historical", "previous", "old", "older", "past",
    "superseded", "version", "versions", "chain", "evolution",
    "\u5386\u53f2", "\u4e4b\u524d", "\u65e7", "\u65e7\u7248",
    "\u7248\u672c", "\u7248\u672c\u94fe", "\u53d8\u66f4", "\u6f14\u5316",
}
_BROAD_CONTEXT_QUERY_TERMS = {
    "complete", "context", "everything", "full", "handover", "resume",
    "recovery", "synthesize", "summary",
    "\u73b0\u72b6", "\u80cc\u666f", "\u4e0a\u4e0b\u6587", "\u60c5\u51b5",
    "\u603b\u7ed3", "\u6458\u8981", "\u63a5\u624b", "\u4ea4\u63a5", "\u6062\u590d",
}
_TASK_SEMANTIC_QUERY_TERMS = {
    "constraint", "constraints", "deadline", "deadlines", "deploy", "deployment",
    "error", "preference", "preferences", "risk", "risks", "workflow",
    "\u7ea6\u675f", "\u622a\u6b62", "\u65f6\u95f4", "\u91cc\u7a0b\u7891",
    "\u90e8\u7f72", "\u53d1\u5e03", "\u98ce\u9669", "\u504f\u597d",
    "\u6d41\u7a0b", "\u7b56\u7565", "\u4e0b\u4e00\u6b65",
}


def _is_history_query(query: str, intent: str) -> bool:
    if intent in {"history", "audit", "version_history", "decision_history"}:
        return True
    query_l = query.lower()
    tokens = _normalized_tokens(query)
    return any(term in query_l for term in _HISTORY_QUERY_TERMS) or bool(tokens & _HISTORY_QUERY_TERMS)


def _is_stale_preference(item: dict[str, Any]) -> bool:
    content = item.get("content") or {}
    tags = {str(tag).lower() for tag in item.get("tags", [])}
    status = str(content.get("status", "")).lower()
    return status == "stale" or "stale" in tags


def _is_superseded_decision(item: dict[str, Any]) -> bool:
    content = item.get("content") or {}
    tags = {str(tag).lower() for tag in item.get("tags", [])}
    current = content.get("current")
    superseded = content.get("superseded")
    return current is False or superseded is True or "superseded" in tags


def _memory_visibility(item: dict[str, Any]) -> str:
    content = item.get("content") or {}
    visibility = str(content.get("visibility", "")).lower() if isinstance(content, dict) else ""
    if visibility in {"private", "project", "org"}:
        return visibility
    if item.get("memory_type") == "preference" or item.get("scope") == "user":
        return "private"
    if item.get("scope") == "org":
        return "org"
    return "project"


def _is_visible_to_request(item: dict[str, Any], request: RecallRequest) -> bool:
    visibility = _memory_visibility(item)
    if visibility == "private":
        if not request.user_id or item.get("user_id") != request.user_id:
            return False
        content = item.get("content") or {}
        item_project_id = None
        if isinstance(content, dict):
            item_project_id = content.get("project_id") or content.get("project_scope")
        if request.project_id and item_project_id and item_project_id != request.project_id:
            return False
        return True
    if visibility == "project":
        return (
            request.project_id is None
            or item.get("project_id") is None
            or item.get("project_id") == request.project_id
        )
    return True


def _parse_memory_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def _is_effective_now(item: dict[str, Any], now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    content = item.get("content") or {}
    valid_from = item.get("valid_from")
    valid_until = item.get("valid_until")
    if isinstance(content, dict):
        valid_from = valid_from or content.get("valid_from")
        valid_until = valid_until or content.get("valid_until")

    starts_at = _parse_memory_time(valid_from)
    ends_at = _parse_memory_time(valid_until)
    if starts_at is not None and current < starts_at:
        return False
    if ends_at is not None and current >= ends_at:
        return False
    return True


def _normalized_tokens(text: str) -> set[str]:
    return {token.lower() for token in _tokenize(text)}


def _has_entity_anchor(query: str, item: dict[str, Any]) -> bool:
    """Require a concrete project/task/user anchor before weak semantic fallback.

    This keeps zero-result refusal strict: generic absent topics such as
    "time travel approval workflow" may share a workflow word, but they should
    not retrieve project memories unless the query also names the scoped entity.
    """
    query_tokens = _normalized_tokens(query)
    if not query_tokens:
        return False
    if ({"project", "\u9879\u76ee"} & query_tokens):
        if item.get("project_id") or item.get("scope") == "project":
            return True
    anchors: set[str] = set()
    for key in ("project_id", "task_id", "user_id"):
        value = item.get(key)
        if value:
            anchors.update(_normalized_tokens(str(value)))
            value_text = str(value).lower()
            if value_text.startswith("proj_"):
                anchors.update(_normalized_tokens(value_text.removeprefix("proj_")))
            if value_text.startswith("project_"):
                anchors.update(_normalized_tokens(value_text.removeprefix("project_")))
    content = item.get("content") or {}
    if isinstance(content, dict):
        for key in ("project", "task", "user", "decided_by"):
            value = content.get(key)
            if value:
                anchors.update(_normalized_tokens(str(value)))
    return bool(query_tokens & anchors)


def _has_broad_context_signal(query: str, intent: str) -> bool:
    tokens = _normalized_tokens(query)
    return (
        intent in _BROAD_CONTEXT_INTENTS
        or bool(tokens & _BROAD_CONTEXT_QUERY_TERMS)
        or bool(tokens & _TASK_SEMANTIC_QUERY_TERMS)
    )


def _semantic_query_allows_item(query: str, item: dict[str, Any], intent: str) -> bool:
    tokens = _normalized_tokens(query)
    memory_type = str(item.get("memory_type", "")).lower()
    item_tokens = _normalized_tokens(
        " ".join(
            [
                str(item.get("title", "")),
                str(item.get("summary", "")),
                " ".join(str(tag) for tag in item.get("tags", [])),
            ]
        )
    )
    if tokens & {"preference", "preferences", "private", "\u504f\u597d"}:
        return memory_type == "preference"

    if intent in {"context_recovery", "task_status"} and memory_type == "task_status":
        return True

    if tokens & {"risk", "risks", "\u98ce\u9669"}:
        return bool(item_tokens & {"risk", "postmortem", "payment", "\u98ce\u9669", "\u6559\u8bad", "\u56de\u6eda", "\u7f13\u89e3", "\u5f00\u59cb", "\u56de\u590d"}) or memory_type in {"decision", "episodic", "task_status"}

    if tokens & {"deploy", "deployment", "workflow", "\u90e8\u7f72", "\u53d1\u5e03", "\u6d41\u7a0b"}:
        if tokens & {"constraint", "constraints", "\u7ea6\u675f"}:
            return memory_type == "decision"
        return memory_type == "procedural" or bool(item_tokens & {"deployment", "deploy", "workflow", "\u90e8\u7f72", "\u53d1\u5e03"})

    if tokens & {"constraint", "constraints", "\u7ea6\u675f"}:
        return memory_type == "decision" or bool(item_tokens & {"deadline", "security", "constraint", "\u622a\u6b62", "\u5b89\u5168", "\u7ea6\u675f"})

    if tokens & {"error", "recovery", "similar", "\u6062\u590d", "\u9519\u8bef", "\u7c7b\u4f3c"}:
        return memory_type in {"decision", "episodic"}

    if tokens & {"deadline", "deadlines", "milestone", "\u622a\u6b62", "\u65f6\u95f4", "\u91cc\u7a0b\u7891"}:
        return bool(item_tokens & {"deadline", "milestone", "\u622a\u6b62", "\u91cc\u7a0b\u7891"})

    broad_context = bool(tokens & _BROAD_CONTEXT_QUERY_TERMS) or intent in {
        "context",
        "context_assembly",
        "context_recovery",
        "multi_hop",
        "project_summary",
        "synthesis",
    }
    if broad_context:
        return True

    return True


def _has_recall_evidence(query: str, item: dict[str, Any], intent: str = "general") -> bool:
    if not query.strip():
        return True
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    overlap = _overlap_score(query, text)
    if float(item.get("relevance_raw", 0.0)) <= 0.0 or overlap <= 0.0:
        return False
    if intent in {"multi_hop", "synthesis", "context_assembly"}:
        return True
    if _has_entity_anchor(query, item) and _has_broad_context_signal(query, intent):
        return _semantic_query_allows_item(query, item, intent)
    if (
        _has_entity_anchor(query, item)
        and _has_broad_context_signal(query, intent)
        and _semantic_query_allows_item(query, item, intent)
    ):
        return True
    query_token_count = len(_tokenize(query))
    if query_token_count >= 3 and overlap < 0.33:
        return False
    return True


def _with_engine_lock(method: Any) -> Any:
    @wraps(method)
    def wrapper(self: "MemoryEngine", *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class MemoryEngine:
    def __init__(
        self,
        db_path: str | Path = "memory_engine.sqlite3",
        governance_ballot_provider: BallotProvider | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        self._lexical_stats_cache: dict[str, Any] | None = None
        self.governance_ballot_provider = governance_ballot_provider
        self._init_db()

    @_with_engine_lock
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> MemoryEngine:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # DB initialization
    # ------------------------------------------------------------------ #

    def _init_db(self) -> None:
        init_db(self.conn)

    # ------------------------------------------------------------------ #
    # Public API: write
    # ------------------------------------------------------------------ #

    @_with_engine_lock
    def write(
        self,
        event: SourceEvent,
        memory_candidates: list[MemoryCandidate],
        project_id: str | None = None,
        task_id: str | None = None,
        user_id: str | None = None,
        memory_layer: str = "factual",
    ) -> dict[str, Any]:
        self.conn.execute("SAVEPOINT write_tx")
        try:
            self._validate_memory_layer(memory_layer)
            privacy_warnings: list[dict[str, Any]] = []
            event = self._scan_event(event, privacy_warnings)

            event_id = self._insert_event(event, project_id=project_id, task_id=task_id, user_id=user_id)
            memory_ids: list[int] = []
            conflicts_found: list[dict[str, Any]] = []
            skipped: list[dict[str, Any]] = []

            for candidate in memory_candidates:
                candidate = self._scan_candidate(candidate, privacy_warnings)
                conflict = self._detect_conflict(candidate, project_id=project_id, task_id=task_id, user_id=user_id)
                if conflict:
                    conflicts_found.append(conflict)

                # #4: write gate
                gate = self._should_store(candidate, project_id=project_id, task_id=task_id, user_id=user_id)
                if gate["action"] == "reject":
                    skipped.append({"candidate": candidate.title, "action": "reject", "reason": gate["reason"]})
                    continue
                if gate["action"] == "skip":
                    skipped.append({"candidate": candidate.title, "action": "skip", "reason": gate["reason"]})
                    continue

                # apply importance modifier from gate (e.g. low confidence penalty)
                importance_modifier = gate.get("importance_modifier", 1.0)
                if importance_modifier != 1.0:
                    candidate = MemoryCandidate(
                        memory_type=candidate.memory_type,
                        title=candidate.title,
                        summary=candidate.summary,
                        content=candidate.content,
                        importance=candidate.importance * importance_modifier,
                        confidence=candidate.confidence,
                        evidence=candidate.evidence,
                        tags=candidate.tags,
                        replaces_memory_id=candidate.replaces_memory_id,
                        change_reason=candidate.change_reason,
                    )

                mid = self._insert_memory(
                    candidate,
                    event_id=event_id,
                    project_id=project_id,
                    task_id=task_id,
                    user_id=user_id,
                    memory_layer=memory_layer,
                )
                memory_ids.append(mid)
                self._insert_event_entry_for_memory(
                    candidate,
                    memory_id=mid,
                    event_id=event_id,
                    event=event,
                    project_id=project_id,
                    task_id=task_id,
                    user_id=user_id,
                )
                if conflict:
                    self._apply_conflict_resolution(conflict, mid)

            # audit: event write
            self._log_audit(
                action=AuditAction.WRITE,
                target_type="event",
                target_id=event_id,
                actor=user_id or "",
                detail=f"wrote event with {len(memory_ids)} memories",
                sensitive_detections=len(privacy_warnings),
            )
            # audit: per-memory write
            for mid in memory_ids:
                self._log_audit(
                    action=AuditAction.WRITE,
                    target_type="memory",
                    target_id=mid,
                    actor=user_id or "",
                    detail="memory written via write()",
                    sensitive_detections=0,
                )

            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT write_tx")
            self.conn.commit()

            result = {"event_id": event_id, "memory_ids": memory_ids, "conflicts": conflicts_found, "skipped": skipped}
            if privacy_warnings:
                result["privacy_warnings"] = privacy_warnings
            return result
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT write_tx")
            self.conn.execute("RELEASE SAVEPOINT write_tx")
            raise

    # ------------------------------------------------------------------ #
    # Public API: recall
    # ------------------------------------------------------------------ #

    @_with_engine_lock
    def recall(self, request: RecallRequest, limit: int = _TOP_K) -> list[dict[str, Any]]:
        if request.memory_layer is not None:
            self._validate_memory_layer(request.memory_layer)
        if request.logical_layer is not None:
            self._validate_logical_layer(request.logical_layer)

        # P1: query rewrite (Phase 1: identity pass-through)
        last_queries = self._get_recent_queries(
            user_id=request.user_id,
            project_id=request.project_id,
            task_id=request.task_id,
            limit=5,
        )
        ctx = RecallContext(
            user_id=request.user_id,
            project_id=request.project_id,
            task_id=request.task_id,
            intent=request.intent,
            last_queries=last_queries,
        )
        rewritten_query = self._rewrite_query(request.query, ctx)

        # P1: optional memory_layer filter
        layer_clause = ""
        layer_params: list[Any] = []
        if request.memory_layer is not None:
            layer_clause = " AND memory_layer = ?"
            layer_params = [request.memory_layer]

        # P2: optional logical_layer filter
        logical_layer_clause = ""
        logical_layer_params: list[Any] = []
        if request.logical_layer is not None:
            logical_layer_clause = " AND logical_layer = ?"
            logical_layer_params = [request.logical_layer]

        include_workflow_candidates = request.include_candidates or request.intent in {
            "workflow",
            "procedural",
            "strategy_reuse",
        }

        rows = self.conn.execute(
            """
            SELECT *
            FROM memories
            WHERE status = 'active'
              AND (
                    ? IS NULL
                    OR user_id = ?
                    OR (
                        memory_type != 'preference'
                        AND scope != 'user'
                    )
                  )
              AND (
                    ? IS NULL
                    OR project_id = ?
                    OR (
                        memory_type = 'preference'
                        AND ? IS NOT NULL
                        AND user_id = ?
                    )
                  )
              AND (? IS NULL OR task_id = ?)
              AND (? IS NULL OR scope = ?)
              AND (
                    ? = 1
                    OR memory_type != 'preference'
                    OR (
                        content_json NOT LIKE ?
                        AND content_json NOT LIKE ?
                    )
                  )
              AND (
                    ? = 1
                    OR content_json NOT LIKE ?
                  )
            """ + layer_clause + logical_layer_clause,
            (
                request.user_id, request.user_id,
                request.project_id, request.project_id, request.user_id, request.user_id,
                request.task_id, request.task_id,
                request.scope, request.scope,
                1 if request.include_candidates else 0,
                f"%{OBSERVATION_KIND}%",
                f"%{CANDIDATE_KIND}%",
                1 if include_workflow_candidates else 0,
                f"%{STRATEGY_CANDIDATE_KIND}%",
            ) + tuple(layer_params) + tuple(logical_layer_params),
        ).fetchall()

        # #5: compute BM25 stats over ALL active memories (not filtered subset)
        lexical_stats = self._get_lexical_stats()

        scored = [self._score_row(rewritten_query, row, lexical_stats) for row in rows]

        # Normalize BM25 relevance within the scoped candidate set before weighted fusion.
        # BM25 is unbounded while other terms are in [0, 1]. Min-max normalization ensures
        # the configured weight balance reflects actual balance.
        if scored:
            rel_values = [s["relevance_raw"] for s in scored]
            max_rel = max(rel_values)
            min_rel = min(rel_values)
            span = max_rel - min_rel
            for s in scored:
                if span > 0:
                    norm = (s["relevance_raw"] - min_rel) / span
                elif max_rel > 0:
                    norm = 1.0  # all candidates equally relevant
                else:
                    norm = 0.0  # no candidate has any relevance
                s["score"] = round(
                    _WEIGHT_RELEVANCE * norm
                    + _WEIGHT_FRESHNESS * s["freshness"]
                    + _WEIGHT_IMPORTANCE * s["importance"]
                    + _WEIGHT_CONFIDENCE * s["confidence"],
                    4,
                )

        pre_filter = scored
        history_query = _is_history_query(rewritten_query, request.intent)
        scored = [
            item for item in scored
            if _is_visible_to_request(item, request)
            and _is_effective_now(item)
            and _has_recall_evidence(rewritten_query, item, request.intent)
            and not (
                not history_query
                and item["memory_type"] == "preference"
                and _is_stale_preference(item)
            )
            and not (
                not history_query
                and item["memory_type"] == "decision"
                and _is_superseded_decision(item)
            )
        ]

        # filter, sort, MMR: get final results first
        scored = [item for item in scored if item["score"] >= _MIN_SCORE]
        scored.sort(key=lambda item: item["score"], reverse=True)
        if (
            scored
            and request.intent in _SINGLE_INTENT_SCORE_FILTERS
            and len(_tokenize(rewritten_query)) >= 4
        ):
            top_score = float(scored[0]["score"])
            min_relative_score = top_score * _SINGLE_INTENT_RELATIVE_SCORE_CUTOFF
            scored = [
                item for item in scored
                if item is scored[0] or float(item["score"]) >= min_relative_score
            ]
        candidate_pool = scored[: min(len(scored), max(limit * 4, limit))]
        results = _mmr_diversity(candidate_pool, limit)

        # log observations with accurate was_returned based on actual returned set
        returned_order = {item["id"]: idx for idx, item in enumerate(results)}
        threshold_ids = {item["id"] for item in scored}
        pool_ids = {item["id"] for item in candidate_pool}
        self._log_recall_observations(request, pre_filter, returned_order, threshold_ids, pool_ids)
        self.conn.commit()

        return results

    # ------------------------------------------------------------------ #
    # Public API: update
    # ------------------------------------------------------------------ #

    @_with_engine_lock
    def update(
        self,
        memory_id: int,
        candidate: MemoryCandidate,
        event: SourceEvent,
        project_id: str | None = None,
        task_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        self.conn.execute("SAVEPOINT update_tx")
        try:
            old = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if old is None:
                raise ValueError(f"memory {memory_id} not found")

            privacy_warnings: list[dict[str, Any]] = []
            event = self._scan_event(event, privacy_warnings)
            candidate = self._scan_candidate(candidate, privacy_warnings)

            event_id = self._insert_event(event, project_id=project_id, task_id=task_id, user_id=user_id)
            new_uuid = str(uuid.uuid4())
            now = utc_now()

            self.conn.execute(
                "UPDATE memories SET status = 'superseded', superseded_by = ?, updated_at = ? WHERE id = ?",
                (new_uuid, now, memory_id),
            )

            new_id = self._insert_memory(
                MemoryCandidate(
                    memory_type=candidate.memory_type,
                    title=candidate.title,
                    summary=candidate.summary,
                    content=candidate.content,
                    importance=candidate.importance,
                    confidence=candidate.confidence,
                    evidence=candidate.evidence,
                    tags=candidate.tags,
                    replaces_memory_id=memory_id,
                    change_reason=candidate.change_reason,
                ),
                event_id=event_id,
                project_id=project_id,
                task_id=task_id,
                user_id=user_id,
                version=int(old["version"]) + 1,
                forced_uuid=new_uuid,
                memory_layer=old["memory_layer"] if "memory_layer" in old.keys() else "factual",
            )
            self._insert_event_entry_for_memory(
                candidate,
                memory_id=new_id,
                event_id=event_id,
                event=event,
                project_id=project_id,
                task_id=task_id,
                user_id=user_id,
            )
            self._log_audit(
                action=AuditAction.UPDATE,
                target_type="memory",
                target_id=new_id,
                actor=user_id or "",
                detail=f"superseded memory {memory_id}, version {int(old['version']) + 1}, reason: {candidate.change_reason or 'N/A'}",
                sensitive_detections=len(privacy_warnings),
            )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT update_tx")
            self.conn.commit()
            result: dict[str, Any] = {"old_memory_id": memory_id, "new_memory_id": new_id}
            if privacy_warnings:
                result["privacy_warnings"] = privacy_warnings
            return result
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT update_tx")
            self.conn.execute("RELEASE SAVEPOINT update_tx")
            raise

    # ------------------------------------------------------------------ #
    # Public API: archive / invalidate
    # ------------------------------------------------------------------ #

    @_with_engine_lock
    def archive(self, memory_id: int, reason: str | None = None) -> None:
        self.conn.execute("SAVEPOINT archive_tx")
        try:
            self._set_status(memory_id, "archived", reason=reason)
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT archive_tx")
            self.conn.commit()
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT archive_tx")
            self.conn.execute("RELEASE SAVEPOINT archive_tx")
            raise

    @_with_engine_lock
    def invalidate(self, memory_id: int, reason: str | None = None) -> None:
        self.conn.execute("SAVEPOINT invalidate_tx")
        try:
            self._set_status(memory_id, "invalid", reason=reason)
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT invalidate_tx")
            self.conn.commit()
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT invalidate_tx")
            self.conn.execute("RELEASE SAVEPOINT invalidate_tx")
            raise

    # ------------------------------------------------------------------ #
    # Public API: compact (#6 + #8)
    # ------------------------------------------------------------------ #

    @_with_engine_lock
    def compact(self) -> dict[str, Any]:
        self.conn.execute("SAVEPOINT compact_tx")
        try:
            merge_report = self._merge_near_duplicates()
            archive_report = self._archive_stale_low_value()
            expire_report = self._expire_old_working()
            # State changes and audit writes are committed together atomically.
            for d in merge_report["details"]:
                self._log_audit(
                    action=AuditAction.COMPACT_MERGE,
                    target_type="memory",
                    target_id=d["removed_id"],
                    detail=f"near-duplicate merge, kept memory {d['kept_id']}",
                )
            for d in archive_report["details"]:
                self._log_audit(
                    action=AuditAction.COMPACT_ARCHIVE,
                    target_type="memory",
                    target_id=d["memory_id"],
                    detail="stale low-value archive",
                )
            for d in expire_report["details"]:
                self._log_audit(
                    action=AuditAction.COMPACT_ARCHIVE,
                    target_type="memory",
                    target_id=d["memory_id"],
                    detail="expired working memory archive",
                )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT compact_tx")
            self.conn.commit()
            return {
                "merged": merge_report["merged"],
                "archived": archive_report["archived"],
                "expired_working": expire_report["expired"],
                "details": merge_report["details"] + archive_report["details"] + expire_report["details"],
            }
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT compact_tx")
            self.conn.execute("RELEASE SAVEPOINT compact_tx")
            raise

    # ------------------------------------------------------------------ #
    # Public API: source validation placeholder (#7)
    # ------------------------------------------------------------------ #

    @_with_engine_lock
    def validate_sources(self, resolver: Any | None = None) -> list[dict[str, Any]]:
        """Validate stored source fingerprints with an optional read-only resolver.

        The resolver is called as resolver(source_type, source_ref) and should
        return a dict with optional keys: exists, content_hash, source_version.
        This method does not mutate memories; it only reports source state.
        """
        rows = self.conn.execute(
            """
            SELECT id, source_type, source_ref, content_hash, source_version, validated_at
            FROM events
            ORDER BY id
            """
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {
                "event_id": int(row["id"]),
                "source_type": row["source_type"],
                "source_ref": row["source_ref"],
                "stored_content_hash": row["content_hash"],
                "stored_source_version": row["source_version"],
                "validated_at": row["validated_at"],
            }
            if resolver is None:
                item["status"] = "unknown"
                item["reason"] = "no resolver"
                results.append(item)
                continue

            try:
                current = resolver(row["source_type"], row["source_ref"])
            except Exception as exc:
                item["status"] = "unknown"
                item["reason"] = f"resolver error: {type(exc).__name__}"
                results.append(item)
                continue
            if not current:
                item["status"] = "unknown"
                item["reason"] = "resolver returned no data"
                results.append(item)
                continue
            if current.get("exists") is False:
                item["status"] = "missing"
                item["reason"] = "source missing"
                results.append(item)
                continue

            current_hash = current.get("content_hash")
            current_version = current.get("source_version")
            item["current_content_hash"] = current_hash
            item["current_source_version"] = current_version

            hash_changed = current_hash is not None and current_hash != row["content_hash"]
            version_changed = (
                current_version is not None
                and row["source_version"] is not None
                and current_version != row["source_version"]
            )
            if hash_changed or version_changed:
                item["status"] = "changed"
                item["reason"] = "source fingerprint changed"
            elif current_hash is None and current_version is None:
                item["status"] = "unknown"
                item["reason"] = "resolver returned no fingerprint"
            else:
                item["status"] = "ok"
                item["reason"] = "source fingerprint matches"
            results.append(item)
        return results

    @_with_engine_lock
    def get_event_bundle(self, source_event_id: int) -> dict[str, Any]:
        """Return a source event with its memory cards and event-centric entries."""
        event = self.conn.execute("SELECT * FROM events WHERE id = ?", (source_event_id,)).fetchone()
        if event is None:
            raise ValueError(f"source event {source_event_id} not found")

        memory_rows = self.conn.execute(
            "SELECT * FROM memories WHERE source_event_id = ? ORDER BY id",
            (source_event_id,),
        ).fetchall()
        entry_rows = self.conn.execute(
            "SELECT * FROM event_entries WHERE source_event_id = ? ORDER BY id",
            (source_event_id,),
        ).fetchall()

        return {
            "event": self._event_row_to_dict(event),
            "memories": [self._memory_row_to_dict(row) for row in memory_rows],
            "event_entries": [self._event_entry_row_to_dict(row) for row in entry_rows],
        }

    @_with_engine_lock
    def find_related_events(
        self,
        *,
        subject: str | None = None,
        relation: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM event_entries
            WHERE (? IS NULL OR subject = ?)
              AND (? IS NULL OR relation = ?)
              AND (? IS NULL OR project_id = ?)
              AND (? IS NULL OR task_id = ?)
              AND (? IS NULL OR user_id = ?)
            ORDER BY event_time DESC, id DESC
            LIMIT ?
            """,
            (
                subject, subject,
                relation, relation,
                project_id, project_id,
                task_id, task_id,
                user_id, user_id,
                limit,
            ),
        ).fetchall()
        return [self._event_entry_row_to_dict(row) for row in rows]

    @_with_engine_lock
    def synthesize_events(self, seed_event_ids: list[int], question: str) -> dict[str, Any]:
        """Constrained cross-event synthesis over explicit event bundles."""
        seed_ids = [int(event_id) for event_id in seed_event_ids]
        if not seed_ids:
            return {
                "status": "insufficient_evidence",
                "reason": "no seed events supplied",
                "question": question,
                "conclusions": [],
            }

        placeholders = ",".join("?" for _ in seed_ids)
        rows = self.conn.execute(
            f"""
            SELECT ee.*, e.source_ref, e.timestamp, e.content AS event_content
            FROM event_entries ee
            JOIN events e ON e.id = ee.source_event_id
            WHERE ee.source_event_id IN ({placeholders})
            ORDER BY ee.event_time ASC, ee.id ASC
            """,
            tuple(seed_ids),
        ).fetchall()
        entries = [self._synthesis_entry(row) for row in rows]
        if len({entry["source_event_id"] for entry in entries}) < 2:
            return {
                "status": "insufficient_evidence",
                "reason": "cross-event synthesis requires at least two source events",
                "question": question,
                "conclusions": [],
            }

        conclusions = self._synthesize_event_conclusions(entries, question)
        if not conclusions:
            return {
                "status": "insufficient_evidence",
                "reason": "no constrained synthesis rule matched the supplied event bundles",
                "question": question,
                "conclusions": [],
            }
        return {
            "status": "ok",
            "question": question,
            "conclusions": conclusions,
        }

    # ------------------------------------------------------------------ #
    # Public API: promote (working -> factual)
    # ------------------------------------------------------------------ #

    @_with_engine_lock
    def promote(self, memory_id: int, user_id: str | None = None) -> dict[str, Any]:
        self.conn.execute("SAVEPOINT promote_tx")
        try:
            old = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if old is None:
                raise ValueError(f"memory {memory_id} not found")
            if old["memory_layer"] != "working":
                raise ValueError(f"memory {memory_id} is not working memory (layer={old['memory_layer']})")

            new_uuid = str(uuid.uuid4())
            now = utc_now()

            self.conn.execute(
                "UPDATE memories SET status = 'promoted', superseded_by = ?, updated_at = ? WHERE id = ?",
                (new_uuid, now, memory_id),
            )
            self.conn.execute(
                """
                INSERT INTO memories (
                    uuid, memory_type, title, summary, content_json, scope,
                    project_id, task_id, user_id, importance, confidence,
                    status, version, replaces_memory_id, superseded_by,
                    change_reason, source_event_id, evidence_json, tags_json,
                    created_at, updated_at,
                    memory_layer, promotion_candidate, promoted_from_memory_id,
                    valid_from, valid_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, NULL,
                            ?, ?, ?, ?, ?, ?,
                            'factual', 0, ?, ?, ?)
                """,
                (
                    new_uuid, old["memory_type"], old["title"], old["summary"],
                    old["content_json"], old["scope"],
                    old["project_id"], old["task_id"], old["user_id"],
                    old["importance"], old["confidence"],
                    memory_id,
                    f"promotion: working -> factual, promoted at {now}",
                    old["source_event_id"], old["evidence_json"], old["tags_json"],
                    old["created_at"], now,
                    memory_id,
                    old["valid_from"] if "valid_from" in old.keys() else None,
                    old["valid_until"] if "valid_until" in old.keys() else None,
                ),
            )
            new_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            self._log_audit(
                action=AuditAction.PROMOTE,
                target_type="memory",
                target_id=new_id,
                actor=user_id or "",
                detail=f"promoted working memory {memory_id} -> factual {new_id}",
                sensitive_detections=0,
            )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT promote_tx")
            self.conn.commit()
            return {"old_memory_id": memory_id, "new_memory_id": new_id, "new_uuid": new_uuid}
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT promote_tx")
            self.conn.execute("RELEASE SAVEPOINT promote_tx")
            raise

    # ------------------------------------------------------------------ #
    # Public API: flush (precompaction)
    # ------------------------------------------------------------------ #

    @_with_engine_lock
    def flush(
        self,
        project_id: str | None = None,
        task_id: str | None = None,
        user_id: str | None = None,
        reason: str = "precompaction",
    ) -> dict[str, Any]:
        candidates = self.conn.execute(
            """
            SELECT id, title, importance, confidence
            FROM memories
            WHERE status = 'active'
              AND memory_layer = 'working'
              AND (? IS NULL OR project_id = ?)
              AND (? IS NULL OR task_id = ?)
              AND (? IS NULL OR user_id = ?)
            """,
            (project_id, project_id, task_id, task_id, user_id, user_id),
        ).fetchall()

        marked_ids: list[int] = []
        for row in candidates:
            self.conn.execute(
                "UPDATE memories SET promotion_candidate = 1 WHERE id = ?",
                (row["id"],),
            )
            marked_ids.append(row["id"])

        self._log_audit(
            action=AuditAction.FLUSH,
            target_type="memory",
            target_id=0,
            actor=user_id or "",
            detail=f"precompaction flush: marked {len(marked_ids)} candidates, reason={reason}",
            sensitive_detections=0,
        )
        self.conn.commit()
        return {
            "flush_reason": reason,
            "marked_count": len(marked_ids),
            "promotion_candidate_ids": marked_ids,
        }

    # ------------------------------------------------------------------ #
    # Public API: review (P2 — usage-based promotion / demotion)
    # ------------------------------------------------------------------ #

    @_with_engine_lock
    def review(
        self,
        user_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        self.conn.execute("SAVEPOINT review_tx")
        try:
            result = self._review_impl(user_id=user_id, project_id=project_id, task_id=task_id)
            if (
                result["demotions"]
                or result["preference_candidates"]
                or result["preference_reviews"]
                or result["workflow_strategy_candidates"]
            ):
                self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT review_tx")
            self.conn.commit()
            return result
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT review_tx")
            self.conn.execute("RELEASE SAVEPOINT review_tx")
            raise

    def _review_impl(
        self,
        user_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Scan active factual memories and apply L-layer promotion / demotion rules.

        Direction B (decision/task_status):
          L1→L2: same-theme decisions ≥ 3, OR cross-task recalls ≥ 2
          L2→L3: multi-role reference in source event, OR workflow keyword in summary
        Direction C (preference):
          L1→L2: cross-scene consistency ≥ 2 projects, OR persistence ≥ 7 days
          L2→L3: no evidence conflict (pending user feedback signal — Phase 3)
        Demotion: inactive > 30 days, OR low importance + zero usage
        """
        # Only review memories that haven't been reviewed in the last 24 hours
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).isoformat()

        candidate_rows = self.conn.execute(
            """
            SELECT id FROM memories
            WHERE status = 'active'
              AND memory_layer = 'factual'
              AND logical_layer IN ('L1', 'L2')
              AND (? IS NULL OR last_reviewed_at IS NULL OR last_reviewed_at < ?)
              AND (? IS NULL OR user_id = ?)
              AND (? IS NULL OR project_id = ?)
              AND (? IS NULL OR task_id = ?)
            """,
            (
                cutoff, cutoff,
                user_id, user_id,
                project_id, project_id,
                task_id, task_id,
            ),
        ).fetchall()

        promotions: list[dict[str, Any]] = []
        pending_promotions: list[dict[str, Any]] = []
        governance_rejections: list[dict[str, Any]] = []
        demotions: list[dict[str, Any]] = []
        preference_review_ids = mark_stale_stable_preferences_for_review(
            self.conn,
            user_id=user_id,
            project_id=project_id,
        )
        preference_candidate_ids = materialize_preference_candidates(
            self.conn,
            user_id=user_id,
            project_id=project_id,
        )
        preference_candidate_archive_ids = prune_preference_candidate_branches(
            self.conn,
            user_id=user_id,
            project_id=project_id,
        )
        workflow_strategy_candidate_ids = materialize_workflow_strategy_candidates(
            self.conn,
            user_id=user_id,
            project_id=project_id,
        )
        workflow_strategy_candidate_archive_ids = prune_workflow_strategy_candidate_branches(
            self.conn,
            user_id=user_id,
            project_id=project_id,
        )
        workflow_skill_review_ids = mark_stale_workflow_skills_for_review(
            self.conn,
            user_id=user_id,
            project_id=project_id,
        )
        for candidate_id in preference_candidate_ids:
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=candidate_id,
                actor=user_id or "",
                detail="implicit preference candidate created",
            )
        for preference_id in preference_review_ids:
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=preference_id,
                actor=user_id or "",
                detail="stable preference marked for stale review",
            )
        for candidate_id in workflow_strategy_candidate_ids:
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=candidate_id,
                actor=user_id or "",
                detail="workflow strategy candidate created",
            )
        for candidate_id in preference_candidate_archive_ids:
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=candidate_id,
                actor=user_id or "",
                detail="preference candidate archived by branch limit",
            )
        for candidate_id in workflow_strategy_candidate_archive_ids:
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=candidate_id,
                actor=user_id or "",
                detail="workflow strategy candidate archived by branch limit",
            )
        for skill_id in workflow_skill_review_ids:
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=skill_id,
                actor=user_id or "",
                detail="workflow skill marked for stale review",
            )

        for row in candidate_rows:
            mid = int(row["id"])

            # Try L1→L2 promotion
            result = promote_l1_to_l2(self.conn, mid)
            if result:
                governance = self._govern_layer_promotion(mid, "L2", user_id=user_id)
                result["governance_assembly_id"] = governance.get("assembly_id", "")
                if governance["decision"] != "approve":
                    self._rollback_layer_promotion(mid, "L1", governance["reason"])
                    result["governance_rejected"] = True
                    result["governance_reason"] = governance["reason"]
                    governance_rejections.append(result)
                    self._log_audit(
                        action=AuditAction.PROMOTION_REVIEW,
                        target_type="memory",
                        target_id=mid,
                        actor=user_id or "",
                        detail=f"L1->L2 rejected by governance: {governance['reason']}",
                    )
                    continue
                promotions.append(result)
                self._log_audit(
                    action=AuditAction.L_LAYER_PROMOTION,
                    target_type="memory",
                    target_id=mid,
                    actor=user_id or "",
                    detail=(
                        f"L1→L2 promotion: direction={result['direction']}, "
                        f"trigger={result['trigger']}, confidence_passed={result['confidence_passed']}"
                    ),
                )
                continue

            # Try L2→L3 promotion
            result = promote_l2_to_l3(self.conn, mid)
            if result:
                if result.get("pending_feedback"):
                    pending_promotions.append(result)
                    self._log_audit(
                        action=AuditAction.PROMOTION_REVIEW,
                        target_type="memory",
                        target_id=mid,
                        actor=user_id or "",
                        detail=(
                            f"L2→L3 pending feedback: direction={result['direction']}, "
                            f"trigger={result['trigger']}, confidence_passed={result['confidence_passed']}"
                        ),
                    )
                    continue
                governance = self._govern_layer_promotion(mid, "L3", user_id=user_id)
                result["governance_assembly_id"] = governance.get("assembly_id", "")
                if governance["decision"] != "approve":
                    self._rollback_layer_promotion(mid, "L2", governance["reason"])
                    result["governance_rejected"] = True
                    result["governance_reason"] = governance["reason"]
                    governance_rejections.append(result)
                    self._log_audit(
                        action=AuditAction.PROMOTION_REVIEW,
                        target_type="memory",
                        target_id=mid,
                        actor=user_id or "",
                        detail=f"L2->L3 rejected by governance: {governance['reason']}",
                    )
                    continue
                promotions.append(result)
                self._log_audit(
                    action=AuditAction.L_LAYER_PROMOTION,
                    target_type="memory",
                    target_id=mid,
                    actor=user_id or "",
                    detail=(
                        f"L2→L3 promotion: direction={result['direction']}, "
                        f"trigger={result['trigger']}, confidence_passed={result['confidence_passed']}"
                    ),
                )
                continue

            # Try demotion
            result = demote_low_value(self.conn, mid)
            if result:
                demotions.append(result)
                self._log_audit(
                    action=AuditAction.L_LAYER_DEMOTION,
                    target_type="memory",
                    target_id=mid,
                    actor=user_id or "",
                    detail=f"demoted: {result['reason']}",
                )

        # Mark all scanned memories as reviewed
        for row in candidate_rows:
            self.conn.execute(
                "UPDATE memories SET last_reviewed_at = ? WHERE id = ?",
                (utc_now(), int(row["id"])),
            )

        self._log_audit(
            action=AuditAction.PROMOTION_REVIEW,
            target_type="memory",
            target_id=0,
            actor=user_id or "",
            detail=(
                f"review complete: scanned={len(candidate_rows)}, "
                f"promotions={len(promotions)}, pending_promotions={len(pending_promotions)}, "
                f"governance_rejections={len(governance_rejections)}, "
                f"demotions={len(demotions)}, preference_candidates={len(preference_candidate_ids)}, "
                f"preference_reviews={len(preference_review_ids)}, "
                f"workflow_strategy_candidates={len(workflow_strategy_candidate_ids)}, "
                f"preference_candidate_archives={len(preference_candidate_archive_ids)}, "
                f"workflow_strategy_candidate_archives={len(workflow_strategy_candidate_archive_ids)}, "
                f"workflow_skill_reviews={len(workflow_skill_review_ids)}"
            ),
        )

        return {
            "scanned": len(candidate_rows),
            "promotions": promotions,
            "pending_promotions": pending_promotions,
            "governance_rejections": governance_rejections,
            "demotions": demotions,
            "preference_candidates": preference_candidate_ids,
            "preference_reviews": preference_review_ids,
            "workflow_strategy_candidates": workflow_strategy_candidate_ids,
            "preference_candidate_archives": preference_candidate_archive_ids,
            "workflow_strategy_candidate_archives": workflow_strategy_candidate_archive_ids,
            "workflow_skill_reviews": workflow_skill_review_ids,
        }

    def _govern_layer_promotion(self, memory_id: int, to_layer: str, user_id: str | None = None) -> dict[str, Any]:
        decision = review_memory_promotion(
            self.conn,
            memory_id,
            to_layer,
            ballot_provider=self.governance_ballot_provider,
        )
        self._log_audit(
            action=AuditAction.PROMOTION_REVIEW,
            target_type="memory",
            target_id=memory_id,
            actor=user_id or "",
            detail=f"governance review for {to_layer}: {decision['decision']}; {decision['reason']}",
        )
        return decision

    def _rollback_layer_promotion(self, memory_id: int, layer: str, reason: str) -> None:
        self.conn.execute(
            "UPDATE memories SET logical_layer = ?, change_reason = ?, updated_at = ? WHERE id = ?",
            (layer, f"governance rejected promotion: {reason}", utc_now(), memory_id),
        )

    # ------------------------------------------------------------------ #
    # Public API: promote_l2 (manual L1→L2)
    # ------------------------------------------------------------------ #

    @_with_engine_lock
    def confirm_preference_candidate(self, candidate_id: int, user_id: str | None = None) -> dict[str, Any]:
        """Confirm an implicit preference candidate into a stable preference."""
        self.conn.execute("SAVEPOINT confirm_preference_tx")
        try:
            governance = review_preference_candidate(
                self.conn,
                candidate_id,
                ballot_provider=self.governance_ballot_provider,
            )
            if governance["decision"] != "approve":
                self._log_audit(
                    action=AuditAction.PROMOTION_REVIEW,
                    target_type="memory",
                    target_id=candidate_id,
                    actor=user_id or "",
                    detail=f"preference candidate rejected by governance: {governance['reason']}",
                )
                raise GovernanceRejected(governance)
            stable_id = confirm_implicit_preference_candidate(self.conn, candidate_id, user_id=user_id)
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=stable_id,
                actor=user_id or "",
                detail=(
                    f"preference candidate {candidate_id} confirmed into stable preference; "
                    f"governance={governance['decision']}; assembly_id={governance['assembly_id']}"
                ),
            )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT confirm_preference_tx")
            self.conn.commit()
            return {
                "candidate_id": candidate_id,
                "stable_preference_id": stable_id,
                "governance": governance,
                "assembly_id": governance["assembly_id"],
            }
        except GovernanceRejected:
            self.conn.execute("RELEASE SAVEPOINT confirm_preference_tx")
            self.conn.commit()
            raise
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT confirm_preference_tx")
            self.conn.execute("RELEASE SAVEPOINT confirm_preference_tx")
            raise

    @_with_engine_lock
    def reject_preference_candidate(self, candidate_id: int, user_id: str | None = None) -> dict[str, Any]:
        """Archive an implicit preference candidate rejected by the user."""
        self.conn.execute("SAVEPOINT reject_preference_tx")
        try:
            rejected_id = reject_implicit_preference_candidate(self.conn, candidate_id, user_id=user_id)
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=rejected_id,
                actor=user_id or "",
                detail="preference candidate rejected by user",
            )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT reject_preference_tx")
            self.conn.commit()
            return {"candidate_id": rejected_id, "rejected": True}
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT reject_preference_tx")
            self.conn.execute("RELEASE SAVEPOINT reject_preference_tx")
            raise

    @_with_engine_lock
    def reconfirm_stable_preference(self, stable_id: int, user_id: str | None = None) -> dict[str, Any]:
        """Re-confirm a stable preference that was marked for review."""
        self.conn.execute("SAVEPOINT reconfirm_stable_preference_tx")
        try:
            confirmed_id = reconfirm_implicit_stable_preference(self.conn, stable_id, user_id=user_id)
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=confirmed_id,
                actor=user_id or "",
                detail="stable preference reconfirmed by user",
            )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT reconfirm_stable_preference_tx")
            self.conn.commit()
            return {"stable_preference_id": confirmed_id, "reconfirmed": True}
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT reconfirm_stable_preference_tx")
            self.conn.execute("RELEASE SAVEPOINT reconfirm_stable_preference_tx")
            raise

    @_with_engine_lock
    def reject_stable_preference(self, stable_id: int, user_id: str | None = None) -> dict[str, Any]:
        """Archive a stable preference rejected during review."""
        self.conn.execute("SAVEPOINT reject_stable_preference_tx")
        try:
            rejected_id = reject_implicit_stable_preference(self.conn, stable_id, user_id=user_id)
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=rejected_id,
                actor=user_id or "",
                detail="stable preference rejected by user",
            )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT reject_stable_preference_tx")
            self.conn.commit()
            return {"stable_preference_id": rejected_id, "rejected": True}
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT reject_stable_preference_tx")
            self.conn.execute("RELEASE SAVEPOINT reject_stable_preference_tx")
            raise

    @_with_engine_lock
    def confirm_workflow_strategy_candidate(self, candidate_id: int, user_id: str | None = None) -> dict[str, Any]:
        """Confirm a workflow strategy candidate into a stable workflow skill."""
        self.conn.execute("SAVEPOINT confirm_workflow_tx")
        try:
            skill_id = confirm_workflow_strategy(
                self.conn,
                candidate_id,
                user_id=user_id,
                ballot_provider=self.governance_ballot_provider,
            )
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=skill_id,
                actor=user_id or "",
                detail=f"workflow strategy candidate {candidate_id} confirmed into workflow skill",
            )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT confirm_workflow_tx")
            self.conn.commit()
            return {"candidate_id": candidate_id, "workflow_skill_id": skill_id}
        except GovernanceRejected as exc:
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=candidate_id,
                actor=user_id or "",
                detail=f"workflow strategy candidate rejected by governance: {exc.decision['reason']}",
            )
            self.conn.execute("RELEASE SAVEPOINT confirm_workflow_tx")
            self.conn.commit()
            raise
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT confirm_workflow_tx")
            self.conn.execute("RELEASE SAVEPOINT confirm_workflow_tx")
            raise

    @_with_engine_lock
    def reject_workflow_strategy_candidate(self, candidate_id: int, user_id: str | None = None) -> dict[str, Any]:
        """Archive a workflow strategy candidate rejected by the user."""
        self.conn.execute("SAVEPOINT reject_workflow_tx")
        try:
            rejected_id = reject_workflow_strategy(self.conn, candidate_id, user_id=user_id)
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=rejected_id,
                actor=user_id or "",
                detail="workflow strategy candidate rejected by user",
            )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT reject_workflow_tx")
            self.conn.commit()
            return {"candidate_id": rejected_id, "rejected": True}
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT reject_workflow_tx")
            self.conn.execute("RELEASE SAVEPOINT reject_workflow_tx")
            raise

    @_with_engine_lock
    def reconfirm_workflow_skill(self, skill_id: int, user_id: str | None = None) -> dict[str, Any]:
        """Re-confirm a workflow skill that was marked for review."""
        self.conn.execute("SAVEPOINT reconfirm_workflow_skill_tx")
        try:
            confirmed_id = reconfirm_workflow_skill_review(self.conn, skill_id, user_id=user_id)
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=confirmed_id,
                actor=user_id or "",
                detail="workflow skill reconfirmed by user",
            )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT reconfirm_workflow_skill_tx")
            self.conn.commit()
            return {"workflow_skill_id": confirmed_id, "reconfirmed": True}
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT reconfirm_workflow_skill_tx")
            self.conn.execute("RELEASE SAVEPOINT reconfirm_workflow_skill_tx")
            raise

    @_with_engine_lock
    def reject_workflow_skill(self, skill_id: int, user_id: str | None = None) -> dict[str, Any]:
        """Archive a workflow skill rejected during review."""
        self.conn.execute("SAVEPOINT reject_workflow_skill_tx")
        try:
            rejected_id = reject_workflow_skill_review(self.conn, skill_id, user_id=user_id)
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=rejected_id,
                actor=user_id or "",
                detail="workflow skill rejected by user",
            )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT reject_workflow_skill_tx")
            self.conn.commit()
            return {"workflow_skill_id": rejected_id, "rejected": True}
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT reject_workflow_skill_tx")
            self.conn.execute("RELEASE SAVEPOINT reject_workflow_skill_tx")
            raise

    @_with_engine_lock
    def record_workflow_skill_outcome(
        self,
        skill_id: int,
        *,
        outcome: str,
        summary: str,
        evidence: list[dict[str, Any]] | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Record explicit success/failure/override feedback for a workflow skill."""
        self.conn.execute("SAVEPOINT workflow_outcome_tx")
        try:
            outcome_memory_id = record_workflow_outcome(
                self.conn,
                skill_id,
                outcome=outcome,
                summary=summary,
                evidence=evidence or [{"source_ref": f"workflow-skill:{skill_id}"}],
                user_id=user_id,
                project_id=project_id,
                task_id=task_id,
            )
            self._log_audit(
                action=AuditAction.PROMOTION_REVIEW,
                target_type="memory",
                target_id=skill_id,
                actor=user_id or "",
                detail=f"workflow skill outcome recorded: {outcome}, outcome_memory_id={outcome_memory_id}",
            )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT workflow_outcome_tx")
            self.conn.commit()
            return {"workflow_skill_id": skill_id, "outcome_memory_id": outcome_memory_id, "outcome": outcome}
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT workflow_outcome_tx")
            self.conn.execute("RELEASE SAVEPOINT workflow_outcome_tx")
            raise

    @_with_engine_lock
    def evaluate_workflow_self_improvement(self, task_type: str) -> dict[str, Any]:
        """Evaluate long-horizon workflow skill improvement for one task type."""
        return evaluate_workflow_self_improvement(self.conn, task_type)

    @_with_engine_lock
    def promote_l2(self, memory_id: int, user_id: str | None = None) -> dict[str, Any]:
        """
        Manually promote a memory from L1 to L2.
        Checks promotion criteria and returns the promotion result or raises ValueError.
        """
        self.conn.execute("SAVEPOINT promote_l2_tx")
        try:
            result = promote_l1_to_l2(self.conn, memory_id)
            if result:
                governance = self._govern_layer_promotion(memory_id, "L2", user_id=user_id)
                if governance["decision"] != "approve":
                    self._rollback_layer_promotion(memory_id, "L1", governance["reason"])
                    self._log_audit(
                        action=AuditAction.PROMOTION_REVIEW,
                        target_type="memory",
                        target_id=memory_id,
                        actor=user_id or "",
                        detail=f"manual L1->L2 rejected by governance: {governance['reason']}",
                    )
                    raise GovernanceRejected(governance)
                self._log_audit(
                    action=AuditAction.L_LAYER_PROMOTION,
                    target_type="memory",
                    target_id=memory_id,
                    actor=user_id or "",
                    detail=(
                        f"manual L1→L2 promotion: direction={result['direction']}, "
                        f"trigger={result['trigger']}, confidence_passed={result['confidence_passed']}"
                    ),
                )
                self._invalidate_lexical_stats_cache()
                self.conn.execute("RELEASE SAVEPOINT promote_l2_tx")
                self.conn.commit()
                return result

            # Fall back: check current state and explain why it didn't qualify
            row = self.conn.execute(
                "SELECT id, logical_layer, status, memory_type, confidence FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"memory {memory_id} not found")
            if row["logical_layer"] != "L1":
                raise ValueError(
                    f"memory {memory_id} is not at L1 (current layer={row['logical_layer']})"
                )
            if row["status"] != "active":
                raise ValueError(
                    f"memory {memory_id} is not active (status={row['status']})"
                )

            stats = get_recall_stats_for_memory(self.conn, memory_id)
            raise ValueError(
                f"memory {memory_id} does not meet L1→L2 criteria "
                f"(confidence={row['confidence']}, returned_count={stats['returned_count']}, "
                f"unique_tasks={stats['unique_tasks']}, distinct_projects={stats['distinct_projects']})"
            )
        except GovernanceRejected:
            self.conn.execute("RELEASE SAVEPOINT promote_l2_tx")
            self.conn.commit()
            raise
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT promote_l2_tx")
            self.conn.execute("RELEASE SAVEPOINT promote_l2_tx")
            raise

    # ------------------------------------------------------------------ #
    # Public API: demote
    # ------------------------------------------------------------------ #

    @_with_engine_lock
    def demote(self, memory_id: int, reason: str, user_id: str | None = None) -> None:
        """
        Manually demote (archive) a memory with an explicit reason.
        """
        self.conn.execute("SAVEPOINT demote_tx")
        try:
            row = self.conn.execute(
                "SELECT id, status FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"memory {memory_id} not found")
            if row["status"] != "active":
                raise ValueError(
                    f"memory {memory_id} is not active (status={row['status']})"
                )

            self.conn.execute(
                "UPDATE memories SET status = 'archived', change_reason = ?, updated_at = ? WHERE id = ?",
                (f"demote: {reason}", utc_now(), memory_id),
            )
            self._log_audit(
                action=AuditAction.L_LAYER_DEMOTION,
                target_type="memory",
                target_id=memory_id,
                actor=user_id or "",
                detail=f"manual demote: {reason}",
            )
            self._invalidate_lexical_stats_cache()
            self.conn.execute("RELEASE SAVEPOINT demote_tx")
            self.conn.commit()
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT demote_tx")
            self.conn.execute("RELEASE SAVEPOINT demote_tx")
            raise

    # ------------------------------------------------------------------ #
    # Internal: audit logging
    # ------------------------------------------------------------------ #

    def _get_lexical_stats(self) -> dict[str, Any]:
        if self._lexical_stats_cache is None:
            all_active = self.conn.execute(
                "SELECT title, summary, content_json, token_list FROM memories WHERE status = 'active'"
            ).fetchall()
            self._lexical_stats_cache = _compute_lexical_stats(all_active)
        return self._lexical_stats_cache

    def _invalidate_lexical_stats_cache(self) -> None:
        self._lexical_stats_cache = None

    def _log_audit(
        self,
        action: str,
        target_type: str,
        target_id: int,
        actor: str = "",
        detail: str = "",
        sensitive_detections: int = 0,
    ) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (action, target_type, target_id, actor, detail, sensitive_detections, audited_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (action, target_type, target_id, actor, detail, sensitive_detections, utc_now()),
        )

    # ------------------------------------------------------------------ #
    # Internal: privacy scan helper (shared by write + update)
    # ------------------------------------------------------------------ #

    def _scan_event(
        self, event: SourceEvent, warnings: list[dict[str, Any]],
    ) -> SourceEvent:
        content_scan = scan_and_mask(event.content, "")
        masked_payload, payload_detections = self._mask_structure(event.payload)
        if content_scan.has_sensitive or payload_detections:
            if content_scan.has_sensitive:
                warnings.append({"field": "event.content", "detections": content_scan.detections})
            if payload_detections:
                warnings.append({"field": "event.payload", "detections": payload_detections})
            return SourceEvent(
                source_type=event.source_type, source_ref=event.source_ref,
                actors=event.actors, timestamp=event.timestamp,
                content=content_scan.masked_content if content_scan.has_sensitive else event.content,
                scope=event.scope,
                payload=masked_payload,
            )
        return event

    def _scan_candidate(
        self, candidate: MemoryCandidate, warnings: list[dict[str, Any]],
    ) -> MemoryCandidate:
        title_scan = scan_and_mask(candidate.title, "")
        summary_scan = scan_and_mask("", candidate.summary)
        masked_content, content_detections = self._mask_structure(candidate.content)
        masked_evidence, evidence_detections = self._mask_structure(candidate.evidence)
        has_sensitive = (
            title_scan.has_sensitive
            or summary_scan.has_sensitive
            or bool(content_detections)
            or bool(evidence_detections)
        )
        if has_sensitive:
            if title_scan.has_sensitive:
                warnings.append({"field": f"candidate[{candidate.title}].title", "detections": title_scan.detections})
            if summary_scan.has_sensitive:
                warnings.append({"field": f"candidate[{candidate.title}].summary", "detections": summary_scan.detections})
            if content_detections:
                warnings.append({"field": f"candidate[{candidate.title}].content", "detections": content_detections})
            if evidence_detections:
                warnings.append({"field": f"candidate[{candidate.title}].evidence", "detections": evidence_detections})
            return MemoryCandidate(
                memory_type=candidate.memory_type,
                title=title_scan.masked_content if title_scan.has_sensitive else candidate.title,
                summary=summary_scan.masked_summary if summary_scan.has_sensitive else candidate.summary,
                content=masked_content,
                importance=candidate.importance,
                confidence=candidate.confidence,
                evidence=masked_evidence,
                tags=candidate.tags, replaces_memory_id=candidate.replaces_memory_id,
                change_reason=candidate.change_reason,
            )
        return candidate

    def _mask_structure(self, value: Any) -> tuple[Any, list[dict[str, Any]]]:
        detections: list[dict[str, Any]] = []
        if isinstance(value, str):
            scan = scan_and_mask(value, "")
            if scan.has_sensitive:
                detections.extend(scan.detections)
                return scan.masked_content, detections
            return value, detections
        if isinstance(value, list):
            masked_list = []
            for item in value:
                masked_item, item_detections = self._mask_structure(item)
                masked_list.append(masked_item)
                detections.extend(item_detections)
            return masked_list, detections
        if isinstance(value, dict):
            masked_dict: dict[str, Any] = {}
            for key, item in value.items():
                if isinstance(item, str) and self._is_sensitive_field_name(str(key)):
                    # scan the string value for PII before replacing
                    scan = scan_and_mask(item, "")
                    if scan.has_sensitive:
                        masked_dict[key] = scan.masked_content
                        detections.append({"category": "sensitive_field_value", "field": key, "detections": scan.detections})
                    else:
                        # no PII found in the value, still redact the field name signal
                        masked_dict[key] = "[secret_field:REDACTED]"
                        detections.append({"category": "secret_field", "field": key, "snippet": f"{key}***"})
                else:
                    masked_item, item_detections = self._mask_structure(item)
                    masked_dict[key] = masked_item
                    detections.extend(item_detections)
            return masked_dict, detections
        return value, detections

    def _is_sensitive_field_name(self, key: str) -> bool:
        normalized = key.strip().lower()
        return normalized in _SENSITIVE_FIELD_NAMES

    # ------------------------------------------------------------------ #
    # Internal: write gate (#4)
    # ------------------------------------------------------------------ #

    def _should_store(
        self,
        candidate: MemoryCandidate,
        project_id: str | None,
        task_id: str | None,
        user_id: str | None,
    ) -> dict[str, Any]:
        summary = candidate.summary.strip()
        if len(summary) < _GATE_MIN_SUMMARY_LEN:
            return {"action": "reject", "reason": f"summary too short ({len(summary)} chars)"}

        if (
            candidate.memory_type == "procedural"
            and candidate.content.get("kind") in {SUCCESS_CASE_KIND, FAILURE_CASE_KIND}
        ):
            return {"action": "write", "reason": "workflow case evidence"}

        # duplicate check
        rows = self.conn.execute(
            """
            SELECT id, title, summary FROM memories
            WHERE status = 'active' AND memory_type = ? AND scope = ?
              AND (? IS NULL OR project_id = ?) AND (? IS NULL OR task_id = ?) AND (? IS NULL OR user_id = ?)
            """,
            (
                candidate.memory_type, candidate.content.get("scope", "task"),
                project_id, project_id, task_id, task_id, user_id, user_id,
            ),
        ).fetchall()

        candidate_text = candidate.title + " " + candidate.summary
        for row in rows:
            existing_text = row["title"] + " " + row["summary"]
            if _overlap_score(candidate_text, existing_text) > _GATE_DUPLICATE_OVERLAP:
                return {"action": "skip", "reason": f"near-duplicate of memory {row['id']} (overlap > {_GATE_DUPLICATE_OVERLAP})"}

        # low confidence: allow but penalize importance
        if candidate.confidence < _GATE_LOW_CONFIDENCE:
            return {"action": "write", "reason": "low confidence, importance penalized", "importance_modifier": 0.5}

        return {"action": "write", "reason": "ok"}

    # ------------------------------------------------------------------ #
    # Internal: recall observation logging (#2)
    # ------------------------------------------------------------------ #

    def _log_recall_observations(
        self,
        request: RecallRequest,
        scored: list[dict[str, Any]],
        returned_order: dict[int, int],
        threshold_ids: set[int] | None = None,
        pool_ids: set[int] | None = None,
    ) -> None:
        now = utc_now()
        if not scored:
            # zero-result query
            self.conn.execute(
                "INSERT INTO recall_log (memory_id, query, raw_score, confidence, rank_index, was_returned, project_id, task_id, user_id, recalled_at) VALUES (NULL, ?, 0, 0, NULL, 2, ?, ?, ?, ?)",
                (request.query, request.project_id, request.task_id, request.user_id, now),
            )
            return

        # default ordering keeps non-returned candidates analyzable by raw score;
        # returned items get their actual MMR order.
        ordered = sorted(scored, key=lambda item: item["score"], reverse=True)
        threshold_ids = threshold_ids or set()
        pool_ids = pool_ids or set()
        for i, item in enumerate(ordered):
            item_id = item["id"]
            if item_id in returned_order:
                was_returned = 1
            elif item_id not in threshold_ids:
                was_returned = 4
            elif item_id in pool_ids:
                was_returned = 3
            else:
                was_returned = 0
            rank_index = returned_order[item_id] if was_returned == 1 else i
            self.conn.execute(
                "INSERT INTO recall_log (memory_id, query, raw_score, confidence, rank_index, was_returned, project_id, task_id, user_id, recalled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, request.query, item["score"], item["confidence"], rank_index, was_returned,
                 request.project_id, request.task_id, request.user_id, now),
            )

    def _get_recall_stats(self, memory_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT COUNT(*) as recall_count,
                   SUM(CASE WHEN was_returned = 1 THEN 1 ELSE 0 END) as returned_count,
                   AVG(raw_score) as avg_score,
                   MAX(recalled_at) as last_recalled_at,
                   COUNT(DISTINCT query) as unique_queries
            FROM recall_log WHERE memory_id = ? AND was_returned IN (0, 1)
            """,
            (memory_id,),
        ).fetchone()
        return {
            "recall_count": int(row["recall_count"]) if row["recall_count"] else 0,
            "returned_count": int(row["returned_count"]) if row["returned_count"] else 0,
            "avg_score": round(float(row["avg_score"]), 4) if row["avg_score"] else 0.0,
            "last_recalled_at": row["last_recalled_at"],
            "unique_queries": int(row["unique_queries"]) if row["unique_queries"] else 0,
        }

    def _get_zero_result_queries(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT query, COUNT(*) as miss_count, MAX(recalled_at) as last_miss
            FROM recall_log WHERE was_returned = 2
            GROUP BY query ORDER BY miss_count DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [{"query": r["query"], "miss_count": int(r["miss_count"]), "last_miss": r["last_miss"]} for r in rows]

    # ------------------------------------------------------------------ #
    # Internal: compact steps (#6 + #8)
    # ------------------------------------------------------------------ #

    def _merge_near_duplicates(self) -> dict[str, Any]:
        return merge_near_duplicates(self.conn)

    def _archive_stale_low_value(self) -> dict[str, Any]:
        return archive_stale_low_value(self.conn)

    def _expire_old_working(self) -> dict[str, Any]:
        return expire_old_working(self.conn)

    # ------------------------------------------------------------------ #
    # Internal: status, conflict, scoring
    # ------------------------------------------------------------------ #

    def _set_status(self, memory_id: int, status: str, reason: str | None = None) -> None:
        now = utc_now()
        self.conn.execute(
            "UPDATE memories SET status = ?, updated_at = ?, change_reason = COALESCE(?, change_reason) WHERE id = ?",
            (status, now, reason, memory_id),
        )
        action = AuditAction.ARCHIVE if status == "archived" else AuditAction.INVALIDATE
        self._log_audit(
            action=action,
            target_type="memory",
            target_id=memory_id,
            detail=f"status set to {status}" + (f", reason: {reason}" if reason else ""),
            sensitive_detections=0,
        )

    def _detect_conflict(
        self,
        candidate: MemoryCandidate,
        project_id: str | None,
        task_id: str | None,
        user_id: str | None,
    ) -> dict[str, Any] | None:
        rows = self.conn.execute(
            """
            SELECT id, title, summary, confidence, project_id, task_id
            FROM memories
            WHERE status = 'active'
              AND memory_type = ? AND scope = ?
              AND (? IS NULL OR project_id = ?) AND (? IS NULL OR task_id = ?) AND (? IS NULL OR user_id = ?)
            """,
            (
                candidate.memory_type, candidate.content.get("scope", "task"),
                project_id, project_id, task_id, task_id, user_id, user_id,
            ),
        ).fetchall()

        candidate_text = candidate.title + " " + candidate.summary
        for row in rows:
            existing = dict(row)
            conflict = classify_conflict(candidate, existing)
            if conflict is not None:
                return conflict
        return None

    def _apply_conflict_resolution(self, conflict: dict[str, Any], new_memory_id: int) -> None:
        action = conflict.get("resolution_action")
        existing_id = conflict.get("existing_memory_id")
        if not existing_id:
            return

        if action == "supersede":
            row = self.conn.execute("SELECT uuid FROM memories WHERE id = ?", (new_memory_id,)).fetchone()
            if row is None:
                return
            self.conn.execute(
                "UPDATE memories SET status = 'superseded', superseded_by = ?, updated_at = ? WHERE id = ?",
                (row["uuid"], utc_now(), int(existing_id)),
            )
            conflict["resolution_applied"] = "superseded_existing"
        elif action == "flag_review":
            self.conn.execute(
                "UPDATE memories SET confidence = MIN(confidence, 0.6), change_reason = COALESCE(change_reason, ?) WHERE id = ?",
                ("conflict: evidence conflict, review required", new_memory_id),
            )
            self._log_audit(
                action=AuditAction.UPDATE,
                target_type="memory",
                target_id=new_memory_id,
                detail="evidence_conflict detected; lowered confidence and flagged review",
                sensitive_detections=0,
            )
            conflict["resolution_applied"] = "flagged_new_for_review"
        else:
            conflict["resolution_applied"] = "kept_both"

    def _validate_memory_layer(self, memory_layer: str) -> None:
        try:
            MemoryLayer(memory_layer)
        except ValueError as exc:
            raise ValueError(f"invalid memory_layer: {memory_layer}") from exc

    def _validate_logical_layer(self, logical_layer: str) -> None:
        if logical_layer not in {"L1", "L2", "L3"}:
            raise ValueError(f"invalid logical_layer: {logical_layer}")

    def _insert_event(
        self, event: SourceEvent, project_id: str | None, task_id: str | None, user_id: str | None,
    ) -> int:
        return insert_event(self.conn, event, project_id, task_id, user_id)

    def _insert_memory(
        self, candidate: MemoryCandidate, event_id: int, project_id: str | None,
        task_id: str | None, user_id: str | None, version: int = 1, forced_uuid: str | None = None,
        memory_layer: str = "factual",
    ) -> int:
        return insert_memory(
            self.conn,
            candidate,
            event_id,
            project_id,
            task_id,
            user_id,
            version=version,
            forced_uuid=forced_uuid,
            memory_layer=memory_layer,
        )

    def _insert_event_entry_for_memory(
        self,
        candidate: MemoryCandidate,
        *,
        memory_id: int,
        event_id: int,
        event: SourceEvent,
        project_id: str | None,
        task_id: str | None,
        user_id: str | None,
    ) -> int | None:
        entry = self._build_event_entry(
            candidate,
            memory_id=memory_id,
            event_id=event_id,
            event=event,
            project_id=project_id,
            task_id=task_id,
            user_id=user_id,
        )
        if entry is None:
            return None
        return insert_event_entry(self.conn, entry)

    def _build_event_entry(
        self,
        candidate: MemoryCandidate,
        *,
        memory_id: int,
        event_id: int,
        event: SourceEvent,
        project_id: str | None,
        task_id: str | None,
        user_id: str | None,
    ) -> EventEntry | None:
        if candidate.memory_type not in {"decision", "task_status", "preference", "procedural"}:
            return None

        subject = task_id or project_id or user_id or candidate.content.get("scope", "unknown")
        obj = candidate.content.get("pattern_key") or candidate.content.get("preference_kind") or candidate.title
        relation = "recorded_memory"
        if candidate.memory_type == "decision":
            relation = "recorded_decision"
            obj = candidate.title
        elif candidate.memory_type == "task_status":
            relation = "changed_task_status"
            obj = candidate.title
        elif candidate.memory_type == "preference":
            relation = (
                "rejected_preference_for"
                if candidate.content.get("polarity") == "negative"
                else "showed_preference_for"
            )
        elif candidate.memory_type == "procedural":
            kind = candidate.content.get("kind")
            if kind == TRACE_KIND:
                relation = "recorded_workflow_trace"
                obj = candidate.content.get("task_type", candidate.title)
            elif kind == SUCCESS_CASE_KIND:
                relation = "recorded_workflow_success"
                obj = candidate.content.get("task_type", candidate.title)
            elif kind == FAILURE_CASE_KIND:
                relation = "recorded_workflow_failure"
                obj = candidate.content.get("task_type", candidate.title)
            elif kind == STRATEGY_CANDIDATE_KIND:
                relation = "synthesized_workflow_strategy"
                obj = candidate.content.get("task_type", candidate.title)
            elif kind == WORKFLOW_SKILL_KIND:
                relation = "recorded_workflow_skill"
                obj = candidate.content.get("task_type", candidate.title)
            elif kind == WORKFLOW_SKILL_OUTCOME_KIND:
                outcome = candidate.content.get("outcome", "")
                if outcome == "success":
                    relation = "workflow_skill_succeeded"
                elif outcome == "failure":
                    relation = "workflow_skill_failed"
                else:
                    relation = "workflow_skill_overridden"
                obj = candidate.content.get("task_type", candidate.title)
            else:
                return None

        return EventEntry(
            source_event_id=event_id,
            event_time=event.timestamp,
            entry_type="workflow" if candidate.memory_type == "procedural" else "relational",
            subject=str(subject),
            relation=relation,
            object=str(obj),
            qualifiers={
                "memory_id": memory_id,
                "memory_type": candidate.memory_type,
                "title": candidate.title,
                "summary": candidate.summary,
                "scope": candidate.content.get("scope", event.scope),
                "content_kind": candidate.content.get("kind", ""),
                "pattern_key": candidate.content.get("pattern_key", ""),
                "polarity": candidate.content.get("polarity", ""),
                "source_ref": event.source_ref,
            },
            project_id=project_id,
            task_id=task_id,
            user_id=user_id,
            confidence=candidate.confidence,
        )

    def _event_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "source_type": row["source_type"],
            "source_ref": row["source_ref"],
            "actors": json.loads(row["actors_json"]),
            "timestamp": row["timestamp"],
            "content": row["content"],
            "scope": row["scope"],
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "user_id": row["user_id"],
            "payload": json.loads(row["payload_json"]),
            "content_hash": row["content_hash"],
            "source_version": row["source_version"],
            "validated_at": row["validated_at"],
        }

    def _memory_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "uuid": row["uuid"],
            "memory_type": row["memory_type"],
            "title": row["title"],
            "summary": row["summary"],
            "content": json.loads(row["content_json"]),
            "scope": row["scope"],
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "user_id": row["user_id"],
            "confidence": float(row["confidence"]),
            "importance": float(row["importance"]),
            "status": row["status"],
            "source_event_id": int(row["source_event_id"]),
            "evidence": json.loads(row["evidence_json"]),
            "tags": json.loads(row["tags_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "valid_from": row["valid_from"] if "valid_from" in row.keys() else None,
            "valid_until": row["valid_until"] if "valid_until" in row.keys() else None,
        }

    def _synthesis_entry(self, row: sqlite3.Row) -> dict[str, Any]:
        qualifiers = json.loads(row["qualifiers_json"]) if row["qualifiers_json"] else {}
        return {
            "id": int(row["id"]),
            "source_event_id": int(row["source_event_id"]),
            "source_ref": row["source_ref"],
            "event_time": row["event_time"],
            "entry_type": row["entry_type"],
            "subject": row["subject"],
            "relation": row["relation"],
            "object": row["object"],
            "qualifiers": qualifiers,
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "user_id": row["user_id"],
            "confidence": row["confidence"],
        }

    def _synthesize_event_conclusions(self, entries: list[dict[str, Any]], question: str) -> list[dict[str, Any]]:
        lowered = question.lower()
        conclusions: list[dict[str, Any]] = []
        if any(token in lowered for token in ("change", "changed", "override", "switch", "换", "改", "变更")):
            conclusions.extend(self._synthesize_decision_change(entries))
        if any(token in lowered for token in ("fail", "failure", "失败", "反复失败", "workflow")):
            conclusions.extend(self._synthesize_repeated_workflow_failures(entries))
        if not conclusions:
            conclusions.extend(self._synthesize_timeline(entries))
        return conclusions

    def _synthesize_decision_change(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        decisions = [entry for entry in entries if entry["relation"] == "recorded_decision"]
        if len(decisions) < 2:
            return []
        return [
            self._synthesis_conclusion(
                kind="decision_change_chain",
                statement=f"Decision changed over time: {decisions[0]['object']} -> {decisions[-1]['object']}",
                entries=decisions,
                confidence=min(float(item["confidence"]) for item in decisions),
            )
        ]

    def _synthesize_repeated_workflow_failures(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        failure_entries = [
            entry
            for entry in entries
            if entry["relation"] in {"workflow_skill_failed", "recorded_workflow_failure"}
        ]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in failure_entries:
            grouped.setdefault(entry["object"], []).append(entry)
        conclusions: list[dict[str, Any]] = []
        for task_type, items in grouped.items():
            if len(items) < 2:
                continue
            conclusions.append(
                self._synthesis_conclusion(
                    kind="repeated_workflow_failure",
                    statement=f"Workflow '{task_type}' failed repeatedly across {len(items)} events.",
                    entries=items,
                    confidence=min(float(item["confidence"]) for item in items),
                )
            )
        return conclusions

    def _synthesize_timeline(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(entries) < 2:
            return []
        return [
            self._synthesis_conclusion(
                kind="event_timeline_chain",
                statement=f"Built a constrained timeline from {len(entries)} event entries.",
                entries=entries,
                confidence=min(float(item["confidence"]) for item in entries),
            )
        ]

    def _synthesis_conclusion(
        self,
        *,
        kind: str,
        statement: str,
        entries: list[dict[str, Any]],
        confidence: float,
    ) -> dict[str, Any]:
        source_event_ids = [entry["source_event_id"] for entry in entries]
        return {
            "kind": kind,
            "statement": statement,
            "source_event_ids": source_event_ids,
            "source_refs": [entry["source_ref"] for entry in entries],
            "relations": [entry["relation"] for entry in entries],
            "event_times": [entry["event_time"] for entry in entries],
            "confidence": round(confidence, 4),
            "candidate": {
                "memory_type": "semantic",
                "content": {
                    "kind": "cross_event_synthesis_candidate",
                    "synthesis_kind": kind,
                    "statement": statement,
                    "source_event_ids": [str(event_id) for event_id in source_event_ids],
                },
            },
        }

    def _event_entry_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "source_event_id": int(row["source_event_id"]),
            "event_time": row["event_time"],
            "entry_type": row["entry_type"],
            "subject": row["subject"],
            "relation": row["relation"],
            "object": row["object"],
            "qualifiers": json.loads(row["qualifiers_json"]),
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "user_id": row["user_id"],
            "confidence": float(row["confidence"]),
            "created_at": row["created_at"],
        }

    def _score_row(self, query: str, row: sqlite3.Row, lexical_stats: dict[str, Any]) -> dict[str, Any]:
        relevance = _lexical_score(query, row, lexical_stats)
        freshness = _freshness_score(row["updated_at"], row["memory_type"])
        importance = float(row["importance"])
        confidence = float(row["confidence"])
        raw_score = (
            _WEIGHT_RELEVANCE * relevance
            + _WEIGHT_FRESHNESS * freshness
            + _WEIGHT_IMPORTANCE * importance
            + _WEIGHT_CONFIDENCE * confidence
        )
        # P1: confidence tier annotation
        tier = 1 if confidence >= 0.7 else (2 if confidence >= 0.4 else 3)
        tier_labels = {1: "direct_injection", 2: "evidence_snippet", 3: "no_confidence_signal"}
        memory_layer = row["memory_layer"] if "memory_layer" in row.keys() else "factual"
        return {
            "id": int(row["id"]),
            "uuid": row["uuid"],
            "memory_type": row["memory_type"],
            "title": row["title"],
            "summary": row["summary"],
            "content": json.loads(row["content_json"]),
            "evidence": json.loads(row["evidence_json"]),
            "tags": json.loads(row["tags_json"]),
            "version": int(row["version"]),
            "status": row["status"],
            "confidence": round(float(row["confidence"]), 4),
            "importance": round(float(row["importance"]), 4),
            "freshness": round(freshness, 4),
            "relevance_raw": round(relevance, 4),
            "scope": row["scope"],
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "user_id": row["user_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "valid_from": row["valid_from"] if "valid_from" in row.keys() else None,
            "valid_until": row["valid_until"] if "valid_until" in row.keys() else None,
            "replaces_memory_id": row["replaces_memory_id"],
            "memory_layer": memory_layer,
            "logical_layer": row["logical_layer"] if "logical_layer" in row.keys() else "L1",
            "confidence_tier": tier,
            "confidence_tier_label": tier_labels[tier],
            "score": round(raw_score, 4),
        }

    def _rewrite_query(self, query: str, context: RecallContext) -> str:
        # Phase 1: identity pass-through. Phase 2+: LLM-based expansion.
        return query

    def _get_recent_queries(
        self,
        user_id: str | None,
        project_id: str | None,
        task_id: str | None,
        limit: int = 5,
    ) -> list[str]:
        rows = self.conn.execute(
            """SELECT DISTINCT query FROM recall_log
               WHERE (? IS NULL OR user_id = ?)
                 AND (? IS NULL OR project_id = ?)
                 AND (? IS NULL OR task_id = ?)
               ORDER BY recalled_at DESC LIMIT ?""",
            (user_id, user_id, project_id, project_id, task_id, task_id, limit),
        ).fetchall()
        return [r["query"] for r in rows]
