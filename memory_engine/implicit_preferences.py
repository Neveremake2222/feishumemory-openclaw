from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import EventEntry, MemoryCandidate, SourceEvent, utc_now
from .storage import insert_event, insert_event_entry, insert_memory


OBSERVATION_KIND = "implicit_preference_observation"
CANDIDATE_KIND = "preference_candidate"
STABLE_PREFERENCE_KIND = "stable_preference"
_STALE_STABLE_PREFERENCE_DAYS = 90
_MAX_ACTIVE_CANDIDATES_PER_PATTERN = 3


@dataclass(frozen=True)
class ImplicitPreferenceSignal:
    preference_kind: str
    pattern_key: str
    signal: str
    risk_level: str
    title: str
    summary: str
    polarity: str = "positive"


@dataclass(frozen=True)
class PreferenceCandidate:
    preference_kind: str
    pattern_key: str
    positive_evidence_count: int
    negative_evidence_count: int
    distinct_project_count: int
    first_observed_at: str
    last_observed_at: str
    risk_level: str
    needs_confirmation: bool
    title: str
    summary: str


_NEGATIVE_SIGNAL_RULES: list[tuple[re.Pattern[str], ImplicitPreferenceSignal]] = [
    (
        re.compile(r"don't use (markdown|tables?|bullets?)|no (markdown|tables?|bullets?)|不要.*(markdown|表格|列表|要点)|别.*(markdown|表格|列表|要点)", re.I),
        ImplicitPreferenceSignal(
            preference_kind="output_format",
            pattern_key="pref.output.structured_format",
            signal="structured_output_rejected",
            risk_level="low",
            title="Possible rejection of structured output preference",
            summary="User rejects structured output such as Markdown, tables, lists, or bullet points.",
            polarity="negative",
        ),
    ),
    (
        re.compile(r"直接.*(改|写|做)|别.*(计划|规划|分析)|不要.*(计划|规划|分析)|skip.*(plan|analysis)|no.*(plan|analysis)", re.I),
        ImplicitPreferenceSignal(
            preference_kind="workflow_style",
            pattern_key="pref.workflow.plan_or_analyze_first",
            signal="plan_or_analyze_before_action_rejected",
            risk_level="medium",
            title="Possible rejection of planning before action",
            summary="User asks not to plan or analyze before direct implementation.",
            polarity="negative",
        ),
    ),
    (
        re.compile(r"别.*(测试|跑测试)|不要.*(测试|跑测试)|skip.*tests?|no.*tests?", re.I),
        ImplicitPreferenceSignal(
            preference_kind="workflow_style",
            pattern_key="pref.workflow.test_before_claim",
            signal="test_before_claim_rejected",
            risk_level="medium",
            title="Possible rejection of testing before completion",
            summary="User asks not to run tests before treating work as complete.",
            polarity="negative",
        ),
    ),
    (
        re.compile(r"详细|展开说|多说|don't be brief|not concise|more detail", re.I),
        ImplicitPreferenceSignal(
            preference_kind="communication",
            pattern_key="pref.communication.concise",
            signal="concise_communication_rejected",
            risk_level="low",
            title="Possible rejection of concise communication preference",
            summary="User asks for more detailed communication instead of concise replies.",
            polarity="negative",
        ),
    ),
]


