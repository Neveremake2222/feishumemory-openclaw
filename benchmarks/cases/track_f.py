"""Track F (Workflow Reflection And Reuse) benchmark cases."""

from __future__ import annotations

from benchmarks.structures import (
    BenchmarkCase,
    Complexity,
    Direction,
    RecallSpec,
    ResultAssertion,
    SetupMemory,
    SetupWorkflowOutcome,
    Track,
)


def _workflow_memory(
    *,
    kind: str,
    task_type: str,
    title: str,
    summary: str,
    evidence_ref: str,
    outcome: str,
    steps: list[str] | None = None,
    root_cause: str | None = None,
) -> SetupMemory:
    content = {
        "scope": "project",
        "kind": kind,
        "task_type": task_type,
        "trigger": "code change requires verification",
        "steps": steps or [],
        "outcome": outcome,
    }
    if root_cause:
        content["root_cause"] = root_cause
    return SetupMemory(
        memory_type="procedural",
        title=title,
        summary=summary,
        content=content,
        importance=0.65,
        confidence=0.72,
        evidence=[{"source_ref": evidence_ref}],
        tags=["workflow", kind, task_type],
        scope="project",
    )


F01_SUCCESS_CASES_SYNTHESIZE_STRATEGY = BenchmarkCase(
    case_id="F-01",
    track=Track.F,
    capability="workflow_strategy_synthesis",
    description="Repeated successful workflow cases should synthesize a workflow strategy candidate during review.",
    direction=Direction.B_PLUS_C,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["procedural"],
    setup_memories=[
        _workflow_memory(
            kind="workflow_success_case",
            task_type="test_verification_workflow",
            title="Workflow success: focused pytest after engine change",
            summary="Focused pytest passed after a memory_engine change.",
            evidence_ref="bench://workflow-success-1",
            outcome="focused pytest passed",
            steps=["run focused pytest", "inspect failures before claiming done"],
        ),
        _workflow_memory(
            kind="workflow_success_case",
            task_type="test_verification_workflow",
            title="Workflow success: adapter tests after hook change",
            summary="Adapter tests passed after a write hook change.",
            evidence_ref="bench://workflow-success-2",
            outcome="adapter tests passed",
            steps=["run focused pytest", "inspect failures before claiming done"],
        ),
    ],
    run_review=True,
    recalls=[
        RecallSpec(
            query="workflow strategy test verification",
            project_id="proj-alpha",
            intent="workflow",
            assertions=[
                ResultAssertion("contains_title", "Workflow strategy candidate: test_verification_workflow"),
                ResultAssertion("contains_memory_type", "procedural"),
            ],
        ),
    ],
    event_assertions=[
        ResultAssertion("memory_content_kind_count", ["workflow_strategy_candidate", 1]),
        ResultAssertion("event_entry_relation_exists", "synthesized_workflow_strategy"),
    ],
    spec_ref="Track-F-01",
)


F02_FAILURE_CASE_IS_RECALLABLE = BenchmarkCase(
    case_id="F-02",
    track=Track.F,
    capability="workflow_failure_case_recall",
    description="A workflow failure case should be recallable with its root-cause evidence.",
    direction=Direction.B,
    complexity_reasoning=Complexity.LOW,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["procedural"],
    setup_memories=[
        _workflow_memory(
            kind="workflow_failure_case",
            task_type="test_verification_workflow",
            title="Workflow failure: missing fixture path",
            summary="Pytest failed because the fixture path was missing.",
            evidence_ref="bench://workflow-failure-1",
            outcome="pytest failed",
            root_cause="fixture path was missing",
        ),
    ],
    recalls=[
        RecallSpec(
            query="fixture path missing pytest failure",
            project_id="proj-alpha",
            assertions=[
                ResultAssertion("contains_title", "Workflow failure: missing fixture path"),
                ResultAssertion("contains_tag", "workflow_failure_case"),
            ],
        ),
    ],
    event_assertions=[
        ResultAssertion("event_entry_relation_exists", "recorded_workflow_failure"),
    ],
    spec_ref="Track-F-02",
)


