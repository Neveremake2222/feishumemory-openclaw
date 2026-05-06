"""Track H (Long-Horizon Self Improvement) benchmark cases."""

from __future__ import annotations

from benchmarks.structures import (
    BenchmarkCase,
    Complexity,
    Direction,
    ResultAssertion,
    SetupMemory,
    SetupWorkflowOutcome,
    Track,
)


def _workflow_skill(title: str, summary: str, evidence_ref: str) -> SetupMemory:
    return SetupMemory(
        memory_type="procedural",
        title=title,
        summary=summary,
        content={
            "scope": "project",
            "kind": "workflow_skill",
            "task_type": "test_verification_workflow",
            "confirmed": "true",
            "needs_confirmation": "false",
        },
        importance=0.75,
        confidence=0.9,
        evidence=[{"source_ref": evidence_ref}],
        tags=["workflow", "workflow_skill", "test_verification_workflow"],
        scope="project",
        logical_layer="L2",
    )


def _review_workflow_skill(title: str, summary: str, evidence_ref: str) -> SetupMemory:
    memory = _workflow_skill(title, summary, evidence_ref)
    memory.content.update(
        {
            "needs_review": "true",
            "review_reason": "workflow skill negative outcome evidence observed",
            "usage_count": "2",
            "adoption_success_count": "0",
            "adoption_failure_count": "2",
            "effectiveness_score": "0.0",
        }
    )
    memory.confidence = 0.55
    return memory


def _workflow_ground_truth(status: str, failed: int, succeeded: int, active: int, retired_or_review: int) -> dict:
    return {
        "expected_self_improvement_status": status,
        "expected_active_skill_count": active,
        "expected_retired_or_review_skill_count": retired_or_review,
        "expected_event_relation_counts": {
            "workflow_skill_failed": failed,
            "workflow_skill_succeeded": succeeded,
        },
    }


def _workflow_rubric() -> dict:
    return {
        "score_type": "weighted_diagnostics",
        "pass_threshold": 1.0,
        "criteria": [
            {
                "name": "self_improvement_decision",
                "weight": 0.35,
                "description": "Workflow self-improvement status matches improved/not_improved/insufficient_evidence expectation.",
            },
            {
                "name": "event_trace_counts",
                "weight": 0.25,
                "description": "Workflow success/failure event relation counts match the expected long-window evidence.",
            },
            {
                "name": "lifecycle_state",
                "weight": 0.25,
                "description": "Skill lifecycle state such as archived, active, needs_review, or rejection reason is correct.",
            },
            {
                "name": "answer_level_behavior",
                "weight": 0.15,
                "description": "Answer-level evaluation remains faithful, relevant, and improved by memory.",
            },
        ],
    }


H01_WORKFLOW_SKILL_REPLACEMENT_IMPROVES_OUTCOMES = BenchmarkCase(
    case_id="H-01",
    track=Track.H.value,
    capability="workflow_skill_replacement_improves_outcomes",
    description=(
        "A brittle workflow skill should accumulate failures and be archived, while a replacement skill "
        "accumulates successful outcomes and is evaluated as an improvement."
    ),
    direction=Direction.B_PLUS_C.value,
    complexity_reasoning=Complexity.HIGH,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.MEDIUM,
    memory_types=["procedural"],
    setup_memories=[
        _workflow_skill(
            "Workflow skill: shortcut_validation_workflow",
            "Brittle validation shortcut that relies on assumed green checks.",
            "bench://self-improve-brittle",
        ),
        _workflow_skill(
            "Workflow skill: robust_ci_validation_workflow",
            "Robust validation workflow that runs focused tests and inspects diagnostics before completion.",
            "bench://self-improve-improved",
        ),
    ],
    workflow_outcomes=[
        SetupWorkflowOutcome(
            "Workflow skill: shortcut_validation_workflow",
            "failure",
            "Brittle workflow failed because it skipped focused verification.",
            evidence=[{"source_ref": "bench://self-improve-brittle-failure-1"}],
        ),
        SetupWorkflowOutcome(
            "Workflow skill: shortcut_validation_workflow",
            "failure",
            "Brittle workflow failed again after using stale assumptions.",
            evidence=[{"source_ref": "bench://self-improve-brittle-failure-2"}],
        ),
        SetupWorkflowOutcome(
            "Workflow skill: shortcut_validation_workflow",
            "failure",
            "Brittle workflow failed a third time and should be archived.",
            evidence=[{"source_ref": "bench://self-improve-brittle-failure-3"}],
        ),
        SetupWorkflowOutcome(
            "Workflow skill: robust_ci_validation_workflow",
            "success",
            "Improved workflow ran focused tests and inspected output.",
            evidence=[{"source_ref": "bench://self-improve-improved-success-1"}],
        ),
        SetupWorkflowOutcome(
            "Workflow skill: robust_ci_validation_workflow",
            "success",
            "Improved workflow reused the verification checklist successfully.",
            evidence=[{"source_ref": "bench://self-improve-improved-success-2"}],
        ),
    ],
    event_assertions=[
        ResultAssertion(
            "workflow_self_improvement_status",
            ["test_verification_workflow", "improved", 1, 1],
        ),
        ResultAssertion("memory_content_field_equals_any_status", ["workflow_skill", "archived_by_policy", "true"]),
        ResultAssertion("event_entry_relation_count", ["workflow_skill_failed", 3]),
        ResultAssertion("event_entry_relation_count", ["workflow_skill_succeeded", 2]),
    ],
    spec_ref="Track-H-01",
    ground_truth=_workflow_ground_truth("improved", failed=3, succeeded=2, active=1, retired_or_review=1),
    scoring_rubric=_workflow_rubric(),
)