_SIGNAL_RULES: list[tuple[re.Pattern[str], ImplicitPreferenceSignal]] = [
    (
        re.compile(r"markdown|md|表格|列表|要点|bullet", re.I),
        ImplicitPreferenceSignal(
            preference_kind="output_format",
            pattern_key="pref.output.structured_format",
            signal="structured_output_requested",
            risk_level="low",
            title="Possible preference for structured output",
            summary="User repeatedly asks for structured output such as Markdown, tables, lists, or bullet points.",
        ),
    ),
    (
        re.compile(r"先.*(计划|规划|步骤|分析)|别直接|不要直接|先别.*(写|改|动)", re.I),
        ImplicitPreferenceSignal(
            preference_kind="workflow_style",
            pattern_key="pref.workflow.plan_or_analyze_first",
            signal="plan_or_analyze_before_action",
            risk_level="medium",
            title="Possible preference for planning before action",
            summary="User asks to plan or analyze before direct implementation.",
        ),
    ),
    (
        re.compile(r"跑测试|先.*测试|test.*first|run.*tests", re.I),
        ImplicitPreferenceSignal(
            preference_kind="workflow_style",
            pattern_key="pref.workflow.test_before_claim",
            signal="test_before_claim",
            risk_level="medium",
            title="Possible preference for testing before claiming completion",
            summary="User asks to run tests or verify results before treating work as complete.",
        ),
    ),
    (
        re.compile(r"简短|简洁|直接说|少废话|brief|concise", re.I),
        ImplicitPreferenceSignal(
            preference_kind="communication",
            pattern_key="pref.communication.concise",
            signal="concise_communication_requested",
            risk_level="low",
            title="Possible preference for concise communication",
            summary="User asks for concise or direct communication.",
        ),
    ),
]


def detect_implicit_preference_signals(text: str) -> list[ImplicitPreferenceSignal]:
    """Detect weak implicit preference signals from behavior-oriented text."""
    if not text:
        return []

    signals: list[ImplicitPreferenceSignal] = []
    seen: set[str] = set()
    for pattern, signal in _NEGATIVE_SIGNAL_RULES + _SIGNAL_RULES:
        if pattern.search(text) and signal.pattern_key not in seen:
            seen.add(signal.pattern_key)
            signals.append(signal)
    return signals


def build_observation_candidate(
    *,
    signal: ImplicitPreferenceSignal,
    source_text: str,
    content_meta: dict[str, str],
    evidence: list[dict],
    observed_at: str,
) -> MemoryCandidate:
    """Build a low-confidence preference observation that is not eligible for C2 reminders."""
    content = dict(content_meta)
    content.update(
        {
            "kind": OBSERVATION_KIND,
            "preference_kind": signal.preference_kind,
            "pattern_key": signal.pattern_key,
            "signal": signal.signal,
            "polarity": signal.polarity,
            "risk_level": signal.risk_level,
            "needs_confirmation": "true",
            "confirmed": "false",
            "observed_at": observed_at,
            "source_text": source_text[:200],
        }
    )
    return MemoryCandidate(
        memory_type="preference",
        title=signal.title,
        summary=f"[implicit {signal.polarity} observation] {signal.summary} Evidence: {source_text[:120]}",
        content=content,
        importance=0.3,
        confidence=0.45 if signal.risk_level == "low" else 0.4,
        evidence=evidence,
        tags=["implicit_preference", signal.preference_kind, signal.pattern_key],
        change_reason=f"implicit_preference_observation: {signal.signal}",
    )


def derive_preference_candidates(
    conn,
    user_id: str | None = None,
    project_id: str | None = None,
    min_positive: int = 3,
) -> list[dict]:
    """Group weak observations into confirmable preference candidates."""
    rows = conn.execute(
        """
        SELECT id, content_json, evidence_json, project_id, created_at
        FROM memories
        WHERE memory_type = 'preference'
          AND status NOT IN ('invalid', 'archived')
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        """,
        (f"%{OBSERVATION_KIND}%", user_id, user_id, project_id, project_id),
    ).fetchall()

    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        content = json.loads(row["content_json"])
        if content.get("kind") != OBSERVATION_KIND:
            continue
        key = (content.get("preference_kind", ""), content.get("pattern_key", ""))
        grouped.setdefault(key, []).append(
            {
                "id": row["id"],
                "project_id": row["project_id"],
                "created_at": row["created_at"],
                "content": content,
                "evidence": json.loads(row["evidence_json"]),
            }
        )

    candidates: list[dict] = []
    for (preference_kind, pattern_key), items in grouped.items():
        positive = [item for item in items if item["content"].get("polarity") == "positive"]
        negative = [item for item in items if item["content"].get("polarity") == "negative"]
        if len(positive) < min_positive:
            continue
        if negative and len(negative) >= len(positive):
            continue

        distinct_projects = {item["project_id"] for item in items if item["project_id"]}
        first_observed_at = min(item["content"].get("observed_at", item["created_at"]) for item in items)
        last_observed_at = max(item["content"].get("observed_at", item["created_at"]) for item in items)
        risk_level = "medium"
        if any(item["content"].get("risk_level") == "high" for item in items):
            risk_level = "high"
        elif all(item["content"].get("risk_level") == "low" for item in items):
            risk_level = "low"

        candidates.append(
            {
                "preference_kind": preference_kind,
                "pattern_key": pattern_key,
                "observation_memory_ids": [item["id"] for item in positive],
                "negative_observation_memory_ids": [item["id"] for item in negative],
                "positive_evidence_count": len(positive),
                "negative_evidence_count": len(negative),
                "distinct_project_count": len(distinct_projects),
                "first_observed_at": first_observed_at,
                "last_observed_at": last_observed_at,
                "risk_level": risk_level,
                "needs_confirmation": bool(negative) or (len(distinct_projects) < 2 and risk_level != "low"),
                "title": f"Possible preference: {preference_kind}",
                "summary": f"Aggregated {len(positive)} positive and {len(negative)} negative observations for {pattern_key}",
                "evidence": _flatten_evidence(positive + negative),
            }
        )

    return candidates