F03_GENERAL_RECALL_HIDES_STRATEGY_CANDIDATE = BenchmarkCase(
    case_id="F-03",
    track=Track.F,
    capability="workflow_candidate_recall_gating",
    description="Draft workflow strategy candidates should not pollute ordinary recall.",
    direction=Direction.B_PLUS_C,
    complexity_reasoning=Complexity.LOW,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["procedural"],
    setup_memories=[
        _workflow_memory(
            kind="workflow_success_case",
            task_type="test_verification_workflow",
            title="Workflow success: focused pytest after model change",
            summary="Focused pytest passed after model changes.",
            evidence_ref="bench://workflow-gating-1",
            outcome="focused pytest passed",
        ),
        _workflow_memory(
            kind="workflow_success_case",
            task_type="test_verification_workflow",
            title="Workflow success: full engine tests",
            summary="Full memory engine tests passed after workflow changes.",
            evidence_ref="bench://workflow-gating-2",
            outcome="engine tests passed",
        ),
    ],
    run_review=True,
    recalls=[
        RecallSpec(
            query="workflow strategy test verification",
            project_id="proj-alpha",
            assertions=[
                ResultAssertion("contains_title", "Workflow strategy candidate", negates=True),
            ],
        ),
    ],
    event_assertions=[
        ResultAssertion("memory_content_kind_count", ["workflow_strategy_candidate", 1]),
    ],
    spec_ref="Track-F-03",
)


F04_WORKFLOW_SKILL_IS_RECALLABLE = BenchmarkCase(
    case_id="F-04",
    track=Track.F,
    capability="workflow_skill_reuse",
    description="A confirmed workflow skill should be visible in ordinary recall as reusable procedural memory.",
    direction=Direction.B_PLUS_C,
    complexity_reasoning=Complexity.LOW,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["procedural"],
    setup_memories=[
        SetupMemory(
            memory_type="procedural",
            title="Workflow skill: test_verification_workflow",
            summary="Reusable workflow skill for test verification: run focused pytest before claiming completion.",
            content={
                "scope": "project",
                "kind": "workflow_skill",
                "task_type": "test_verification_workflow",
                "success_evidence_count": "2",
                "failure_evidence_count": "0",
                "recommended_steps": ["run focused pytest", "inspect failures before claiming done"],
                "known_limits": [],
                "confirmed": "true",
                "needs_confirmation": "false",
            },
            importance=0.75,
            confidence=0.82,
            evidence=[{"source_ref": "bench://workflow-skill-1"}],
            tags=["workflow", "workflow_skill", "test_verification_workflow"],
            scope="project",
        ),
    ],
    recalls=[
        RecallSpec(
            query="workflow skill focused pytest before claiming completion",
            project_id="proj-alpha",
            assertions=[
                ResultAssertion("contains_title", "Workflow skill: test_verification_workflow"),
                ResultAssertion("contains_tag", "workflow_skill"),
            ],
        ),
    ],
    event_assertions=[
        ResultAssertion("event_entry_relation_exists", "recorded_workflow_skill"),
    ],
    spec_ref="Track-F-04",
)


F05_WORKFLOW_SKILL_FAILURES_TRIGGER_REVIEW = BenchmarkCase(
    case_id="F-05",
    track=Track.F,
    capability="workflow_skill_effectiveness",
    description="Repeated failed outcomes should lower workflow skill effectiveness and mark it for review.",
    direction=Direction.B_PLUS_C,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["procedural"],
    setup_memories=[
        SetupMemory(
            memory_type="procedural",
            title="Workflow skill: fragile_test_verification_workflow",
            summary="Reusable workflow skill for fragile test verification.",
            content={
                "scope": "project",
                "kind": "workflow_skill",
                "task_type": "fragile_test_verification_workflow",
                "recommended_steps": ["run focused pytest"],
                "known_limits": [],
                "confirmed": "true",
                "needs_confirmation": "false",
            },
            importance=0.75,
            confidence=0.9,
            evidence=[{"source_ref": "bench://workflow-skill-fragile"}],
            tags=["workflow", "workflow_skill", "fragile_test_verification_workflow"],
            scope="project",
        ),
    ],
    workflow_outcomes=[
        SetupWorkflowOutcome(
            skill_title="Workflow skill: fragile_test_verification_workflow",
            outcome="failure",
            summary="The workflow skill was used, but focused pytest failed.",
            evidence=[{"source_ref": "bench://workflow-skill-failure-1"}],
        ),
        SetupWorkflowOutcome(
            skill_title="Workflow skill: fragile_test_verification_workflow",
            outcome="failure",
            summary="The workflow skill was used again, but verification failed again.",
            evidence=[{"source_ref": "bench://workflow-skill-failure-2"}],
        ),
    ],
    event_assertions=[
        ResultAssertion("memory_content_kind_count", ["workflow_skill_outcome", 2]),
        ResultAssertion("event_entry_relation_count", ["workflow_skill_failed", 2]),
        ResultAssertion("memory_content_field_equals", ["workflow_skill", "needs_review", "true"]),
        ResultAssertion("memory_content_field_equals", ["workflow_skill", "effectiveness_score", "0.0"]),
    ],
    spec_ref="Track-F-05",
)


