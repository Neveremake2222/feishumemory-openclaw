from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MemoryType(str, Enum):
    # spec 4.2 classification
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    SOCIAL = "social"
    # spec 4.3 / Phase 1 core card types
    DECISION = "decision"
    TASK_STATUS = "task_status"
    PREFERENCE = "preference"
    HABIT_RULE = "habit_rule"


class SourceType(str, Enum):
    MESSAGE = "message"
    DOC = "doc"
    TASK = "task"
    MEETING = "meeting"
    APPROVAL = "approval"
    EVENT = "event"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    INVALID = "invalid"
    PROMOTED = "promoted"


class MemoryLayer(str, Enum):
    WORKING = "working"
    FACTUAL = "factual"


class ConflictType(str, Enum):
    FACT_OVERRIDE = "fact_override"
    ROLE_CHANGE = "role_change"
    GOAL_DRIFT = "goal_drift"
    CONSTRAINT_SUPPLEMENT = "constraint_supplement"
    EVIDENCE_CONFLICT = "evidence_conflict"


class Scope(str, Enum):
    USER = "user"
    SESSION = "session"
    TASK = "task"
    PROJECT = "project"
    ORGANIZATION = "organization"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class SourceEvent:
    source_type: str
    source_ref: str
    actors: list[str]
    timestamp: str
    content: str
    scope: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        SourceType(self.source_type)
        Scope(self.scope)


@dataclass(slots=True)
class MemoryCandidate:
    memory_type: str
    title: str
    summary: str
    content: dict[str, Any]
    importance: float = 0.5
    confidence: float = 0.8
    evidence: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    replaces_memory_id: int | None = None
    change_reason: str | None = None

    def __post_init__(self) -> None:
        if self.memory_type not in {m.value for m in MemoryType}:
            raise ValueError(f"invalid memory_type: {self.memory_type}")
        if not (0.0 <= self.importance <= 1.0):
            raise ValueError(f"importance must be in [0, 1], got {self.importance}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        if not self.evidence:
            raise ValueError("evidence must contain at least one source reference")


@dataclass(slots=True)
class EventEntry:
    source_event_id: int
    event_time: str
    entry_type: str
    subject: str
    relation: str
    object: str
    qualifiers: dict[str, Any] = field(default_factory=dict)
    project_id: str | None = None
    task_id: str | None = None
    user_id: str | None = None
    confidence: float = 0.6


@dataclass(slots=True)
class RecallRequest:
    query: str
    user_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    scope: str | None = None
    intent: str = "general"
    memory_layer: str | None = None
    logical_layer: str | None = None
    include_candidates: bool = False


@dataclass(slots=True)
class RecallContext:
    user_id: str | None
    project_id: str | None
    task_id: str | None
    intent: str
    last_queries: list[str]


@dataclass(slots=True)
class PromotionResult:
    memory_id: int
    from_layer: str
    to_layer: str
    direction: str   # 'B' or 'C'
    trigger: str     # e.g. 'same_theme_decisions_3'
    confidence_passed: bool
    timestamp: str