def materialize_preference_candidates(
    conn,
    user_id: str | None = None,
    project_id: str | None = None,
    min_positive: int = 3,
) -> list[int]:
    """Persist derived preference candidates, suppressing duplicates."""
    _flag_stable_preferences_with_negative_evidence(conn, user_id=user_id, project_id=project_id)
    inserted_ids: list[int] = []
    for candidate in derive_preference_candidates(
        conn,
        user_id=user_id,
        project_id=project_id,
        min_positive=min_positive,
    ):
        if _active_candidate_exists(conn, candidate, user_id=user_id, project_id=project_id):
            continue

        content = {
            "scope": "project" if project_id else "user",
            "kind": CANDIDATE_KIND,
            "preference_kind": candidate["preference_kind"],
            "pattern_key": candidate["pattern_key"],
            "positive_evidence_count": str(candidate["positive_evidence_count"]),
            "negative_evidence_count": str(candidate["negative_evidence_count"]),
            "distinct_project_count": str(candidate["distinct_project_count"]),
            "first_observed_at": candidate["first_observed_at"],
            "last_observed_at": candidate["last_observed_at"],
            "risk_level": candidate["risk_level"],
            "needs_confirmation": "true" if candidate["needs_confirmation"] else "false",
            "confirmed": "false",
            "observation_memory_ids": ",".join(str(mid) for mid in candidate["observation_memory_ids"]),
            "negative_observation_memory_ids": ",".join(str(mid) for mid in candidate["negative_observation_memory_ids"]),
        }
        evidence = candidate["evidence"] or [{"source_ref": "implicit-preference-review"}]
        source_ref = _candidate_source_ref(candidate, user_id=user_id, project_id=project_id)
        event_id = insert_event(
            conn,
            SourceEvent(
                source_type="event",
                source_ref=source_ref,
                actors=[user_id] if user_id else [],
                timestamp=utc_now(),
                content=candidate["summary"],
                scope=content["scope"],
                payload={"kind": CANDIDATE_KIND, "pattern_key": candidate["pattern_key"]},
            ),
            project_id=project_id,
            task_id=None,
            user_id=user_id,
        )
        memory_id = insert_memory(
            conn,
            MemoryCandidate(
                memory_type="preference",
                title=candidate["title"],
                summary=candidate["summary"],
                content=content,
                importance=0.5,
                confidence=_candidate_confidence(candidate),
                evidence=evidence,
                tags=["implicit_preference", CANDIDATE_KIND, candidate["pattern_key"]],
                change_reason=f"{CANDIDATE_KIND}: {candidate['pattern_key']}",
            ),
            event_id=event_id,
            project_id=project_id,
            task_id=None,
            user_id=user_id,
        )
        _insert_preference_event_entry(
            conn,
            event_id=event_id,
            event_time=utc_now(),
            relation="synthesized_preference_candidate",
            memory_id=memory_id,
            memory_type="preference",
            title=candidate["title"],
            summary=candidate["summary"],
            content=content,
            project_id=project_id,
            task_id=None,
            user_id=user_id,
            confidence=_candidate_confidence(candidate),
        )
        inserted_ids.append(memory_id)
    return inserted_ids


