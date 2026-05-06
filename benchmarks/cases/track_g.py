"""Track G (Memory Governance) benchmark cases."""

from __future__ import annotations

from benchmarks.structures import (
    BenchmarkCase,
    Complexity,
    Direction,
    ResultAssertion,
    SetupMemory,
    Track,
)


G01_L1_L2_GOVERNANCE_APPROVES = BenchmarkCase(
    case_id="G-01",
    track=Track.G.value,
    capability="l1_l2_governance_approval",
    description="A persistent L1 preference should pass deterministic governance and promote to L2.",
    direction=Direction.C.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["preference"],
    setup_memories=[
        SetupMemory(
            memory_type="preference",
            title="Governed stable preference",
            summary="User prefers concise implementation updates.",
            content={"scope": "project", "preference_kind": "communication"},
            importance=0.7,
            confidence=0.82,
            evidence=[{"source_ref": "bench://governance-l2-approve"}],
            tags=["governance", "preference"],
            scope="project",
            created_hours_ago=24 * 8,
        )
    ],
    run_review=True,
    event_assertions=[
        ResultAssertion("memory_title_logical_layer_equals", ["Governed stable preference", "L2"]),
        ResultAssertion("memory_vote_count", ["Governed stable preference", 3]),
        ResultAssertion("memory_vote_assembly", ["Governed stable preference", "deterministic_citizen_assembly", 1]),
        ResultAssertion("memory_reviewer_vote", ["Governed stable preference", "EvidenceReviewer", "approve"]),
        ResultAssertion("memory_reviewer_vote", ["Governed stable preference", "PrivacyReviewer", "approve"]),
        ResultAssertion("memory_reviewer_vote", ["Governed stable preference", "ScopeReviewer", "approve"]),
    ],
    spec_ref="Track-G-01",
)


G02_L1_L2_GOVERNANCE_REJECTS_PRIVACY_RISK = BenchmarkCase(
    case_id="G-02",
    track=Track.G.value,
    capability="l1_l2_governance_rejection",
    description="A persistent L1 memory with a secret marker should be rolled back by governance.",
    direction=Direction.C.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["preference"],
    setup_memories=[
        SetupMemory(
            memory_type="preference",
            title="Governed risky preference",
            summary="Do not promote [api_key:REDACTED] details.",
            content={"scope": "project", "preference_kind": "security"},
            importance=0.7,
            confidence=0.82,
            evidence=[{"source_ref": "bench://governance-l2-reject"}],
            tags=["governance", "preference"],
            scope="project",
            created_hours_ago=24 * 8,
        )
    ],
    run_review=True,
    event_assertions=[
        ResultAssertion("memory_title_logical_layer_equals", ["Governed risky preference", "L1"]),
        ResultAssertion("memory_title_change_reason_contains", ["Governed risky preference", "governance rejected promotion"]),
        ResultAssertion("memory_vote_count", ["Governed risky preference", 3]),
        ResultAssertion("memory_vote_assembly", ["Governed risky preference", "deterministic_citizen_assembly", 1]),
        ResultAssertion("memory_reviewer_vote", ["Governed risky preference", "PrivacyReviewer", "reject"]),
    ],
    spec_ref="Track-G-02",
)


G03_L2_L3_GOVERNANCE_APPROVES = BenchmarkCase(
    case_id="G-03",
    track=Track.G.value,
    capability="l2_l3_governance_approval",
    description="An L2 workflow status memory should pass L3 governance when utility and evidence are sufficient.",
    direction=Direction.B.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["task_status"],
    setup_memories=[
        SetupMemory(
            memory_type="task_status",
            title="Governed workflow SOP",
            summary="Workflow step SOP for release verification.",
            content={"scope": "project", "task_type": "release_workflow"},
            importance=0.78,
            confidence=0.82,
            evidence=[{"source_ref": "bench://governance-l3-approve"}],
            tags=["governance", "workflow"],
            scope="project",
            logical_layer="L2",
        )
    ],
    run_review=True,
    event_assertions=[
        ResultAssertion("memory_title_logical_layer_equals", ["Governed workflow SOP", "L3"]),
        ResultAssertion("memory_vote_count", ["Governed workflow SOP", 5]),
        ResultAssertion("memory_vote_assembly", ["Governed workflow SOP", "deterministic_citizen_assembly", 1]),
        ResultAssertion("memory_reviewer_vote", ["Governed workflow SOP", "UtilityReviewer", "approve"]),
        ResultAssertion("memory_reviewer_vote", ["Governed workflow SOP", "ConflictReviewer", "approve"]),
    ],
    spec_ref="Track-G-03",
)


G04_L2_L3_GOVERNANCE_REJECTS_PRIVACY_RISK = BenchmarkCase(
    case_id="G-04",
    track=Track.G.value,
    capability="l2_l3_governance_rejection",
    description="An L2 workflow status memory with a secret marker should be rolled back by L3 governance.",
    direction=Direction.B.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["task_status"],
    setup_memories=[
        SetupMemory(
            memory_type="task_status",
            title="Governed risky workflow SOP",
            summary="Workflow step SOP includes [api_key:REDACTED] release details.",
            content={"scope": "project", "task_type": "risky_release_workflow"},
            importance=0.78,
            confidence=0.82,
            evidence=[{"source_ref": "bench://governance-l3-reject"}],
            tags=["governance", "workflow"],
            scope="project",
            logical_layer="L2",
        )
    ],
    run_review=True,
    event_assertions=[
        ResultAssertion("memory_title_logical_layer_equals", ["Governed risky workflow SOP", "L2"]),
        ResultAssertion("memory_title_change_reason_contains", ["Governed risky workflow SOP", "governance rejected promotion"]),
        ResultAssertion("memory_vote_count", ["Governed risky workflow SOP", 5]),
        ResultAssertion("memory_vote_assembly", ["Governed risky workflow SOP", "deterministic_citizen_assembly", 1]),
        ResultAssertion("memory_reviewer_vote", ["Governed risky workflow SOP", "PrivacyReviewer", "reject"]),
    ],
    spec_ref="Track-G-04",
)


TRACK_G_CASES = [
    G01_L1_L2_GOVERNANCE_APPROVES,
    G02_L1_L2_GOVERNANCE_REJECTS_PRIVACY_RISK,
    G03_L2_L3_GOVERNANCE_APPROVES,
    G04_L2_L3_GOVERNANCE_REJECTS_PRIVACY_RISK,
]