F06_WORKFLOW_SKILL_REPEATED_FAILURES_ARCHIVE = BenchmarkCase(
    case_id="F-06",
    track=Track.F.value,
    capability="workflow_skill_lifecycle",
    description="Repeated negative outcomes archive a workflow skill so it stops participating in active recall.",
    direction=Direction.B.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["procedural"],
    setup_memories=[
        SetupMemory(
            memory_type="procedural",
            title="Workflow skill: brittle_test_verification_workflow",
            summary="Reusable workflow skill for brittle test verification.",
            content={
                "scope": "project",
                "kind": "workflow_skill",
                "task_type": "brittle_test_verification_workflow",
                "recommended_steps": ["run focused pytest"],
                "known_limits": [],
                "confirmed": "true",
                "needs_confirmation": "false",
            },
            importance=0.75,
            confidence=0.9,
            evidence=[{"source_ref": "bench://workflow-skill-brittle"}],
            tags=["workflow", "workflow_skill", "brittle_test_verification_workflow"],
            scope="project",
        ),
    ],
    workflow_outcomes=[
        SetupWorkflowOutcome(
            skill_title="Workflow skill: brittle_test_verification_workflow",
            outcome="failure",
            summary="The workflow skill failed its first verification run.",
            evidence=[{"source_ref": "bench://workflow-skill-archive-failure-1"}],
        ),
        SetupWorkflowOutcome(
            skill_title="Workflow skill: brittle_test_verification_workflow",
            outcome="failure",
            summary="The workflow skill failed its second verification run.",
            evidence=[{"source_ref": "bench://workflow-skill-archive-failure-2"}],
        ),
        SetupWorkflowOutcome(
            skill_title="Workflow skill: brittle_test_verification_workflow",
            outcome="failure",
            summary="The workflow skill failed its third verification run.",
            evidence=[{"source_ref": "bench://workflow-skill-archive-failure-3"}],
        ),
    ],
    recalls=[
        RecallSpec(
            query="brittle test verification workflow",
            project_id="p_bench",
            intent="workflow",
            assertions=[
                ResultAssertion("contains_title", "Workflow skill: brittle_test_verification_workflow", negates=True),
            ],
        )
    ],
    event_assertions=[
        ResultAssertion("memory_content_kind_count", ["workflow_skill_outcome", 3]),
        ResultAssertion("event_entry_relation_count", ["workflow_skill_failed", 3]),
        ResultAssertion("memory_content_kind_status_count", ["workflow_skill", "archived", 1]),
        ResultAssertion("memory_content_field_equals_any_status", ["workflow_skill", "archived_by_policy", "true"]),
    ],
    spec_ref="Track-F-06",
)


F07_WORKFLOW_TRACE_RECORDS_STEPS = BenchmarkCase(
    case_id="F-07",
    track=Track.F.value,
    capability="workflow_trace_capture",
    description="A workflow trace stores ordered execution steps and creates a trace event entry.",
    direction=Direction.B.value,
    complexity_reasoning=Complexity.LOW,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["procedural"],
    setup_memories=[
        SetupMemory(
            memory_type="procedural",
            title="Workflow trace: test_verification_workflow",
            summary="Step trace for test verification: focused pytest passed.",
            content={
                "scope": "project",
                "kind": "workflow_trace",
                "task_type": "test_verification_workflow",
                "trigger": "run focused tests",
                "outcome": "success",
                "summary": "focused pytest passed",
                "step_count": "3",
                "steps": [
                    {"index": "1", "phase": "request", "name": "user_request", "status": "observed", "summary": "run focused tests"},
                    {"index": "2", "phase": "tool_call", "name": "pytest", "status": "called", "summary": "tool=pytest"},
                    {"index": "3", "phase": "tool_result", "name": "pytest", "status": "succeeded", "summary": "3 passed, 0 failed"},
                ],
            },
            importance=0.5,
            confidence=0.7,
            evidence=[{"source_ref": "bench://workflow-trace"}],
            tags=["workflow", "workflow_trace", "test_verification_workflow"],
            scope="project",
        )
    ],
    event_assertions=[
        ResultAssertion("memory_content_kind_count", ["workflow_trace", 1]),
        ResultAssertion("memory_content_field_equals", ["workflow_trace", "step_count", "3"]),
        ResultAssertion("event_entry_relation_count", ["recorded_workflow_trace", 1]),
    ],
    spec_ref="Track-F-07",
)


