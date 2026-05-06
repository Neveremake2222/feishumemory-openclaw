from __future__ import annotations

import re
from typing import Any

from .models import MemoryCandidate
from .ranking import _overlap_score


def classify_conflict(
    candidate: MemoryCandidate,
    existing: dict[str, Any],
) -> dict[str, Any] | None:
    candidate_text = candidate.title + " " + candidate.summary
    existing_text = existing["title"] + " " + existing["summary"]
    overlap = _overlap_score(candidate_text, existing_text)
    if overlap <= 0.6:
        return None

    mem_type = candidate.memory_type
    combined_lower = (candidate_text + " " + existing_text).lower()

    if _has_evidence_conflict(candidate, existing):
        return {
            "conflict_type": "evidence_conflict",
            "existing_memory_id": int(existing["id"]),
            "existing_title": existing["title"],
            "overlap_score": round(overlap, 4),
            "resolution_strategy": "keep both active, lower confidence, flag for review",
            "resolution_action": "flag_review",
        }

    if mem_type == "decision":
        role_keywords = {"\u8d1f\u8d23\u4eba", "owner", "lead", "role", "assignee"}
        if any(kw in combined_lower for kw in role_keywords):
            return {
                "conflict_type": "role_change",
                "existing_memory_id": int(existing["id"]),
                "existing_title": existing["title"],
                "overlap_score": round(overlap, 4),
                "resolution_strategy": "supersede + notify relevant parties",
                "resolution_action": "supersede",
            }

    if mem_type == "decision":
        goal_keywords = {"\u76ee\u6807", "\u8ba1\u5212", "\u65b9\u6848", "goal", "plan", "replan", "adjust"}
        if any(kw in combined_lower for kw in goal_keywords):
            return {
                "conflict_type": "goal_drift",
                "existing_memory_id": int(existing["id"]),
                "existing_title": existing["title"],
                "overlap_score": round(overlap, 4),
                "resolution_strategy": "preserve original as decision chain, new as active",
                "resolution_action": "keep_both",
            }

    if mem_type == "semantic":
        additive_keywords = {"\u989d\u5916", "\u65b0\u589e", "\u8865\u5145", "also", "additional", "plus"}
        if any(kw in combined_lower for kw in additive_keywords):
            return {
                "conflict_type": "constraint_supplement",
                "existing_memory_id": int(existing["id"]),
                "existing_title": existing["title"],
                "overlap_score": round(overlap, 4),
                "resolution_strategy": "keep both active as incremental",
                "resolution_action": "keep_both",
            }

    if mem_type in ("decision", "task_status") and _same_factual_topic(candidate, existing):
        return {
            "conflict_type": "fact_override",
            "existing_memory_id": int(existing["id"]),
            "existing_title": existing["title"],
            "overlap_score": round(overlap, 4),
            "resolution_strategy": "supersede old, link version chain",
            "resolution_action": "supersede",
        }

    return {
        "conflict_type": "potential_overlap",
        "existing_memory_id": int(existing["id"]),
        "existing_title": existing["title"],
        "overlap_score": round(overlap, 4),
        "resolution_strategy": "keep both active; insufficient evidence for typed conflict",
        "resolution_action": "keep_both",
    }


def _same_factual_topic(candidate: MemoryCandidate, existing: dict[str, Any]) -> bool:
    cand_pid = candidate.content.get("project_id") or ""
    exist_pid = existing.get("project_id") or ""
    return bool(cand_pid and exist_pid and cand_pid == exist_pid)


def _has_evidence_conflict(candidate: MemoryCandidate, existing: dict[str, Any]) -> bool:
    cand_nums = _numeric_evidence(candidate.summary)
    exist_nums = _numeric_evidence(existing["summary"])
    if cand_nums and exist_nums and cand_nums != exist_nums:
        candidate_text = candidate.title + " " + candidate.summary
        existing_text = existing["title"] + " " + existing["summary"]
        return _overlap_score(candidate_text, existing_text) > 0.6
    return False


def _numeric_evidence(text: str) -> set[int]:
    """Extract numeric evidence while ignoring label-like tokens such as Q3 or v2."""
    return {int(match.group(0)) for match in re.finditer(r"(?<![A-Za-z])\d+", text)}
