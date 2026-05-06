"""Sample local governance ballot reviewer.

Usage:
    python scripts/sample_governance_ballot.py < context.json

The script reads one governance context JSON object from stdin and writes
{"votes": [...]} to stdout. It is intended for local CLI demos via
GOVERNANCE_BALLOT_COMMAND_JSON, not for hosted OpenClaw auto-execution.
"""

from __future__ import annotations

import json
import sys
from typing import Any


SENSITIVE_MARKERS = (
    "api_key",
    "access_token",
    "secret_key",
    "password",
    "passwd",
    "bearer ",
    ":REDACTED]",
)


def main() -> int:
    try:
        context = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(json.dumps({"votes": [_vote("SampleFormatReviewer", "reject", 0.0, f"invalid json: {exc}")] }))
        return 0

    text = _flatten_context_text(context).lower()
    topic = str(context.get("topic") or "memory_governance")
    candidate_id = context.get("candidate_memory_id")

    if any(marker.lower() in text for marker in SENSITIVE_MARKERS):
        votes = [
            _vote(
                "SamplePrivacyAgent",
                "reject",
                0.05,
                f"local CLI reviewer found sensitive marker for {topic}",
                evidence_refs=[f"memory://{candidate_id}"] if candidate_id is not None else [],
            )
        ]
    else:
        votes = [
            _vote(
                "SampleUtilityAgent",
                "approve",
                0.8,
                f"local CLI reviewer found no blocking risk for {topic}",
                evidence_refs=_evidence_refs(context),
            )
        ]

    print(json.dumps({"votes": votes}, ensure_ascii=False))
    return 0


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
        "reviewer_role": "sample_cli_governance_reviewer",
        "vote": vote,
        "score": score,
        "reason": reason,
        "evidence_refs": evidence_refs or [],
    }


def _flatten_context_text(context: dict[str, Any]) -> str:
    return " ".join(
        [
            str(context.get("title") or ""),
            str(context.get("summary") or ""),
            json.dumps(context.get("content") or {}, ensure_ascii=False),
            json.dumps(context.get("evidence") or [], ensure_ascii=False),
        ]
    )


def _evidence_refs(context: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for item in context.get("evidence") or []:
        if isinstance(item, dict) and item.get("source_ref"):
            refs.append(str(item["source_ref"]))
    return refs


if __name__ == "__main__":
    raise SystemExit(main())