F08_GOVERNANCE_APPROVES_WORKFLOW_STRATEGY = BenchmarkCase(
    case_id="F-08",
    track=Track.F.value,
    capability="workflow_governance_approval",
    description="Deterministic governance approves a reusable workflow strategy candidate and records reviewer votes.",
    direction=Direction.B.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["procedural"],
    setup_memories=[
        SetupMemory(
            memory_type="procedural",
            title="Workflow strategy candidate: governed_test_workflow",
            summary="Reusable governed workflow strategy for test verification.",
            content={
                "scope": "project",
                "kind": "workflow_strategy_candidate",
                "task_type": "governed_test_workflow",
                "success_evidence_count": "2",
                "failure_evidence_count": "0",
                "recommended_steps": ["run focused pytest", "inspect output"],
                "needs_confirmation": "true",
                "confirmed": "false",
            },
            importance=0.72,
            confidence=0.72,
            evidence=[{"source_ref": "bench://workflow-governance-approve"}],
            tags=["workflow", "workflow_strategy_candidate", "governed_test_workflow"],
            scope="project",
        )
    ],
    event_assertions=[
        ResultAssertion("workflow_strategy_governance_approves", ["Workflow strategy candidate: governed_test_workflow", 5]),
    ],
    spec_ref="Track-F-08",
)


F09_GOVERNANCE_REJECTS_PRIVACY_RISK = BenchmarkCase(
    case_id="F-09",
    track=Track.F.value,
    capability="workflow_governance_rejection",
    description="Deterministic governance rejects a workflow strategy candidate with a privacy/secret marker.",
    direction=Direction.B.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["procedural"],
    setup_memories=[
        SetupMemory(
            memory_type="procedural",
            title="Workflow strategy candidate: risky_secret_workflow",
            summary="Do not promote [api_key:REDACTED] workflow details.",
            content={
                "scope": "project",
                "kind": "workflow_strategy_candidate",
                "task_type": "risky_secret_workflow",
                "success_evidence_count": "2",
                "failure_evidence_count": "0",
                "recommended_steps": ["run tool", "verify output"],
                "needs_confirmation": "true",
                "confirmed": "false",
            },
            importance=0.72,
            confidence=0.72,
            evidence=[{"source_ref": "bench://workflow-governance-reject"}],
            tags=["workflow", "workflow_strategy_candidate", "risky_secret_workflow"],
            scope="project",
        )
    ],
    event_assertions=[
        ResultAssertion(
            "workflow_strategy_governance_rejects",
            ["Workflow strategy candidate: risky_secret_workflow", "PrivacyReviewer", 5],
        ),
    ],
    spec_ref="Track-F-09",
)


F10_RICH_WORKFLOW_TRACE_DIAGNOSTICS = BenchmarkCase(
    case_id="F-10",
    track=Track.F.value,
    capability="workflow_trace_diagnostics",
    description="Workflow traces retain step-level verification and failure diagnostics for later reflection.",
    direction=Direction.B.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["procedural"],
    setup_memories=[
        SetupMemory(
            memory_type="procedural",
            title="Workflow trace: fixture_test_verification_workflow",
            summary="Step trace for fixture verification: pytest failed on a missing fixture path.",
            content={
                "scope": "project",
                "kind": "workflow_trace",
                "task_type": "fixture_test_verification_workflow",
                "trigger": "verify fixture change",
                "outcome": "failure",
                "summary": "pytest failed on missing fixture path",
                "step_count": "3",
                "failed_step_indexes": ["3"],
                "verification_signals": ["tests"],
                "failure_signals": ["Traceback: fixture path missing"],
                "tool_families": ["test"],
                "steps": [
                    {"index": "1", "phase": "request", "name": "user_request", "status": "observed", "summary": "verify fixture change"},
                    {
                        "index": "2",
                        "phase": "tool_call",
                        "name": "pytest",
                        "status": "called",
                        "summary": "tool=pytest",
                        "tool_family": "test",
                        "verification_signal": "tests",
                    },
                    {
                        "index": "3",
                        "phase": "tool_result",
                        "name": "pytest",
                        "status": "failed",
                        "summary": "Traceback: fixture path missing; 1 failed, 2 passed",
                        "exit_code": "1",
                        "failure_type": "exception",
                        "failure_signal": "Traceback: fixture path missing",
                        "verification_signal": "tests",
                    },
                ],
            },
            importance=0.55,
            confidence=0.72,
            evidence=[{"source_ref": "bench://workflow-trace-rich"}],
            tags=["workflow", "workflow_trace", "fixture_test_verification_workflow"],
            scope="project",
        )
    ],
    event_assertions=[
        ResultAssertion("workflow_trace_step_field_equals", ["fixture_test_verification_workflow", "3", "failure_type", "exception"]),
        ResultAssertion("workflow_trace_step_field_equals", ["fixture_test_verification_workflow", "3", "exit_code", "1"]),
        ResultAssertion("memory_content_list_contains", ["workflow_trace", "failed_step_indexes", "3"]),
        ResultAssertion("memory_content_list_contains", ["workflow_trace", "verification_signals", "tests"]),
        ResultAssertion("memory_content_list_contains", ["workflow_trace", "tool_families", "test"]),
    ],
    spec_ref="Track-F-10",
)


