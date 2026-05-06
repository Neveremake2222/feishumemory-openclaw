"""Benchmark case data structures and runnable test framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Direction(str, Enum):
    B = "B"
    C = "C"
    B_PLUS_C = "B+C"


class Track(str, Enum):
    A = "A"  # Dialogue Memory
    B = "B"  # Task Decision
    C = "C"  # Preference Learning
    D = "D"  # Structured Memory Advantage
    E = "E"  # Event-Centric Temporal Reasoning
    F = "F"  # Workflow Reflection And Reuse
    G = "G"  # Memory Governance
    H = "H"  # Long-Horizon Self Improvement
    I = "I"  # Agent Memory Eval Dataset MVP
    J = "J"  # Retrieval Quality
    M = "M"  # Project Management Business Value


@dataclass
class SetupMemory:
    """A memory candidate to write before executing the benchmark case."""
    memory_type: str
    title: str
    summary: str
    content: dict[str, Any]
    importance: float = 0.5
    confidence: float = 0.8
    evidence: list[dict[str, Any]] | None = None
    tags: list[str] | None = None
    # Timestamps are relative to "now" in the runner
    created_hours_ago: float = 0.0  # 0 = just now, 24 = 1 day ago, etc.
    # Memory scope used for DB storage and recall filtering (must be a valid Scope enum value)
    scope: str = "project"
    # Per-memory ID overrides (None = use runner defaults)
    project_id: str | None = None
    task_id: str | None = None
    user_id: str | None = None
    logical_layer: str | None = None


@dataclass
class SetupEvent:
    """A source event to write before executing the benchmark case."""
    source_type: str
    content: str
    actors: list[str]
    scope: str
    source_ref: str | None = None
    payload: dict[str, Any] | None = None
    created_hours_ago: float = 0.0


@dataclass
class SetupWorkflowOutcome:
    """Explicit outcome feedback to record for a setup workflow skill."""
    skill_title: str
    outcome: str
    summary: str
    evidence: list[dict[str, Any]] | None = None
    project_id: str | None = None
    task_id: str | None = None
    user_id: str | None = None


@dataclass
class RecallSpec:
    """A recall query to execute."""
    query: str
    user_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    scope: str | None = None
    intent: str = "general"
    limit: int = 10
    # Per-recall assertions (evaluated against this recall's results only)
    assertions: list[ResultAssertion] = field(default_factory=list)


@dataclass
class ResultAssertion:
    """An assertion about the expected result of a recall query."""
    type: str  # "contains_title" | "contains_memory_type" | "contains_tag" | "contains_evidence_source_ref" | "evidence_has_fields"
    value: str | list | tuple | None = None
    negates: bool = False  # True = should NOT match


@dataclass
class InterferenceSetup:
    """Noise data to inject before the recall query."""
    memories: list[SetupMemory] = field(default_factory=list)
    events: list[SetupEvent] = field(default_factory=list)
    count: int = 0  # minimum number of noise items to inject (informational)


@dataclass
class BenchmarkCase:
    """A single benchmark test case."""
    case_id: str
    track: str  # "A" / "B" / "C"
    capability: str
    description: str
    direction: str  # "B" / "C" / "B+C"

    # Complexity dimensions (spec.md 9.3.2.4 / VitaBench)
    complexity_reasoning: str  # "low" / "medium" / "high"
    complexity_tool: str  # "low" / "medium" / "high"
    complexity_interaction: str  # "low" / "medium" / "high"

    # Memory types involved (for filtering and analysis)
    memory_types: list[str]

    # Setup: write these before the recall
    setup_events: list[SetupEvent] = field(default_factory=list)
    setup_memories: list[SetupMemory] = field(default_factory=list)
    workflow_outcomes: list[SetupWorkflowOutcome] = field(default_factory=list)

    # Optional interference injection
    interference: InterferenceSetup | None = None

    # The recall queries to execute
    recalls: list[RecallSpec] = field(default_factory=list)

    # Assertions about results
    assertions: list[ResultAssertion] = field(default_factory=list)
    # Assertions about event_entries / event bundles
    event_assertions: list[ResultAssertion] = field(default_factory=list)
    # Run MemoryEngine.review() after setup and before recall/assertions.
    run_review: bool = False

    # Expected: memories that SHOULD be returned
    expected_titles: list[str] = field(default_factory=list)
    # Memories that should NOT appear in results
    forbidden_titles: list[str] = field(default_factory=list)
    # Expected count range (min, max)
    expected_count_range: tuple[int, int] | None = None

    # Special flags
    expect_zero_results: bool = False  # This query should return nothing
    spec_ref: str | None = None  # e.g., "AIT-01", "CUT-02", "PLT-03"
    notes: str | None = None

    # Unified dataset-level metadata (all tracks A-L)
    memory_target: str = ""           # "fact" / "decision" / "preference" / "error" / ...
    setup_turns: list[dict[str, Any]] = field(default_factory=list)
    distractor_turns: list[dict[str, Any]] = field(default_factory=list)
    evaluation_task: str = ""
    expected_behavior: str = ""
    expected_memory_ids: list[str] = field(default_factory=list)
    forbidden_memory_ids: list[str] = field(default_factory=list)
    ground_truth: dict[str, Any] | None = None
    scoring_rubric: dict[str, Any] | None = None
    difficulty: str = "medium"         # "easy" / "medium" / "hard" / "adversarial"
    source_anchor: str = ""           # "benchmark:A-001" or external reference
    baseline_mode: str = "memory_enabled"

    # Track J: retrieval quality dimension
    memory_type_dimension: str = ""  # "fact" / "decision" / "preference" / "procedural" / ...
    recall_intent: str = ""         # "decision_support" / "preference_lookup" / ...

    # Track K: scale benchmark
    scale_level: int | None = None  # 100 / 1000 / 5000 / 10000
    query_set: list[str] = field(default_factory=list)

    # Track L: agent task benchmark
    agent_config: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Helper functions for standard ground_truth and scoring_rubric
# ---------------------------------------------------------------------------

def _standard_ground_truth(
    expected_titles: list[str],
    forbidden_titles: list[str] | None = None,
    expected_count_range: tuple[int, int] | None = None,
    expect_zero_results: bool = False,
    required_content: list[str] | None = None,
) -> dict:
    """Build a standard ground_truth dict for benchmark cases."""
    return {
        "expected_titles": list(expected_titles),
        "forbidden_titles": list(forbidden_titles) if forbidden_titles else [],
        "expected_count_range": list(expected_count_range) if expected_count_range else None,
        "expect_zero_results": bool(expect_zero_results),
        "required_content": list(required_content) if required_content else [],
    }


def _standard_rubric(
    expected_titles: list[str] | None = None,
    forbidden_titles: list[str] | None = None,
    bounded_context: bool = True,
    answer_required: bool = True,
    retrieval_only: bool = False,
) -> dict:
    """Build a standard scoring_rubric dict for benchmark cases."""
    criteria: list[dict] = []

    if expected_titles:
        criteria.append({
            "name": "recall_required_memory",
            "weight": 0.35,
            "description": "Selected context includes every expected memory title.",
        })

    if forbidden_titles:
        criteria.append({
            "name": "exclude_wrong_or_stale_memory",
            "weight": 0.25,
            "description": "Selected context excludes stale or forbidden memories.",
        })

    if bounded_context:
        criteria.append({
            "name": "bounded_context",
            "weight": 0.15,
            "description": "Recall returns only the expected bounded amount of context.",
        })

    if answer_required:
        criteria.append({
            "name": "answer_uses_memory",
            "weight": 0.25,
            "description": "Answer-level evaluation is faithful and relevant.",
        })

    if retrieval_only:
        criteria.append({
            "name": "retrieval_precision",
            "weight": 0.40,
            "description": "Recall@K / NDCG@10 / MRR metrics.",
        })

    if not criteria:
        criteria.append({
            "name": "refusal_correctness",
            "weight": 1.0,
            "description": "No unsupported memory is returned for an unknown request.",
        })

    return {
        "score_type": "weighted_diagnostics",
        "pass_threshold": 1.0,
        "criteria": criteria,
    }


# ---------------------------------------------------------------------------
# Failure case structure (aligned with spec.md 9.5 failure categories)
# ---------------------------------------------------------------------------

class FailureType(str, Enum):
    REASONING = "reasoning"          # spec.md 9.5: recalled but not used correctly
    RETRIEVAL = "retrieval"          # spec.md 9.5: memory exists but not recalled
    MAINTENANCE = "maintenance"      # spec.md 9.5: stale memory used as fresh
    INTERACTION = "interaction"       # spec.md 9.5: missing context in handover
    TOOLING = "tooling"              # spec.md 9.5: external API failure


@dataclass
class FailureCase:
    """A structured failure case for analysis and regression testing."""
    case_id: str
    failure_type: str  # reasoning / retrieval / maintenance / interaction / tooling

    # What happened
    original_query: str
    # What the system did
    system_response: str
    # What should have happened
    expected_behavior: str
    # Evidence gaps that explain the failure
    evidence_gaps: list[str]

    # Memory context
    memory_ids_involved: list[int]
    memory_titles_involved: list[str]

    # Corrected version
    corrected_query: str | None = None
    corrected_response: str | None = None

    # Traceability
    spec_ref: str | None = None
    discovered_date: str | None = None
    fixed_date: str | None = None
    notes: str | None = None
