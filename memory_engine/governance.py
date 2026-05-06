from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Callable
from collections.abc import Sequence
from typing import Any

import httpx

from .env import load_project_env
from .guard import scan_and_mask
from .models import utc_now


_SENSITIVE_REDACTION_MARKERS = (
    ":REDACTED]",
    "api_key",
    "access_token",
    "secret_key",
    "password",
    "passwd",
    "bearer",
    "openai_key",
)
_BALLOT_KIND = "deterministic_citizen_assembly"
BallotProvider = Callable[[dict[str, Any]], list[dict[str, Any]]]
_DEFAULT_LLM_BALLOT_AGENTS = [
    {
        "reviewer_name": "LLMEvidenceAgent",
        "reviewer_role": "llm_evidence_reviewer",
        "focus": "Check whether this memory has enough traceable evidence and whether the conclusion follows from the evidence.",
    },
    {
        "reviewer_name": "LLMRiskAgent",
        "reviewer_role": "llm_privacy_and_risk_reviewer",
        "focus": "Check privacy, secret leakage, harmful overgeneralization, and whether this memory should require manual confirmation.",
    },
    {
        "reviewer_name": "LLMUtilityAgent",
        "reviewer_role": "llm_utility_reviewer",
        "focus": "Check whether this memory is useful, durable, scoped correctly, and not too noisy for long-term reuse.",
    },
]


class GovernanceRejected(ValueError):
    def __init__(self, decision: dict[str, Any]) -> None:
        self.decision = decision
        super().__init__(f"governance rejected memory {decision['candidate_memory_id']}: {decision['reason']}")


