"""Track E (Event-Centric Temporal Reasoning) benchmark cases.

These cases validate the StructMem-style event-centric layer added on top of
existing memory cards. They intentionally test the event_entries and event
bundle plumbing before any cross-event synthesis is introduced.
"""

from __future__ import annotations

from benchmarks.structures import (
    BenchmarkCase,
    Complexity,
    Direction,
    RecallSpec,
    ResultAssertion,
    SetupMemory,
    Track,
)


def _memory(
    memory_type: str,
    title: str,
    summary: str,
    content: dict,
    importance: float = 0.7,
    confidence: float = 0.8,
    evidence: list[dict] | None = None,
    scope: str = "project",
) -> SetupMemory:
    return SetupMemory(
        memory_type=memory_type,
        title=title,
        summary=summary,
        content=content,
        importance=importance,
        confidence=confidence,
        evidence=evidence or [{"source_ref": "bench://track-e"}],
        scope=scope,
    )


E01_EVENT_ENTRIES_FOR_CORE_TYPES = BenchmarkCase(
    case_id="E-01",
    track=Track.E,
    capability="event_entry_generation",
    description="Core memory card writes should create event-centric relational entries.",
    direction=Direction.B_PLUS_C,
    complexity_reasoning=Complexity.LOW,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["decision", "task_status", "preference"],
    setup_memories=[
        _memory(
            "decision",
            "Use SQLite for local memory",
            "The project decided to use SQLite for local memory storage.",
            {"scope": "project"},
        ),
        _memory(
            "task_status",
            "Feishu daemon is blocked",
            "Feishu daemon rollout is blocked by credential verification.",
            {"scope": "project"},
        ),
        _memory(
            "preference",
            "Prefer concise project updates",
            "User prefers concise project updates.",
            {"scope": "project", "preference_kind": "communication"},
        ),
    ],
    event_assertions=[
        ResultAssertion("event_entry_relation_count", ["recorded_decision", 1]),
        ResultAssertion("event_entry_relation_count", ["changed_task_status", 1]),
        ResultAssertion("event_entry_relation_count", ["showed_preference_for", 1]),
    ],
    spec_ref="Track-E-01",
)


E02_EVENT_BUNDLE_RECONSTRUCTION = BenchmarkCase(
    case_id="E-02",
    track=Track.E,
    capability="event_bundle_reconstruction",
    description="A source event bundle should reconstruct event, memory card, and relational entry.",
    direction=Direction.B,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["decision"],
    setup_memories=[
        _memory(
            "decision",
            "Use BM25 before vector search",
            "The memory engine should use BM25 before adding vector search.",
            {"scope": "project", "technology": "BM25"},
            evidence=[{"source_ref": "bench://bm25-decision"}],
        ),
    ],
    event_assertions=[
        ResultAssertion("event_bundle_has_relation", "recorded_decision"),
    ],
    recalls=[
        RecallSpec(
            query="BM25 vector search decision",
            project_id="proj-alpha",
            assertions=[ResultAssertion("contains_title", "Use BM25 before vector search")],
        ),
    ],
    spec_ref="Track-E-02",
)


E03_EVENT_LAYER_DOES_NOT_BREAK_RECALL = BenchmarkCase(
    case_id="E-03",
    track=Track.E,
    capability="event_layer_recall_compatibility",
    description="Adding event_entries must not change normal memory recall behavior.",
    direction=Direction.B,
    complexity_reasoning=Complexity.LOW,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["decision"],
    setup_memories=[
        _memory(
            "decision",
            "Use event bundle for evidence trace",
            "Use event bundle reconstruction to explain memory source evidence.",
            {"scope": "project"},
        ),
    ],
    recalls=[
        RecallSpec(
            query="event bundle evidence trace",
            project_id="proj-alpha",
            assertions=[
                ResultAssertion("contains_title", "Use event bundle for evidence trace"),
                ResultAssertion("contains_memory_type", "decision"),
            ],
        ),
    ],
    event_assertions=[
        ResultAssertion("event_entry_relation_exists", "recorded_decision"),
    ],
    spec_ref="Track-E-03",
)


E04_CONSTRAINED_CROSS_EVENT_SYNTHESIS = BenchmarkCase(
    case_id="E-04",
    track=Track.E,
    capability="constrained_cross_event_synthesis",
    description="Synthesis should answer a decision-change question only from supplied event bundles with traceable event ids.",
    direction=Direction.B,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["decision"],
    setup_memories=[
        _memory(
            "decision",
            "Use SQLite for local memory",
            "The project decided to use SQLite for local storage.",
            {"scope": "project"},
            evidence=[{"source_ref": "bench://synthesis-sqlite"}],
        ),
        _memory(
            "decision",
            "Use PostgreSQL for shared memory",
            "The project changed the storage decision to PostgreSQL for shared deployment.",
            {"scope": "project"},
            evidence=[{"source_ref": "bench://synthesis-postgres"}],
        ),
    ],
    event_assertions=[
        ResultAssertion("cross_event_synthesis", ["Did the storage decision change?", "ok", "decision_change_chain", 2]),
    ],
    spec_ref="Track-E-04",
)


TRACK_E_CASES = [
    E01_EVENT_ENTRIES_FOR_CORE_TYPES,
    E02_EVENT_BUNDLE_RECONSTRUCTION,
    E03_EVENT_LAYER_DOES_NOT_BREAK_RECALL,
    E04_CONSTRAINED_CROSS_EVENT_SYNTHESIS,
]