F11_STRATEGY_SYNTHESIS_USES_TRACE_DIAGNOSTICS = BenchmarkCase(
    case_id="F-11",
    track=Track.F.value,
    capability="workflow_strategy_trace_synthesis",
    description="Workflow strategy synthesis should carry trace diagnostics into recommended steps and known limits.",
    direction=Direction.B_PLUS_C.value,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["procedural"],
    setup_memories=[
        _workflow_memory(
            kind="workflow_success_case",
            task_type="diagnostic_test_workflow",
            title="Workflow success: diagnostic pytest run 1",
            summary="Focused pytest passed after a diagnostic workflow.",
            evidence_ref="bench://workflow-diagnostic-success-1",
            outcome="focused pytest passed",
        ),
        _workflow_memory(
            kind="workflow_success_case",
            task_type="diagnostic_test_workflow",
            title="Workflow success: diagnostic pytest run 2",
            summary="Focused pytest passed again after a diagnostic workflow.",
            evidence_ref="bench://workflow-diagnostic-success-2",
            outcome="focused pytest passed",
        ),
        SetupMemory(
            memory_type="procedural",
            title="Workflow trace: diagnostic_test_workflow",
            summary="Step trace for diagnostic workflow: pytest failed on a missing fixture path.",
            content={
                "scope": "project",
                "kind": "workflow_trace",
                "task_type": "diagnostic_test_workflow",
                "trigger": "verify diagnostic workflow",
                "outcome": "failure",
                "summary": "pytest failed on missing fixture path",
                "step_count": "3",
                "failed_step_indexes": ["3"],
                "verification_signals": ["tests"],
                "failure_signals": ["Traceback: fixture path missing"],
                "tool_families": ["test"],
                "steps": [
                    {"index": "1", "phase": "request", "name": "user_request", "status": "observed", "summary": "verify diagnostic workflow"},
                    {"index": "2", "phase": "tool_call", "name": "pytest", "status": "called", "summary": "tool=pytest", "tool_family": "test", "verification_signal": "tests"},
                    {"index": "3", "phase": "tool_result", "name": "pytest", "status": "failed", "summary": "Traceback: fixture path missing", "exit_code": "1", "failure_type": "exception", "failure_signal": "Traceback: fixture path missing", "verification_signal": "tests"},
                ],
            },
            importance=0.55,
            confidence=0.72,
            evidence=[{"source_ref": "bench://workflow-diagnostic-trace"}],
            tags=["workflow", "workflow_trace", "diagnostic_test_workflow"],
            scope="project",
        ),
    ],
    run_review=True,
    event_assertions=[
        ResultAssertion("memory_content_list_contains", ["workflow_strategy_candidate", "recommended_steps", "run the relevant tests before claiming completion"]),
        ResultAssertion("memory_content_list_contains", ["workflow_strategy_candidate", "known_limits", "Traceback: fixture path missing"]),
        ResultAssertion("memory_content_list_contains", ["workflow_strategy_candidate", "verification_signals", "tests"]),
        ResultAssertion("memory_content_list_contains", ["workflow_strategy_candidate", "tool_families", "test"]),
    ],
    spec_ref="Track-F-11",
)


TRACK_F_CASES = [
    F01_SUCCESS_CASES_SYNTHESIZE_STRATEGY,
    F02_FAILURE_CASE_IS_RECALLABLE,
    F03_GENERAL_RECALL_HIDES_STRATEGY_CANDIDATE,
    F04_WORKFLOW_SKILL_IS_RECALLABLE,
    F05_WORKFLOW_SKILL_FAILURES_TRIGGER_REVIEW,
    F06_WORKFLOW_SKILL_REPEATED_FAILURES_ARCHIVE,
    F07_WORKFLOW_TRACE_RECORDS_STEPS,
    F08_GOVERNANCE_APPROVES_WORKFLOW_STRATEGY,
    F09_GOVERNANCE_REJECTS_PRIVACY_RISK,
    F10_RICH_WORKFLOW_TRACE_DIAGNOSTICS,
    F11_STRATEGY_SYNTHESIS_USES_TRACE_DIAGNOSTICS,
]