def prune_preference_candidate_branches(
    conn,
    user_id: str | None = None,
    project_id: str | None = None,
    max_active: int = _MAX_ACTIVE_CANDIDATES_PER_PATTERN,
) -> list[int]:
    """Archive lower-value active preference candidates beyond the per-pattern cap."""
    rows = conn.execute(
        """
        SELECT id, content_json, confidence, created_at, user_id, project_id
        FROM memories
        WHERE memory_type = 'preference'
          AND status = 'active'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        """,
        (f"%{CANDIDATE_KIND}%", user_id, user_id, project_id, project_id),
    ).fetchall()

    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        content = json.loads(row["content_json"])
        if content.get("kind") != CANDIDATE_KIND:
            continue
        key = (
            row["user_id"] or "",
            row["project_id"] or "",
            content.get("pattern_key", ""),
        )
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
                (now, f"archived by preference candidate branch limit max_active={max_active}", item["id"]),
            )
            archived_ids.append(item["id"])
    return archived_ids


def mark_stale_stable_preferences_for_review(
    conn,
    user_id: str | None = None,
    project_id: str | None = None,
    stale_days: int = _STALE_STABLE_PREFERENCE_DAYS,
) -> list[int]:
    """Mark long-unused stable preferences for user review without archiving them."""
    now = utc_now()
    now_dt = _parse_time(now)
    cutoff_dt = now_dt - timedelta(days=stale_days)
    rows = conn.execute(
        """
        SELECT id, title, summary, content_json, project_id, task_id, user_id, scope, confidence, created_at
        FROM memories
        WHERE memory_type = 'preference'
          AND status = 'active'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        """,
        (f"%{STABLE_PREFERENCE_KIND}%", user_id, user_id, project_id, project_id),
    ).fetchall()
    marked_ids: list[int] = []
    for row in rows:
        content = json.loads(row["content_json"])
        if content.get("kind") != STABLE_PREFERENCE_KIND or content.get("needs_review") == "true":
            continue
        last_recalled = _last_recalled_at(conn, int(row["id"]))
        freshness_anchor = _stable_preference_freshness_anchor(row["created_at"], content, last_recalled)
        if freshness_anchor >= cutoff_dt:
            continue

        content["needs_review"] = "true"
        content["review_reason"] = "stable preference stale or long unused"
        content["decay_reviewed_at"] = now
        content["decay_stale_days"] = str(stale_days)
        if last_recalled:
            content["last_recalled_at"] = last_recalled
        confidence = min(float(row["confidence"]), 0.6)
        event_id = insert_event(
            conn,
            SourceEvent(
                source_type="event",
                source_ref=f"stable-preference-decay-review:{int(row['id'])}",
                actors=[user_id or row["user_id"]] if (user_id or row["user_id"]) else [],
                timestamp=now,
                content=f"Stable preference marked for review due to age or low usage: {content.get('pattern_key', '')}",
                scope=content.get("scope", row["scope"]),
                payload={
                    "kind": STABLE_PREFERENCE_KIND,
                    "stable_preference_id": int(row["id"]),
                    "pattern_key": content.get("pattern_key", ""),
                    "action": "mark_stale_for_review",
                    "stale_days": stale_days,
                },
            ),
            project_id=row["project_id"],
            task_id=row["task_id"],
            user_id=user_id or row["user_id"],
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
                "stable preference stale or long unused",
                int(row["id"]),
            ),
        )
        _insert_preference_event_entry(
            conn,
            event_id=event_id,
            event_time=now,
            relation="stable_preference_marked_stale_for_review",
            memory_id=int(row["id"]),
            memory_type="preference",
            title=row["title"],
            summary=row["summary"],
            content=content,
            project_id=row["project_id"],
            task_id=row["task_id"],
            user_id=user_id or row["user_id"],
            confidence=confidence,
        )
        marked_ids.append(int(row["id"]))
    return marked_ids