H02_WORKFLOW_SKILL_REPLACEMENT_NOT_IMPROVED = BenchmarkCase(
    case_id="H-02",
    track=Track.H.value,
    capability="workflow_skill_replacement_not_improved",
    description=(
        "A replacement workflow skill should not be labeled improved when its outcome score does not beat "
        "the retired/reviewed skill."
    ),
    direction=Direction.B_PLUS_C.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.MEDIUM,
    memory_types=["procedural"],
    setup_memories=[
        _workflow_skill(
            "Workflow skill: abandoned_shortcut_workflow",
            "Abandoned shortcut workflow that repeatedly failed.",
            "bench://self-improve-not-better-old",
        ),
        _workflow_skill(
            "Workflow skill: unproven_replacement_workflow",
            "Replacement workflow that has not yet produced better outcomes.",
            "bench://self-improve-not-better-new",
        ),
    ],
    workflow_outcomes=[
        SetupWorkflowOutcome(
            "Workflow skill: abandoned_shortcut_workflow",
            "failure",
            "Old shortcut failed first cycle.",
            evidence=[{"source_ref": "bench://self-improve-not-better-old-failure-1"}],
        ),
        SetupWorkflowOutcome(
            "Workflow skill: abandoned_shortcut_workflow",
            "failure",
            "Old shortcut failed second cycle.",
            evidence=[{"source_ref": "bench://self-improve-not-better-old-failure-2"}],
        ),
        SetupWorkflowOutcome(
            "Workflow skill: abandoned_shortcut_workflow",
            "failure",
            "Old shortcut failed third cycle and was archived.",
            evidence=[{"source_ref": "bench://self-improve-not-better-old-failure-3"}],
        ),
        SetupWorkflowOutcome(
            "Workflow skill: unproven_replacement_workflow",
            "failure",
            "Replacement workflow also failed its first observed cycle.",
            evidence=[{"source_ref": "bench://self-improve-not-better-new-failure-1"}],
        ),
    ],
    event_assertions=[
        ResultAssertion(
            "workflow_self_improvement_status",
            ["test_verification_workflow", "not_improved", 1, 1],
        ),
        ResultAssertion("event_entry_relation_count", ["workflow_skill_failed", 4]),
    ],
    spec_ref="Track-H-02",
)


H03_WORKFLOW_SELF_IMPROVEMENT_INSUFFICIENT_EVIDENCE = BenchmarkCase(
    case_id="H-03",
    track=Track.H.value,
    capability="workflow_self_improvement_insufficient_evidence",
    description="A single active workflow skill without a retired/reviewed comparator should remain insufficient evidence.",
    direction=Direction.B_PLUS_C.value,
    complexity_reasoning=Complexity.LOW,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["procedural"],
    setup_memories=[
        _workflow_skill(
            "Workflow skill: solo_successful_workflow",
            "A single active workflow skill with success evidence but no replacement history.",
            "bench://self-improve-insufficient-solo",
        ),
    ],
    workflow_outcomes=[
        SetupWorkflowOutcome(
            "Workflow skill: solo_successful_workflow",
            "success",
            "Solo workflow succeeded, but there is no retired comparator.",
            evidence=[{"source_ref": "bench://self-improve-insufficient-success-1"}],
        ),
    ],
    event_assertions=[
        ResultAssertion(
            "workflow_self_improvement_status",
            ["test_verification_workflow", "insufficient_evidence", 1, 0],
        ),
        ResultAssertion("event_entry_relation_count", ["workflow_skill_succeeded", 1]),
    ],
    spec_ref="Track-H-03",
)


