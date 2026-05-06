"""InjectionFormatter — recall results → Markdown snippet for context injection."""

from __future__ import annotations

from typing import Any


def format_injection(results: list[dict[str, Any]]) -> str:
    """Convert recall results to a Markdown snippet for OpenClaw context injection.

    Tier 1 (confidence >= 0.7): injected directly
    Tier 2 (0.4 <= confidence < 0.7): injected with caution note
    Tier 3 (confidence < 0.4): omitted
    """
    if not results:
        return ""

    lines = ["## External Memory"]

    for r in results:
        confidence = r.get("confidence", 0.0)
        if confidence < 0.4:
            continue

        mem_type = r.get("memory_type", "unknown").upper()
        title = r.get("title", "")
        summary = r.get("summary", "")
        evidence = r.get("evidence", [])

        evidence_line = ""
        if evidence:
            ev = evidence[0]
            src = ev.get("source_ref", "")
            src_type = ev.get("source_type", "")
            if src:
                evidence_line = f"\n  Evidence: {src_type}:{src}"

        if confidence >= 0.7:
            lines.append(f"- [{mem_type}] {title}")
            lines.append(f"  {summary}{evidence_line}")
        else:
            lines.append(f"- [{mem_type}] {title} (confidence={confidence:.2f})")
            lines.append(f"  {summary}{evidence_line}")
            lines.append("  Note: Verify this information before acting.")

    if len(lines) == 1:
        return ""

    return "\n".join(lines)