def build_cli_ballot_provider(command: Sequence[str], *, timeout_s: float = 30.0) -> BallotProvider:
    """Build a ballot provider that sends governance context JSON to a local CLI command.

    The command must read one JSON object from stdin and write either a JSON list of votes
    or {"votes": [...]} to stdout. It is intentionally opt-in and shell-free.
    """
    command = [str(part) for part in command if str(part)]
    if not command:
        raise ValueError("governance ballot CLI command must not be empty")

    def provider(context: dict[str, Any]) -> list[dict[str, Any]]:
        completed = subprocess.run(
            command,
            input=json.dumps(context, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_s,
            check=False,
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()[:500]
            raise RuntimeError(f"governance ballot CLI exited with {completed.returncode}: {stderr}")
        payload_text = (completed.stdout or "").strip()
        if not payload_text:
            return []
        payload = json.loads(payload_text)
        if isinstance(payload, dict):
            payload = payload.get("votes", [])
        if not isinstance(payload, list):
            raise ValueError("governance ballot CLI must return a vote list or {'votes': [...]}")
        return [vote for vote in payload if isinstance(vote, dict)]

    return provider


def governance_ballot_provider_from_env(env: dict[str, str] | None = None) -> BallotProvider | None:
    """Create optional local CLI and/or LLM ballot providers from environment settings.

    Preferred form on Windows:
    GOVERNANCE_BALLOT_COMMAND_JSON=["python","agent_vote.py"]

    Fallback form:
    GOVERNANCE_BALLOT_COMMAND=python agent_vote.py
    """
    if env is None:
        load_project_env()
        env = os.environ
    providers: list[BallotProvider] = []
    timeout_s = _float_env(env.get("GOVERNANCE_BALLOT_TIMEOUT_S"), 30.0)
    raw_json = env.get("GOVERNANCE_BALLOT_COMMAND_JSON")
    if raw_json:
        command = json.loads(raw_json)
        if not isinstance(command, list):
            raise ValueError("GOVERNANCE_BALLOT_COMMAND_JSON must be a JSON string list")
        providers.append(build_cli_ballot_provider([str(part) for part in command], timeout_s=timeout_s))

    raw_command = env.get("GOVERNANCE_BALLOT_COMMAND")
    if raw_command:
        providers.append(build_cli_ballot_provider(shlex.split(raw_command, posix=os.name != "nt"), timeout_s=timeout_s))

    if _env_enabled(env.get("GOVERNANCE_LLM_BALLOT_ENABLED")):
        llm_provider = _llm_ballot_provider_from_env(env)
        if llm_provider is not None:
            providers.append(llm_provider)

    if not providers:
        return None
    if len(providers) == 1:
        return providers[0]

    def combined_provider(context: dict[str, Any]) -> list[dict[str, Any]]:
        votes: list[dict[str, Any]] = []
        for provider in providers:
            votes.extend(provider(context))
        return votes

    return combined_provider


def build_llm_ballot_provider(
    *,
    api_base: str,
    api_key: str,
    model: str,
    agents: list[dict[str, str]] | None = None,
    timeout_s: float = 30.0,
) -> BallotProvider:
    """Build an OpenAI-compatible multi-agent ballot provider."""
    agent_specs = agents or _DEFAULT_LLM_BALLOT_AGENTS

    def provider(context: dict[str, Any]) -> list[dict[str, Any]]:
        votes: list[dict[str, Any]] = []
        for agent in agent_specs:
            votes.append(_run_llm_reviewer(api_base, api_key, model, agent, context, timeout_s=timeout_s))
        return votes

    return provider


def _llm_ballot_provider_from_env(env: dict[str, str]) -> BallotProvider | None:
    api_key = env.get("GOVERNANCE_LLM_BALLOT_API_KEY") or env.get("OPENAI_API_KEY")
    api_base = env.get("GOVERNANCE_LLM_BALLOT_API_BASE") or env.get("OPENAI_API_BASE") or env.get("OPENAI_BASE_URL")
    model = env.get("GOVERNANCE_LLM_BALLOT_MODEL") or env.get("OPENAI_MODEL")
    if not api_key or not api_base or not model:
        return None
    timeout_s = _float_env(env.get("GOVERNANCE_LLM_BALLOT_TIMEOUT_S"), 30.0)
    agents = _load_llm_agents(env.get("GOVERNANCE_LLM_BALLOT_AGENTS_JSON"))
    return build_llm_ballot_provider(
        api_base=api_base,
        api_key=api_key,
        model=model,
        agents=agents,
        timeout_s=timeout_s,
    )


def review_workflow_strategy_candidate(
    conn,
    candidate_id: int,
    ballot_provider: BallotProvider | None = None,
) -> dict[str, Any]:
    """Run deterministic governance reviewers before promoting a workflow strategy."""
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise ValueError(f"memory {candidate_id} not found")

    content = _loads(row["content_json"], {})
    evidence = _loads(row["evidence_json"], [])
    votes = [
        _evidence_review(conn, row, evidence),
        _privacy_review(row, content, evidence),
        _utility_review(row, content),
        _scope_review(row),
        _conflict_review(conn, row, content),
    ]
    votes.extend(_provider_votes(ballot_provider, "workflow_strategy_promotion", row, content, evidence, votes))
    assembly = _record_assembly_decision(
        conn,
        candidate_id,
        topic="workflow_strategy_promotion",
        votes=votes,
        required_reviewers=("EvidenceReviewer", "PrivacyReviewer", "UtilityReviewer", "ScopeReviewer"),
        blocking_reviewers=("ConflictReviewer",),
        approve_reason="deterministic governance approved",
    )
    return {
        "candidate_memory_id": candidate_id,
        "decision": assembly["decision"],
        "votes": votes,
        "reason": assembly["reason"],
        "assembly": assembly,
        "assembly_id": assembly["assembly_id"],
    }


def review_preference_candidate(
    conn,
    candidate_id: int,
    ballot_provider: BallotProvider | None = None,
) -> dict[str, Any]:
    """Run multi-reviewer governance before confirming an implicit preference."""
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise ValueError(f"memory {candidate_id} not found")

    content = _loads(row["content_json"], {})
    evidence = _loads(row["evidence_json"], [])
    if row["memory_type"] != "preference" or content.get("kind") != "preference_candidate":
        raise ValueError(f"memory {candidate_id} is not a preference candidate")

    votes = [
        _evidence_review(conn, row, evidence),
        _privacy_review(row, content, evidence),
        _scope_review(row),
        _preference_evidence_review(content),
        _preference_conflict_review(conn, row, content),
    ]
    votes.extend(_provider_votes(ballot_provider, "implicit_preference_confirmation", row, content, evidence, votes))
    assembly = _record_assembly_decision(
        conn,
        candidate_id,
        topic="implicit_preference_confirmation",
        votes=votes,
        required_reviewers=(
            "EvidenceReviewer",
            "PrivacyReviewer",
            "ScopeReviewer",
            "PreferenceEvidenceReviewer",
        ),
        blocking_reviewers=("PreferenceConflictReviewer",),
        approve_reason="implicit preference governance approved",
    )
    return {
        "candidate_memory_id": candidate_id,
        "decision": assembly["decision"],
        "votes": votes,
        "reason": assembly["reason"],
        "assembly": assembly,
        "assembly_id": assembly["assembly_id"],
    }


def review_memory_promotion(
    conn,
    memory_id: int,
    to_layer: str,
    ballot_provider: BallotProvider | None = None,
) -> dict[str, Any]:
    """Run deterministic governance for L-layer promotion."""
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        raise ValueError(f"memory {memory_id} not found")

    content = _loads(row["content_json"], {})
    evidence = _loads(row["evidence_json"], [])
    votes = [
        _evidence_review(conn, row, evidence),
        _privacy_review(row, content, evidence),
        _scope_review(row),
    ]
    required = ["EvidenceReviewer", "PrivacyReviewer", "ScopeReviewer"]
    if to_layer == "L3":
        votes.append(_utility_review(row, content))
        votes.append(_conflict_review(conn, row, content))
        required.append("UtilityReviewer")
    topic = f"promotion_to_{to_layer}"
    votes.extend(_provider_votes(ballot_provider, topic, row, content, evidence, votes))

    assembly = _record_assembly_decision(
        conn,
        memory_id,
        topic=topic,
        votes=votes,
        required_reviewers=tuple(required),
        blocking_reviewers=("ConflictReviewer",) if to_layer == "L3" else (),
        approve_reason=f"promotion to {to_layer} approved",
    )
    return {
        "candidate_memory_id": memory_id,
        "decision": assembly["decision"],
        "votes": votes,
        "reason": assembly["reason"],
        "assembly": assembly,
        "assembly_id": assembly["assembly_id"],
    }


def _record_assembly_decision(
    conn,
    candidate_id: int,
    *,
    topic: str,
    votes: list[dict[str, Any]],
    required_reviewers: tuple[str, ...],
    blocking_reviewers: tuple[str, ...],
    approve_reason: str,
) -> dict[str, Any]:
    now = utc_now()
    assembly_id = f"{_BALLOT_KIND}:{topic}:{candidate_id}:{now}"
    _record_votes(conn, candidate_id, votes, assembly_id=assembly_id)
    by_name = {vote["reviewer_name"]: vote for vote in votes}
    approved = all(by_name[name]["vote"] == "approve" for name in required_reviewers)
    approved = approved and all(by_name[name]["vote"] != "reject" for name in blocking_reviewers if name in by_name)
    approved = approved and not any(vote["vote"] == "reject" and vote.get("external") for vote in votes)
    decision = "approve" if approved else "reject"
    reject_reasons = [
        f"{vote['reviewer_name']}: {vote['reason']}"
        for vote in votes
        if vote["vote"] == "reject"
    ]
    counts = {
        "approve": sum(1 for vote in votes if vote["vote"] == "approve"),
        "reject": sum(1 for vote in votes if vote["vote"] == "reject"),
        "abstain": sum(1 for vote in votes if vote["vote"] == "abstain"),
    }
    return {
        "assembly_id": assembly_id,
        "ballot_kind": _BALLOT_KIND,
        "topic": topic,
        "decision": decision,
        "reason": "; ".join(reject_reasons) if reject_reasons else approve_reason,
        "quorum": len(votes),
        "required_reviewers": list(required_reviewers),
        "blocking_reviewers": list(blocking_reviewers),
        "counts": counts,
        "external_vote_count": sum(1 for vote in votes if vote.get("external")),
    }


def _provider_votes(
    ballot_provider: BallotProvider | None,
    topic: str,
    row,
    content: dict[str, Any],
    evidence: list[dict[str, Any]],
    deterministic_votes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if ballot_provider is None:
        return []
    context = {
        "topic": topic,
        "candidate_memory_id": int(row["id"]),
        "memory_type": row["memory_type"],
        "title": row["title"],
        "summary": row["summary"],
        "content": dict(content),
        "evidence": list(evidence),
        "deterministic_votes": [dict(vote) for vote in deterministic_votes],
    }
    provided = ballot_provider(context)
    return [_normalize_provider_vote(vote) for vote in provided or []]


def _run_llm_reviewer(
    api_base: str,
    api_key: str,
    model: str,
    agent: dict[str, str],
    context: dict[str, Any],
    *,
    timeout_s: float,
) -> dict[str, Any]:
    reviewer_name = agent.get("reviewer_name") or agent.get("name") or "LLMBallotAgent"
    reviewer_role = agent.get("reviewer_role") or reviewer_name
    focus = agent.get("focus") or "Review the candidate memory and vote approve, reject, or abstain."
    prompt = {
        "reviewer_name": reviewer_name,
        "reviewer_role": reviewer_role,
        "focus": focus,
        "topic": context.get("topic"),
        "candidate_memory_id": context.get("candidate_memory_id"),
        "memory_type": context.get("memory_type"),
        "title": context.get("title"),
        "summary": context.get("summary"),
        "content": context.get("content"),
        "evidence": context.get("evidence"),
        "deterministic_votes": context.get("deterministic_votes"),
        "instructions": (
            "Return only one JSON object with keys: reviewer_name, reviewer_role, vote, score, reason, evidence_refs. "
            "vote must be approve, reject, or abstain. Reject if evidence is weak, privacy risk exists, "
            "scope is wrong, or the candidate overgeneralizes from sparse observations."
        ),
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an independent memory-governance voting agent. "
                    "You must be conservative and must not invent facts beyond the supplied JSON context."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "max_tokens": 260,
    }
    try:
        with httpx.Client(timeout=timeout_s) as client:
            response = client.post(
                _chat_completions_url(api_base),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        vote = _loads_json_object(str(content))
        if not isinstance(vote, dict):
            raise ValueError("LLM reviewer did not return a JSON object")
        vote.setdefault("reviewer_name", reviewer_name)
        vote.setdefault("reviewer_role", reviewer_role)
        vote.setdefault("reason", "LLM reviewer vote")
        return vote
    except Exception as exc:
        return {
            "reviewer_name": reviewer_name,
            "reviewer_role": reviewer_role,
            "vote": "reject",
            "score": 0.0,
            "reason": f"LLM reviewer failed closed: {type(exc).__name__}",
            "evidence_refs": [],
        }


def _normalize_provider_vote(raw: dict[str, Any]) -> dict[str, Any]:
    reviewer_name = str(raw.get("reviewer_name") or raw.get("agent_name") or raw.get("name") or "ExternalReviewer")
    vote = str(raw.get("vote") or "abstain").lower()
    if vote not in {"approve", "reject", "abstain"}:
        vote = "abstain"
    try:
        score = float(raw.get("score", 0.5))
    except (TypeError, ValueError):
        score = 0.5
    evidence_refs = raw.get("evidence_refs") or []
    if not isinstance(evidence_refs, list):
        evidence_refs = [str(evidence_refs)]
    normalized = _vote(
        reviewer_name,
        vote,
        score,
        str(raw.get("reason") or "external ballot provider vote"),
        evidence_refs=[str(item) for item in evidence_refs],
    )
    normalized["external"] = True
    normalized["reviewer_role"] = str(raw.get("reviewer_role") or reviewer_name)
    return normalized


def _load_llm_agents(raw: str | None) -> list[dict[str, str]] | None:
    if not raw:
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("GOVERNANCE_LLM_BALLOT_AGENTS_JSON must be a JSON list")
    agents: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        agents.append({str(key): str(value) for key, value in item.items()})
    return agents or None


def _chat_completions_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _loads_json_object(raw: str) -> Any:
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def _evidence_review(conn, row, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    event = conn.execute("SELECT source_ref FROM events WHERE id = ?", (int(row["source_event_id"]),)).fetchone()
    has_evidence = bool(evidence)
    has_source_ref = any(item.get("source_ref") for item in evidence if isinstance(item, dict))
    evidence_refs = [str(item.get("source_ref")) for item in evidence if isinstance(item, dict) and item.get("source_ref")]
    if has_evidence and event is not None and has_source_ref:
        return _vote(
            "EvidenceReviewer",
            "approve",
            1.0,
            "evidence and source event are traceable",
            evidence_refs=evidence_refs,
        )
    return _vote(
        "EvidenceReviewer",
        "reject",
        0.0,
        "missing traceable evidence or source event",
        evidence_refs=evidence_refs,
    )


def _privacy_review(row, content: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    text = " ".join(
        [
            str(row["title"]),
            str(row["summary"]),
            json.dumps(content, ensure_ascii=False),
            json.dumps(evidence, ensure_ascii=False),
        ]
    )
    scan = scan_and_mask(text, "")
    lowered = text.lower()
    if scan.has_sensitive or any(marker in lowered for marker in _SENSITIVE_REDACTION_MARKERS):
        return _vote("PrivacyReviewer", "reject", 0.0, "privacy or secret marker detected")
    return _vote("PrivacyReviewer", "approve", 1.0, "no sensitive marker detected")


def _utility_review(row, content: dict[str, Any]) -> dict[str, Any]:
    if row["memory_type"] in {"decision", "task_status", "preference", "habit_rule"}:
        if float(row["importance"]) >= 0.5 and float(row["confidence"]) >= 0.6:
            return _vote("UtilityReviewer", "approve", 0.75, "memory has sufficient durable utility")
    success_count = _int_value(content.get("success_evidence_count"))
    recommended_steps = content.get("recommended_steps")
    if success_count >= 2 or (isinstance(recommended_steps, list) and len(recommended_steps) >= 2):
        return _vote("UtilityReviewer", "approve", 0.9, "workflow evidence is reusable")
    if float(row["importance"]) >= 0.7 and float(row["confidence"]) >= 0.7:
        return _vote("UtilityReviewer", "approve", 0.75, "candidate has high importance and confidence")
    return _vote("UtilityReviewer", "reject", 0.25, "insufficient reuse evidence")


def _scope_review(row) -> dict[str, Any]:
    scope = str(row["scope"])
    if scope == "project" and row["project_id"]:
        return _vote("ScopeReviewer", "approve", 1.0, "project scope has project_id")
    if scope == "user" and row["user_id"]:
        return _vote("ScopeReviewer", "approve", 1.0, "user scope has user_id")
    if scope in {"task", "session"} and row["task_id"]:
        return _vote("ScopeReviewer", "approve", 1.0, "task/session scope has task_id")
    return _vote("ScopeReviewer", "reject", 0.0, f"scope {scope} lacks required identifier")


def _conflict_review(conn, row, content: dict[str, Any]) -> dict[str, Any]:
    task_type = str(content.get("task_type") or "")
    if not task_type:
        return _vote("ConflictReviewer", "abstain", 0.5, "task_type missing")
    existing = conn.execute(
        """
        SELECT id
        FROM memories
        WHERE id != ?
          AND status = 'active'
          AND memory_type = 'procedural'
          AND content_json LIKE '%workflow_skill%'
          AND content_json LIKE ?
          AND (? IS NULL OR project_id = ?)
        LIMIT 1
        """,
        (int(row["id"]), f"%{task_type}%", row["project_id"], row["project_id"]),
    ).fetchone()
    if existing is not None:
        return _vote("ConflictReviewer", "reject", 0.1, "active workflow skill already exists for task_type")
    return _vote("ConflictReviewer", "approve", 0.8, "no active workflow skill conflict found")


def _preference_evidence_review(content: dict[str, Any]) -> dict[str, Any]:
    positive = _int_value(content.get("positive_evidence_count"))
    negative = _int_value(content.get("negative_evidence_count"))
    risk_level = str(content.get("risk_level") or "medium")
    if positive < 3:
        return _vote(
            "PreferenceEvidenceReviewer",
            "reject",
            0.2,
            f"not enough positive observations for implicit habit confirmation: {positive}",
        )
    if negative >= positive:
        return _vote(
            "PreferenceEvidenceReviewer",
            "reject",
            0.2,
            f"negative observations are not weaker than positive observations: positive={positive}, negative={negative}",
        )
    if risk_level == "high":
        return _vote(
            "PreferenceEvidenceReviewer",
            "reject",
            0.25,
            "high-risk implicit habit requires explicit user confirmation outside automatic voting",
        )
    return _vote(
        "PreferenceEvidenceReviewer",
        "approve",
        0.8,
        f"implicit habit has enough supporting observations: positive={positive}, negative={negative}",
    )


def _preference_conflict_review(conn, row, content: dict[str, Any]) -> dict[str, Any]:
    pattern_key = str(content.get("pattern_key") or "")
    if not pattern_key:
        return _vote("PreferenceConflictReviewer", "reject", 0.1, "preference candidate missing pattern_key")
    existing = conn.execute(
        """
        SELECT id
        FROM memories
        WHERE id != ?
          AND status = 'active'
          AND memory_type = 'preference'
          AND content_json LIKE '%stable_preference%'
          AND content_json LIKE ?
          AND (? IS NULL OR user_id = ?)
          AND (? IS NULL OR project_id = ?)
        LIMIT 1
        """,
        (
            int(row["id"]),
            f"%{pattern_key}%",
            row["user_id"], row["user_id"],
            row["project_id"], row["project_id"],
        ),
    ).fetchone()
    if existing is not None:
        return _vote("PreferenceConflictReviewer", "reject", 0.2, "active stable preference already exists")
    return _vote("PreferenceConflictReviewer", "approve", 0.8, "no active stable preference conflict found")


def _record_votes(conn, candidate_id: int, votes: list[dict[str, Any]], *, assembly_id: str) -> None:
    now = utc_now()
    for vote in votes:
        conn.execute(
            """
            INSERT INTO memory_votes (
                candidate_memory_id, reviewer_name, vote, score, reason, created_at,
                assembly_id, ballot_kind, reviewer_role, evidence_refs_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                vote["reviewer_name"],
                vote["vote"],
                vote["score"],
                vote["reason"],
                now,
                assembly_id,
                _BALLOT_KIND,
                vote.get("reviewer_role") or vote["reviewer_name"],
                json.dumps(vote.get("evidence_refs", []), ensure_ascii=True),
            ),
        )


def _vote(
    reviewer_name: str,
    vote: str,
    score: float,
    reason: str,
    *,
    evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "reviewer_name": reviewer_name,
        "vote": vote,
        "score": score,
        "reason": reason,
        "evidence_refs": evidence_refs or [],
        "external": False,
        "reviewer_role": reviewer_name,
    }


def _loads(raw: str, fallback: Any) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_env(value: str | None, fallback: float) -> float:
    try:
        return float(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _env_enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