H04_WORKFLOW_SKILL_RECONFIRM_REVIEW = BenchmarkCase(
    case_id="H-04",
    track=Track.H.value,
    capability="workflow_skill_reconfirm_review",
    description="A review-needed workflow skill can be explicitly re-confirmed and kept active.",
    direction=Direction.B_PLUS_C.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.MEDIUM,
    memory_types=["procedural"],
    setup_memories=[
        _review_workflow_skill(
            "Workflow skill: review_reconfirm_workflow",
            "Review-needed workflow skill that the user explicitly keeps.",
            "bench://self-improve-reconfirm-review",
        ),
    ],
    event_assertions=[
        ResultAssertion(
            "workflow_skill_review_action_creates_event_entry",
            ["Workflow skill: review_reconfirm_workflow", "reconfirm", "reconfirmed_workflow_skill"],
        ),
        ResultAssertion("memory_content_field_equals", ["workflow_skill", "needs_review", "false"]),
    ],
    spec_ref="Track-H-04",
)


H05_WORKFLOW_SKILL_REJECT_REVIEW = BenchmarkCase(
    case_id="H-05",
    track=Track.H.value,
    capability="workflow_skill_reject_review",
    description="A review-needed workflow skill can be explicitly rejected and archived.",
    direction=Direction.B_PLUS_C.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.MEDIUM,
    memory_types=["procedural"],
    setup_memories=[
        _review_workflow_skill(
            "Workflow skill: review_reject_workflow",
            "Review-needed workflow skill that the user rejects.",
            "bench://self-improve-reject-review",
        ),
    ],
    event_assertions=[
        ResultAssertion(
            "workflow_skill_review_action_creates_event_entry",
            ["Workflow skill: review_reject_workflow", "reject", "rejected_workflow_skill"],
        ),
        ResultAssertion("memory_content_field_equals_any_status", ["workflow_skill", "rejection_reason", "user rejected workflow skill during review"]),
    ],
    spec_ref="Track-H-05",
)


H06_LONG_WINDOW_REPLACEMENT_IMPROVES = BenchmarkCase(
    case_id="H-06",
    track=Track.H.value,
    capability="long_window_replacement_improves",
    description="A longer outcome window should show the replacement workflow outperforming the retired shortcut.",
    direction=Direction.B_PLUS_C.value,
    complexity_reasoning=Complexity.HIGH,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.MEDIUM,
    memory_types=["procedural"],
    setup_memories=[
        _workflow_skill(
            "Workflow skill: thirty_day_flaky_release_shortcut",
            "Old release shortcut that skipped rollback verification over a long window.",
            "bench://long-window-replacement-old",
        ),
        _workflow_skill(
            "Workflow skill: thirty_day_release_verification_loop",
            "Replacement release workflow that verifies rollback, smoke tests, and release notes.",
            "bench://long-window-replacement-new",
        ),
    ],
    workflow_outcomes=[
        SetupWorkflowOutcome(
            "Workflow skill: thirty_day_flaky_release_shortcut",
            "failure",
            "Day 3 failed because rollback verification was skipped.",
            evidence=[{"source_ref": "bench://long-window-old-failure-1"}],
        ),
        SetupWorkflowOutcome(
            "Workflow skill: thirty_day_flaky_release_shortcut",
            "failure",
            "Day 11 failed because smoke tests were not rerun after config changes.",
            evidence=[{"source_ref": "bench://long-window-old-failure-2"}],
        ),
        SetupWorkflowOutcome(
            "Workflow skill: thirty_day_flaky_release_shortcut",
            "failure",
            "Day 21 failed because release notes missed a breaking change.",
            evidence=[{"source_ref": "bench://long-window-old-failure-3"}],
        ),
        SetupWorkflowOutcome(
            "Workflow skill: thirty_day_release_verification_loop",
            "success",
            "Day 24 replacement workflow verified rollback and smoke tests.",
            evidence=[{"source_ref": "bench://long-window-new-success-1"}],
        ),
        SetupWorkflowOutcome(
            "Workflow skill: thirty_day_release_verification_loop",
            "success",
            "Day 28 replacement workflow caught a release-note gap before completion.",
            evidence=[{"source_ref": "bench://long-window-new-success-2"}],
        ),
    ],
    event_assertions=[
        ResultAssertion(
            "workflow_self_improvement_status",
            ["test_verification_workflow", "improved", 1, 1],
        ),
        ResultAssertion("event_entry_relation_count", ["workflow_skill_failed", 3]),
        ResultAssertion("event_entry_relation_count", ["workflow_skill_succeeded", 2]),
    ],
    spec_ref="Track-H-06",
    ground_truth=_workflow_ground_truth("improved", failed=3, succeeded=2, active=1, retired_or_review=1),
    scoring_rubric=_workflow_rubric(),
)


