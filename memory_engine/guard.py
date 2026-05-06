"""Privacy guard: sensitive data detection, masking, and audit logging."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# PII / sensitive pattern definitions
# ---------------------------------------------------------------------------

_SENSITIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # API keys & tokens
    ("api_key", re.compile(r'(?:api[_-]?key|apikey|access[_-]?token|secret[_-]?key)\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{20,})', re.I)),
    ("bearer_token", re.compile(r'Bearer\s+[a-zA-Z0-9_\-\.]{20,}', re.I)),
    ("openai_key", re.compile(r'sk-[a-zA-Z0-9]{20,}')),
    ("generic_secret", re.compile(r'(?:password|passwd|pwd)\s*[:=]\s*["\']?(\S{6,})', re.I)),
    # Personal identity
    ("china_id", re.compile(r'\b\d{17}[\dXx]\b')),
    ("china_phone", re.compile(r'\b1[3-9]\d{9}\b')),
    ("email", re.compile(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b')),
    ("bank_card", re.compile(r'\b\d{16,19}\b')),
    # Salary & compensation
    ("salary", re.compile(r'(?:\u85aa\u8d44|\u5de5\u8d44|\u85aa\u6c34|\u6708\u85aa|\u5e74\u85aa|salary|compensation|pay)\s*[:\uff1a]?\s*[\d,.\u4e07]+', re.I)),
]


@dataclass(slots=True)
class ScanResult:
    """Result of a sensitive data scan."""
    has_sensitive: bool = False
    detections: list[dict[str, Any]] = field(default_factory=list)
    masked_content: str = ""
    masked_summary: str = ""


# ---------------------------------------------------------------------------
# Detection and masking
# ---------------------------------------------------------------------------

def scan_and_mask(content: str, summary: str) -> ScanResult:
    """Scan content for PII/sensitive data, return masked versions."""
    detections: list[dict[str, Any]] = []
    masked_content = content
    masked_summary = summary

    for category, pattern in _SENSITIVE_PATTERNS:
        for match in pattern.finditer(content):
            original = match.group(0)
            detections.append({
                "category": category,
                "position": match.start(),
                "snippet": original[:8] + "***",
            })
            masked_content = masked_content.replace(original, f"[{category}:REDACTED]")

        for match in pattern.finditer(summary):
            original = match.group(0)
            masked_summary = masked_summary.replace(original, f"[{category}:REDACTED]")
            # avoid duplicate detection entries if same text in both
            already = any(d["category"] == category for d in detections)
            if not already:
                detections.append({
                    "category": category,
                    "position": match.start(),
                    "snippet": original[:8] + "***",
                })

    return ScanResult(
        has_sensitive=len(detections) > 0,
        detections=detections,
        masked_content=masked_content,
        masked_summary=masked_summary,
    )


def contains_api_key(text: str) -> bool:
    """Quick check if text contains what looks like an API key or secret."""
    for category, pattern in _SENSITIVE_PATTERNS:
        if category in ("api_key", "bearer_token", "openai_key", "generic_secret"):
            if pattern.search(text):
                return True
    return False


# ---------------------------------------------------------------------------
# Audit log types
# ---------------------------------------------------------------------------

class AuditAction:
    WRITE = "write"
    UPDATE = "update"
    ARCHIVE = "archive"
    INVALIDATE = "invalidate"
    COMPACT_MERGE = "compact_merge"
    COMPACT_ARCHIVE = "compact_archive"
    PROMOTE = "promote"
    FLUSH = "flush"
    PROMOTION_REVIEW = "promotion_review"
    L_LAYER_PROMOTION = "l_layer_promotion"
    L_LAYER_DEMOTION = "l_layer_demotion"


@dataclass(slots=True)
class AuditEntry:
    action: str
    target_type: str  # "memory" or "event"
    target_id: int
    actor: str = ""
    detail: str = ""
    sensitive_detections: int = 0