def confirm_preference_candidate(conn, candidate_id: int, user_id: str | None = None) -> int:
    """Promote a preference candidate into a stable preference and archive the candidate."""
    row = conn.execute(
        """
        SELECT *
        FROM memories
        WHERE id = ?
          AND memory_type = 'preference'
          AND status = 'active'
        """,
        (candidate_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"unknown preference candidate: {candidate_id}")

    content = json.loads(row["content_json"])
    if content.get("kind") != CANDIDATE_KIND:
        raise ValueError(f"memory {candidate_id} is not a preference candidate")

    now = utc_now()
    stable_content = dict(content)
    stable_content.update(
        {
            "kind": STABLE_PREFERENCE_KIND,
            "confirmed": "true",
            "needs_confirmation": "false",
            "confirmed_at": now,
            "confirmed_by": user_id or row["user_id"] or "",
            "derived_from_candidate_id": str(candidate_id),
        }
    )

    event_id = insert_event(
        conn,
        SourceEvent(
            source_type="event",
            source_ref=f"implicit-preference-confirmation:{candidate_id}",
            actors=[user_id or row["user_id"]] if (user_id or row["user_id"]) else [],
            timestamp=now,
            content=f"Confirmed implicit preference candidate {candidate_id}: {content.get('pattern_key', '')}",
            scope=stable_content.get("scope", row["scope"]),
            payload={
                "kind": STABLE_PREFERENCE_KIND,
                "candidate_id": candidate_id,
                "pattern_key": content.get("pattern_key", ""),
            },
        ),
        project_id=row["project_id"],
        task_id=row["task_id"],
        user_id=user_id or row["user_id"],
    )
    stable_id = insert_memory(
        conn,
        MemoryCandidate(
            memory_type="preference",
            title=row["title"].replace("Possible preference", "Confirmed preference"),
            summary=row["summary"],
            content=stable_content,
            importance=max(float(row["importance"]), 0.65),
            confidence=max(float(row["confidence"]), 0.75),
            evidence=json.loads(row["evidence_json"]),
            tags=_stable_tags(json.loads(row["tags_json"])),
            replaces_memory_id=candidate_id,
            change_reason=f"confirmed preference candidate {candidate_id}",
        ),
        event_id=event_id,
        project_id=row["project_id"],
        task_id=row["task_id"],
        user_id=user_id or row["user_id"],
    )
    _insert_preference_event_entry(
        conn,
        event_id=event_id,
        event_time=now,
        relation="confirmed_stable_preference",
        memory_id=stable_id,
        memory_type="preference",
        title=row["title"].replace("Possible preference", "Confirmed preference"),
        summary=row["summary"],
        content=stable_content,
        project_id=row["project_id"],
        task_id=row["task_id"],
        user_id=user_id or row["user_id"],
        confidence=max(float(row["confidence"]), 0.75),
    )
    conn.execute(
        """
        UPDATE memories
        SET status = 'archived',
            updated_at = ?,
            change_reason = ?
        WHERE id = ?
        """,
        (now, f"confirmed into stable preference {stable_id}", candidate_id),
    )
    conn.execute(
        "UPDATE memories SET logical_layer = 'L2' WHERE id = ?",
        (stable_id,),
    )
    return stable_id


def _insert_preference_event_entry(
    conn,
    *,
    event_id: int,
    event_time: str,
    relation: str,
    memory_id: int,
    memory_type: str,
    title: str,
    summary: str,
    content: dict,
    project_id: str | None,
    task_id: str | None,
    user_id: str | None,
    confidence: float,
) -> int:
    subject = task_id or project_id or user_id or content.get("scope", "preference")
    return insert_event_entry(
        conn,
        EventEntry(
            source_event_id=event_id,
            event_time=event_time,
            entry_type="relational",
            subject=str(subject),
            relation=relation,
            object=str(content.get("pattern_key") or content.get("preference_kind") or title),
            qualifiers={
                "memory_id": memory_id,
                "memory_type": memory_type,
                "title": title,
                "summary": summary,
                "scope": content.get("scope", ""),
                "content_kind": content.get("kind", ""),
                "preference_kind": content.get("preference_kind", ""),
                "pattern_key": content.get("pattern_key", ""),
                "positive_evidence_count": content.get("positive_evidence_count", ""),
                "negative_evidence_count": content.get("negative_evidence_count", ""),
                "derived_from_candidate_id": content.get("derived_from_candidate_id", ""),
                **_preference_behavior_qualifiers(relation, memory_id, content),
            },
            project_id=project_id,
            task_id=task_id,
            user_id=user_id,
            confidence=confidence,
        ),
    )


def reject_preference_candidate(conn, candidate_id: int, user_id: str | None = None) -> int:
    """Archive a preference candidate rejected by the user."""
    row = conn.execute(
        """
        SELECT *
        FROM memories
        WHERE id = ?
          AND memory_type = 'preference'
          AND status = 'active'
        """,
        (candidate_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"unknown preference candidate: {candidate_id}")

    content = json.loads(row["content_json"])
    if content.get("kind") != CANDIDATE_KIND:
        raise ValueError(f"memory {candidate_id} is not a preference candidate")

    now = utc_now()
    event_id = insert_event(
        conn,
        SourceEvent(
            source_type="event",
            source_ref=f"implicit-preference-rejection:{candidate_id}",
            actors=[user_id or row["user_id"]] if (user_id or row["user_id"]) else [],
            timestamp=now,
            content=f"Rejected implicit preference candidate {candidate_id}: {content.get('pattern_key', '')}",
            scope=content.get("scope", row["scope"]),
            payload={
                "kind": CANDIDATE_KIND,
                "candidate_id": candidate_id,
                "pattern_key": content.get("pattern_key", ""),
                "action": "reject",
            },
        ),
        project_id=row["project_id"],
        task_id=row["task_id"],
        user_id=user_id or row["user_id"],
    )
    _insert_preference_event_entry(
        conn,
        event_id=event_id,
        event_time=now,
        relation="rejected_preference_candidate",
        memory_id=candidate_id,
        memory_type="preference",
        title=row["title"],
        summary=row["summary"],
        content=content,
        project_id=row["project_id"],
        task_id=row["task_id"],
        user_id=user_id or row["user_id"],
        confidence=min(float(row["confidence"]), 0.4),
    )
    conn.execute(
        """
        UPDATE memories
        SET status = 'archived',
            updated_at = ?,
            change_reason = ?
        WHERE id = ?
        """,
        (now, f"user rejected implicit preference candidate by {user_id or 'unknown'}", candidate_id),
    )
    return candidate_id


def _preference_behavior_qualifiers(relation: str, memory_id: int, content: dict) -> dict[str, str | int]:
    action = {
        "synthesized_preference_candidate": "candidate_created",
        "confirmed_stable_preference": "confirm",
        "rejected_preference_candidate": "reject",
        "reconfirmed_stable_preference": "reconfirm",
        "rejected_stable_preference": "reject",
        "stable_preference_marked_stale_for_review": "mark_review",
    }.get(relation, relation)
    correction = ""
    if action == "reject":
        correction = content.get("rejection_reason", "user rejected preference")
    elif action == "reconfirm":
        correction = "user reconfirmed preference"
    elif action == "mark_review":
        correction = content.get("review_reason", "preference requires review")
    return {
        "context_key": content.get("pattern_key") or content.get("preference_kind") or "",
        "action": action,
        "target_memory_id": memory_id,
        "correction": correction,
        "outcome": "archived" if action == "reject" else content.get("confirmed", ""),
        "polarity": content.get("polarity", ""),
    }


def reconfirm_stable_preference(conn, stable_id: int, user_id: str | None = None) -> int:
    """Re-confirm a stable preference after it was marked for review."""
    row = conn.execute(
        """
        SELECT *
        FROM memories
        WHERE id = ?
          AND memory_type = 'preference'
          AND status = 'active'
        """,
        (stable_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"unknown stable preference: {stable_id}")

    content = json.loads(row["content_json"])
    if content.get("kind") != STABLE_PREFERENCE_KIND:
        raise ValueError(f"memory {stable_id} is not a stable preference")

    now = utc_now()
    content["confirmed"] = "true"
    content["needs_review"] = "false"
    content["needs_confirmation"] = "false"
    content["reconfirmed_at"] = now
    content["reconfirmed_by"] = user_id or row["user_id"] or ""
    content.pop("review_reason", None)

    event_id = insert_event(
        conn,
        SourceEvent(
            source_type="event",
            source_ref=f"stable-preference-reconfirmation:{stable_id}",
            actors=[user_id or row["user_id"]] if (user_id or row["user_id"]) else [],
            timestamp=now,
            content=f"Re-confirmed stable preference {stable_id}: {content.get('pattern_key', '')}",
            scope=content.get("scope", row["scope"]),
            payload={
                "kind": STABLE_PREFERENCE_KIND,
                "stable_preference_id": stable_id,
                "pattern_key": content.get("pattern_key", ""),
                "action": "reconfirm",
            },
        ),
        project_id=row["project_id"],
        task_id=row["task_id"],
        user_id=user_id or row["user_id"],
    )
    confidence = max(float(row["confidence"]), 0.75)
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
            f"stable preference reconfirmed by {user_id or 'unknown'}",
            stable_id,
        ),
    )
    _insert_preference_event_entry(
        conn,
        event_id=event_id,
        event_time=now,
        relation="reconfirmed_stable_preference",
        memory_id=stable_id,
        memory_type="preference",
        title=row["title"],
        summary=row["summary"],
        content=content,
        project_id=row["project_id"],
        task_id=row["task_id"],
        user_id=user_id or row["user_id"],
        confidence=confidence,
    )
    return stable_id


def reject_stable_preference(conn, stable_id: int, user_id: str | None = None) -> int:
    """Archive a stable preference after user rejects it during review."""
    row = conn.execute(
        """
        SELECT *
        FROM memories
        WHERE id = ?
          AND memory_type = 'preference'
          AND status = 'active'
        """,
        (stable_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"unknown stable preference: {stable_id}")

    content = json.loads(row["content_json"])
    if content.get("kind") != STABLE_PREFERENCE_KIND:
        raise ValueError(f"memory {stable_id} is not a stable preference")

    now = utc_now()
    content["confirmed"] = "false"
    content["needs_review"] = "false"
    content["rejected_at"] = now
    content["rejected_by"] = user_id or row["user_id"] or ""
    content["rejection_reason"] = "user rejected stable preference during review"
    content.pop("review_reason", None)

    event_id = insert_event(
        conn,
        SourceEvent(
            source_type="event",
            source_ref=f"stable-preference-rejection:{stable_id}",
            actors=[user_id or row["user_id"]] if (user_id or row["user_id"]) else [],
            timestamp=now,
            content=f"Rejected stable preference {stable_id}: {content.get('pattern_key', '')}",
            scope=content.get("scope", row["scope"]),
            payload={
                "kind": STABLE_PREFERENCE_KIND,
                "stable_preference_id": stable_id,
                "pattern_key": content.get("pattern_key", ""),
                "action": "reject",
            },
        ),
        project_id=row["project_id"],
        task_id=row["task_id"],
        user_id=user_id or row["user_id"],
    )
    conn.execute(
        """
        UPDATE memories
        SET content_json = ?,
            status = 'archived',
            confidence = ?,
            updated_at = ?,
            change_reason = ?
        WHERE id = ?
        """,
        (
            json.dumps(content, ensure_ascii=True),
            min(float(row["confidence"]), 0.4),
            now,
            f"stable preference rejected by {user_id or 'unknown'}",
            stable_id,
        ),
    )
    _insert_preference_event_entry(
        conn,
        event_id=event_id,
        event_time=now,
        relation="rejected_stable_preference",
        memory_id=stable_id,
        memory_type="preference",
        title=row["title"],
        summary=row["summary"],
        content=content,
        project_id=row["project_id"],
        task_id=row["task_id"],
        user_id=user_id or row["user_id"],
        confidence=min(float(row["confidence"]), 0.4),
    )
    return stable_id


def _stable_tags(tags: list[str]) -> list[str]:
    stable = [tag for tag in tags if tag != CANDIDATE_KIND]
    if "stable_preference" not in stable:
        stable.append("stable_preference")
    return stable


def _last_recalled_at(conn, memory_id: int) -> str | None:
    row = conn.execute(
        "SELECT MAX(recalled_at) AS last_recalled_at FROM recall_log WHERE memory_id = ?",
        (memory_id,),
    ).fetchone()
    if not row:
        return None
    return row["last_recalled_at"]


def _stable_preference_freshness_anchor(
    created_at: str,
    content: dict,
    last_recalled_at: str | None,
) -> datetime:
    candidates = [
        _parse_time(created_at),
        _parse_time(content.get("confirmed_at", "")),
        _parse_time(content.get("reconfirmed_at", "")),
        _parse_time(last_recalled_at or ""),
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


def _flag_stable_preferences_with_negative_evidence(
    conn,
    user_id: str | None,
    project_id: str | None,
) -> None:
    rows = conn.execute(
        """
        SELECT id, content_json
        FROM memories
        WHERE memory_type = 'preference'
          AND status = 'active'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        """,
        (f"%{OBSERVATION_KIND}%", user_id, user_id, project_id, project_id),
    ).fetchall()
    negative_keys = set()
    for row in rows:
        content = json.loads(row["content_json"])
        if content.get("kind") == OBSERVATION_KIND and content.get("polarity") == "negative":
            negative_keys.add((content.get("preference_kind", ""), content.get("pattern_key", "")))
    if not negative_keys:
        return

    stable_rows = conn.execute(
        """
        SELECT id, content_json, confidence
        FROM memories
        WHERE memory_type = 'preference'
          AND status = 'active'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        """,
        (f"%{STABLE_PREFERENCE_KIND}%", user_id, user_id, project_id, project_id),
    ).fetchall()
    now = utc_now()
    for row in stable_rows:
        content = json.loads(row["content_json"])
        key = (content.get("preference_kind", ""), content.get("pattern_key", ""))
        if key not in negative_keys or content.get("needs_review") == "true":
            continue
        content["needs_review"] = "true"
        content["review_reason"] = "negative implicit preference evidence observed"
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
                min(float(row["confidence"]), 0.6),
                now,
                "negative implicit preference evidence observed",
                int(row["id"]),
            ),
        )


def _flatten_evidence(items: list[dict]) -> list[dict]:
    evidence: list[dict] = []
    seen_refs: set[str] = set()
    for item in items:
        for entry in item["evidence"]:
            source_ref = str(entry.get("source_ref", ""))
            if source_ref and source_ref in seen_refs:
                continue
            if source_ref:
                seen_refs.add(source_ref)
            evidence.append(entry)
    return evidence[:10]


def _active_candidate_exists(
    conn,
    candidate: dict,
    user_id: str | None,
    project_id: str | None,
) -> bool:
    rows = conn.execute(
        """
        SELECT content_json
        FROM memories
        WHERE memory_type = 'preference'
          AND status = 'active'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        """,
        (f"%{CANDIDATE_KIND}%", user_id, user_id, project_id, project_id),
    ).fetchall()
    for row in rows:
        content = json.loads(row["content_json"])
        if (
            content.get("kind") == CANDIDATE_KIND
            and content.get("preference_kind") == candidate["preference_kind"]
            and content.get("pattern_key") == candidate["pattern_key"]
        ):
            return True
    return False


def _candidate_source_ref(candidate: dict, user_id: str | None, project_id: str | None) -> str:
    owner = user_id or "all-users"
    scope = project_id or "all-projects"
    return f"implicit-preference-review:{owner}:{scope}:{candidate['pattern_key']}"


def _candidate_confidence(candidate: dict) -> float:
    confidence = 0.35
    confidence += min(float(candidate["positive_evidence_count"]) * 0.08, 0.32)
    confidence += min(max(float(candidate["distinct_project_count"]) - 1.0, 0.0) * 0.08, 0.16)
    confidence -= min(float(candidate["negative_evidence_count"]) * 0.12, 0.36)
    return round(max(0.1, min(confidence, 0.85)), 4)