H07_LONG_WINDOW_ROLLBACK_REJECTS_BAD_SKILL = BenchmarkCase(
    case_id="H-07",
    track=Track.H.value,
    capability="long_window_rollback",
    description="A long-horizon review window should support rolling back a repeatedly bad workflow skill.",
    direction=Direction.B_PLUS_C.value,
    complexity_reasoning=Complexity.HIGH,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.MEDIUM,
    memory_types=["procedural"],
    setup_memories=[
        _review_workflow_skill(
            "Workflow skill: rollback_bad_release_window",
            "Review-needed workflow skill with repeated long-window release failures.",
            "bench://long-window-rollback",
        ),
    ],
    event_assertions=[
        ResultAssertion(
            "workflow_skill_review_action_creates_event_entry",
            ["Workflow skill: rollback_bad_release_window", "reject", "rejected_workflow_skill"],
        ),
        ResultAssertion("memory_content_field_equals_any_status", ["workflow_skill", "rejection_reason", "user rejected workflow skill during review"]),
        ResultAssertion("memory_content_kind_status_count", ["workflow_skill", "archived", 1]),
    ],
    spec_ref="Track-H-07",
    ground_truth={
        "expected_review_action": "reject",
        "expected_event_relation": "rejected_workflow_skill",
        "expected_status": "archived",
        "expected_rejection_reason": "user rejected workflow skill during review",
    },
    scoring_rubric=_workflow_rubric(),
)


H08_LONG_WINDOW_INSUFFICIENT_EVIDENCE = BenchmarkCase(
    case_id="H-08",
    track=Track.H.value,
    capability="long_window_insufficient_evidence",
    description="Mixed outcomes without a retired comparator should remain insufficient evidence.",
    direction=Direction.B_PLUS_C.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.MEDIUM,
    memory_types=["procedural"],
    setup_memories=[
        _workflow_skill(
            "Workflow skill: mixed_signal_release_window",
            "Active workflow skill with mixed long-window outcomes but no retired comparator.",
            "bench://long-window-insufficient",
        ),
    ],
    workflow_outcomes=[
        SetupWorkflowOutcome(
            "Workflow skill: mixed_signal_release_window",
            "success",
            "Day 7 succeeded after full release validation.",
            evidence=[{"source_ref": "bench://long-window-mixed-success"}],
        ),
        SetupWorkflowOutcome(
            "Workflow skill: mixed_signal_release_window",
            "failure",
            "Day 19 failed due to missing rollback evidence.",
            evidence=[{"source_ref": "bench://long-window-mixed-failure"}],
        ),
    ],
    event_assertions=[
        ResultAssertion(
            "workflow_self_improvement_status",
            ["test_verification_workflow", "insufficient_evidence", 1, 0],
        ),
        ResultAssertion("event_entry_relation_count", ["workflow_skill_succeeded", 1]),
        ResultAssertion("event_entry_relation_count", ["workflow_skill_failed", 1]),
    ],
    spec_ref="Track-H-08",
    ground_truth=_workflow_ground_truth("insufficient_evidence", failed=1, succeeded=1, active=1, retired_or_review=0),
    scoring_rubric=_workflow_rubric(),
)


TRACK_H_CASES = [
    H01_WORKFLOW_SKILL_REPLACEMENT_IMPROVES_OUTCOMES,
    H02_WORKFLOW_SKILL_REPLACEMENT_NOT_IMPROVED,
    H03_WORKFLOW_SELF_IMPROVEMENT_INSUFFICIENT_EVIDENCE,
    H04_WORKFLOW_SKILL_RECONFIRM_REVIEW,
    H05_WORKFLOW_SKILL_REJECT_REVIEW,
    H06_LONG_WINDOW_REPLACEMENT_IMPROVES,
    H07_LONG_WINDOW_ROLLBACK_REJECTS_BAD_SKILL,
    H08_LONG_WINDOW_INSUFFICIENT_EVIDENCE,
]
