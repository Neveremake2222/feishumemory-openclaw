from benchmarks.runner import (
    ABLATION_FLAT_KEYWORD_ONLY,
    ABLATION_FULL_SYSTEM,
    ABLATION_TYPED_MEMORY_NO_EVENT,
    BASELINE_MEMORY_ENABLED,
    BASELINE_NO_MEMORY,
    BASELINE_RECENT_CONTEXT_ONLY,
    BenchmarkReport,
    _classify_case_failure,
    export_transcripts_jsonl,
    run_ablation_comparison,
    run_all_benchmarks,
    run_baseline_comparison,
    run_case,
)
from benchmarks.structures import BenchmarkCase, RecallSpec, ResultAssertion, SetupMemory
from benchmarks.export_dataset import export_cases_jsonl
from benchmarks.report import build_ablation_markdown_report, build_critical_markdown_report, build_markdown_report
from benchmarks import report as benchmark_report_module
from benchmarks.regression_gate import evaluate_regression_gate
from scripts.export_track_m_failures import export_track_m_failures


def _baseline_case(expected_title: str, forbidden_title: str | None = None) -> BenchmarkCase:
    return BenchmarkCase(
        case_id="TEST-BASELINE",
        track="T",
        capability="baseline_modes",
        description="baseline mode smoke test",
        direction="B",
        complexity_reasoning="low",
        complexity_tool="low",
        complexity_interaction="low",
        memory_types=["decision"],
        setup_memories=[
            SetupMemory(
                memory_type="decision",
                title="Older Baseline Marker",
                summary="Older decision marker for baseline smoke tests.",
                content={"marker": "older baseline marker"},
                importance=0.9,
                confidence=0.95,
            ),
            SetupMemory(
                memory_type="decision",
                title="Recent Baseline Marker",
                summary="Recent decision marker for baseline smoke tests.",
                content={"marker": "recent baseline marker"},
                importance=0.9,
                confidence=0.95,
            ),
        ],
        recalls=[
            RecallSpec(
                query="baseline marker decision",
                project_id="proj-alpha",
                scope="project",
                limit=10,
            )
        ],
        expected_titles=[expected_title],
        forbidden_titles=[forbidden_title] if forbidden_title else [],
    )


def test_run_case_defaults_to_memory_enabled() -> None:
    result = run_case(_baseline_case("Older Baseline Marker"))

    assert result.baseline_mode == BASELINE_MEMORY_ENABLED
    assert result.passed
    assert result.write_latency_ms is not None
    assert result.write_latency_ms > 0
    assert result.transcript is not None
    assert result.transcript["case_id"] == "TEST-BASELINE"
    assert result.transcript["setup"]["memories"]
    assert result.transcript["recalls"][0]["query"] == "baseline marker decision"
    assert result.transcript["recalls"][0]["results"]
    assert result.transcript["outcome"]["passed"] is True
    assert result.rubric_score == 1.0
    assert result.rubric_scores
    assert result.transcript["outcome"]["rubric_score"] == 1.0
    assert result.answer_text
    assert result.answer_faithfulness == 1.0
    assert result.answer_relevancy == 1.0
    assert result.memory_improvement == 1.0
    assert result.transcript["outcome"]["answer_scores"]["faithfulness"] == 1.0


def test_run_case_baseline_no_memory_skips_setup() -> None:
    result = run_case(_baseline_case("Older Baseline Marker"), baseline_mode=BASELINE_NO_MEMORY)

    assert result.baseline_mode == BASELINE_NO_MEMORY
    assert not result.passed
    assert result.write_latency_ms == 0
    assert result.memory_event_rate is None
    assert any("skipped setup" in note for note in result.notes or [])


def test_run_case_recent_context_only_uses_latest_setup_memory() -> None:
    result = run_case(
        _baseline_case("Recent Baseline Marker", forbidden_title="Older Baseline Marker"),
        baseline_mode=BASELINE_RECENT_CONTEXT_ONLY,
    )

    assert result.baseline_mode == BASELINE_RECENT_CONTEXT_ONLY
    assert result.passed
    assert result.write_latency_ms is not None
    assert result.write_latency_ms > 0


def test_run_case_flat_keyword_only_flattens_memory_type() -> None:
    result = run_case(_baseline_case("Older Baseline Marker"), baseline_mode=ABLATION_FLAT_KEYWORD_ONLY)

    assert result.baseline_mode == ABLATION_FLAT_KEYWORD_ONLY
    assert result.passed
    assert result.transcript["setup"]["memories"][0]["memory_type"] == "semantic"
    assert any("flat_keyword_only" in note for note in result.notes or [])


def test_run_case_typed_memory_no_event_clears_trace_entries() -> None:
    result = run_case(_baseline_case("Older Baseline Marker"), baseline_mode=ABLATION_TYPED_MEMORY_NO_EVENT)

    assert result.baseline_mode == ABLATION_TYPED_MEMORY_NO_EVENT
    assert result.passed
    assert result.memory_event_rate == 0.0
    assert any("typed_memory_no_event" in note for note in result.notes or [])


def test_run_baseline_comparison_uses_requested_modes(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_run_all_benchmarks(
        baseline_mode: str = BASELINE_MEMORY_ENABLED,
        *,
        print_reports: bool = True,
        track_ids: list[str] | None = None,
    ):
        calls.append((baseline_mode, print_reports))
        return {}

    monkeypatch.setattr("benchmarks.runner.run_all_benchmarks", fake_run_all_benchmarks)

    reports = run_baseline_comparison(
        modes=[BASELINE_NO_MEMORY, BASELINE_RECENT_CONTEXT_ONLY],
        print_reports=True,
    )

    assert reports == {
        BASELINE_NO_MEMORY: {},
        BASELINE_RECENT_CONTEXT_ONLY: {},
    }
    assert calls == [
        (BASELINE_NO_MEMORY, True),
        (BASELINE_RECENT_CONTEXT_ONLY, True),
    ]


def test_run_ablation_comparison_uses_requested_modes(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_run_all_benchmarks(
        baseline_mode: str = BASELINE_MEMORY_ENABLED,
        *,
        print_reports: bool = True,
    ):
        calls.append((baseline_mode, print_reports))
        return {}

    monkeypatch.setattr("benchmarks.runner.run_all_benchmarks", fake_run_all_benchmarks)

    reports = run_ablation_comparison(
        modes=[ABLATION_FLAT_KEYWORD_ONLY, ABLATION_FULL_SYSTEM],
        print_reports=True,
    )

    assert reports == {
        ABLATION_FLAT_KEYWORD_ONLY: {},
        ABLATION_FULL_SYSTEM: {},
    }
    assert calls == [
        (ABLATION_FLAT_KEYWORD_ONLY, True),
        (ABLATION_FULL_SYSTEM, True),
    ]


def test_critical_report_runs_bounded_gate(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_run_track(cases, track_label: str, baseline_mode: str = BASELINE_MEMORY_ENABLED):
        calls.append((track_label, len(cases)))
        return BenchmarkReport(
            track=track_label,
            baseline_mode=baseline_mode,
            total=len(cases),
            passed=len(cases),
            failed=0,
            skip=0,
            cases=[],
            by_capability={},
        )

    small_case = _baseline_case("Older Baseline Marker")
    monkeypatch.setattr(
        benchmark_report_module,
        "_benchmark_tracks",
        lambda: [
            ("A", "A", [small_case]),
            ("B", "B", [small_case]),
            ("J", "J", [small_case]),
            ("L", "L", [small_case]),
        ],
    )
    monkeypatch.setattr(benchmark_report_module, "generate_decision_version_cases", lambda track: [small_case])
    monkeypatch.setattr(benchmark_report_module, "generate_preference_cases", lambda track: [small_case])
    monkeypatch.setattr(benchmark_report_module, "generate_zero_result_cases", lambda track: [small_case])
    monkeypatch.setattr(benchmark_report_module, "run_track", fake_run_track)

    reports, cases = benchmark_report_module.run_critical_benchmarks()
    markdown = build_critical_markdown_report(reports, cases)

    assert [label for label, _ in calls] == [
        "A",
        "B",
        "J",
        "L",
        "JGEN-decision-version",
        "JGEN-preference-recall",
        "JGEN-zero-result",
    ]
    assert len(cases) == 7
    assert "Run mode: `critical`" in markdown
    assert "7/7 passed" in markdown


def test_run_all_benchmarks_validates_baseline_mode() -> None:
    try:
        run_all_benchmarks(baseline_mode="unknown", print_reports=False)
    except ValueError as exc:
        assert "unknown baseline_mode" in str(exc)
    else:
        raise AssertionError("expected invalid baseline mode to raise")


def test_run_all_benchmarks_filters_tracks(monkeypatch) -> None:
    calls: list[str] = []
    case = _baseline_case("Older Baseline Marker")

    monkeypatch.setattr(
        "benchmarks.runner._benchmark_tracks",
        lambda: [
            ("A", "A", [case]),
            ("B", "B", [case]),
            ("J-gen", "J-gen", [case]),
        ],
    )

    def fake_run_track(cases, track_label: str, baseline_mode: str = BASELINE_MEMORY_ENABLED):
        calls.append(track_label)
        return BenchmarkReport(
            track=track_label,
            baseline_mode=baseline_mode,
            total=len(cases),
            passed=len(cases),
            failed=0,
            skip=0,
            cases=[],
            by_capability={},
        )

    monkeypatch.setattr("benchmarks.runner.run_track", fake_run_track)

    reports = run_all_benchmarks(print_reports=False, track_ids=["B", "j-GEN"])

    assert list(reports) == ["B", "J-gen"]
    assert calls == ["B", "J-gen"]


def test_run_all_benchmarks_rejects_unknown_track(monkeypatch) -> None:
    monkeypatch.setattr("benchmarks.runner._benchmark_tracks", lambda: [("A", "A", [])])

    try:
        run_all_benchmarks(print_reports=False, track_ids=["missing"])
    except ValueError as exc:
        assert "unknown benchmark track" in str(exc)
        assert "A" in str(exc)
    else:
        raise AssertionError("expected unknown track to raise")


def test_write_markdown_report_uses_selected_cases(monkeypatch) -> None:
    import shutil
    import uuid
    from pathlib import Path

    case_a = _baseline_case("Older Baseline Marker")
    case_b = _baseline_case("Recent Baseline Marker")
    temp_dir = Path("tests_runtime") / "selected_report" / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        benchmark_report_module,
        "_selected_benchmark_tracks",
        lambda track_ids=None: [("B", "B", [case_b])] if track_ids else [("A", "A", [case_a]), ("B", "B", [case_b])],
    )

    def fake_run_all_benchmarks(
        baseline_mode: str = BASELINE_MEMORY_ENABLED,
        *,
        print_reports: bool = True,
        track_ids: list[str] | None = None,
    ):
        return {
            "B": BenchmarkReport(
                track="B",
                baseline_mode=baseline_mode,
                total=1,
                passed=1,
                failed=0,
                skip=0,
                cases=[],
                by_capability={},
            )
        }

    monkeypatch.setattr(benchmark_report_module, "run_all_benchmarks", fake_run_all_benchmarks)

    try:
        output = temp_dir / "selected_report.md"
        benchmark_report_module.write_markdown_report(output, include_baselines=False, track_ids=["B"])

        markdown = output.read_text(encoding="utf-8")
        assert "Case count: `1`" in markdown
        assert "| B | 1 | 1 | 0 |" in markdown
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_write_sharded_markdown_report_writes_shards_and_aggregate(monkeypatch) -> None:
    import shutil
    import uuid
    from pathlib import Path

    case_a = _baseline_case("Older Baseline Marker")
    case_b = _baseline_case("Recent Baseline Marker")
    temp_dir = Path("tests_runtime") / "sharded_report" / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)

    def fake_selected(track_ids=None):
        if track_ids == ["A"]:
            return [("A", "A", [case_a])]
        if track_ids == ["B"]:
            return [("B", "B", [case_b])]
        return [("A", "A", [case_a]), ("B", "B", [case_b])]

    def fake_run_all_benchmarks(
        baseline_mode: str = BASELINE_MEMORY_ENABLED,
        *,
        print_reports: bool = True,
        track_ids: list[str] | None = None,
    ):
        track = track_ids[0]
        return {
            track: BenchmarkReport(
                track=track,
                baseline_mode=baseline_mode,
                total=1,
                passed=1,
                failed=0,
                skip=0,
                cases=[],
                by_capability={},
            )
        }

    monkeypatch.setattr(benchmark_report_module, "_selected_benchmark_tracks", fake_selected)
    monkeypatch.setattr(benchmark_report_module, "run_all_benchmarks", fake_run_all_benchmarks)

    try:
        output = temp_dir / "full_report.md"
        benchmark_report_module.write_sharded_markdown_report(
            output,
            include_baselines=False,
            shards=[["A"], ["B"]],
        )

        markdown = output.read_text(encoding="utf-8")
        shard_files = sorted((temp_dir / "full_report_shards").glob("*.md"))
        assert "# Sharded Benchmark Report" in markdown
        assert "Shard count: `2`" in markdown
        assert "Case count: `2`" in markdown
        assert "| 1 | A | 1 | 1 | 0 |" in markdown
        assert "| 2 | B | 1 | 1 | 0 |" in markdown
        assert "| A | 1 | 1 | 0 |" in markdown
        assert "| B | 1 | 1 | 0 |" in markdown
        assert len(shard_files) == 2
        assert all("# Benchmark Report" in path.read_text(encoding="utf-8") for path in shard_files)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_parse_shard_specs() -> None:
    assert benchmark_report_module._parse_shard_specs("A,B; C ;J,J-gen") == [
        ["A", "B"],
        ["C"],
        ["J", "J-gen"],
    ]
    assert benchmark_report_module._parse_shard_specs(" ; ") is None


def test_export_cases_jsonl_writes_reviewable_dataset() -> None:
    import json
    import shutil
    import uuid
    from pathlib import Path

    temp_dir = Path("tests_runtime") / "benchmark_export" / str(uuid.uuid4())
    output = temp_dir / "cases.jsonl"

    try:
        count = export_cases_jsonl(output)
        records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        assert count == len(records)
        assert count >= 71
        assert records[0]["case_id"]
        assert records[0]["complexity"]["reasoning"]
        required_dataset_fields = [
            "memory_target",
            "evaluation_task",
            "expected_behavior",
            "ground_truth",
            "scoring_rubric",
            "difficulty",
            "source_anchor",
        ]
        for record in records:
            for field in required_dataset_fields:
                assert record[field], f"{record['case_id']} missing {field}"
        track_i = [record for record in records if record["track"] == "I"]
        assert len(track_i) == 30
        assert {record["memory_target"] for record in track_i} >= {
            "artifact",
            "instruction",
            "context",
            "workflow_trace",
            "relationship",
            "governance_reviewer",
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_export_transcripts_jsonl_writes_trace_records() -> None:
    import json
    import shutil
    import uuid
    from pathlib import Path

    temp_dir = Path("tests_runtime") / "benchmark_transcripts" / str(uuid.uuid4())
    output = temp_dir / "transcripts.jsonl"

    try:
        report = BenchmarkReport(
            track="T",
            baseline_mode=BASELINE_MEMORY_ENABLED,
            total=1,
            passed=1,
            failed=0,
            skip=0,
            cases=[run_case(_baseline_case("Older Baseline Marker"))],
            by_capability={"baseline_modes": {"passed": 1, "failed": 0}},
        )
        count = export_transcripts_jsonl(output, reports={"T": report})
        records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        assert count == 1
        assert records[0]["case_id"] == "TEST-BASELINE"
        assert records[0]["setup"]["memories"]
        assert records[0]["recalls"][0]["result_count"] > 0
        assert records[0]["outcome"]["passed"] is True
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_export_track_m_failures_writes_current_failure() -> None:
    import shutil
    import uuid
    from pathlib import Path

    temp_dir = Path("tests_runtime") / "track_m_failures" / str(uuid.uuid4())
    output = temp_dir / "failures.jsonl"

    try:
        count = export_track_m_failures(output)

        assert count == 0
        assert output.read_text(encoding="utf-8") == ""
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_failure_classifier_uses_specific_categories() -> None:
    assert _classify_case_failure(["Missing expected title: 'A'"]) == "missed_recall"
    assert _classify_case_failure(["Found forbidden title: 'B'"]) == "wrong_recall"
    assert _classify_case_failure(["Result count 9 outside expected range [1, 3]"]) == "over_retrieval_noise"
    assert _classify_case_failure(["Expected zero results but got 2"]) == "hallucinated_memory"
    assert _classify_case_failure(["workflow trace step assertion failed: deploy step 1 status expected ok"]) == "event_trace_missing"
    assert _classify_case_failure(["workflow strategy governance approval assertion failed: votes=1"]) == "governance_failed"


def test_trace_completeness_is_reported_for_event_assertions() -> None:
    case = BenchmarkCase(
        case_id="TEST-TRACE",
        track="T",
        capability="trace_quality",
        description="trace completeness smoke test",
        direction="B",
        complexity_reasoning="low",
        complexity_tool="low",
        complexity_interaction="low",
        memory_types=["event"],
        event_assertions=[
            ResultAssertion("event_entry_relation_count", ["missing_relation", 0]),
        ],
    )

    result = run_case(case)

    assert result.passed is True
    assert result.trace_completeness == 1.0
    assert result.trace_checks_passed == 1
    assert result.trace_checks_total == 1
    assert result.transcript["outcome"]["trace_completeness"] == 1.0


def test_build_markdown_report_includes_required_sections() -> None:
    report = BenchmarkReport(
        track="T",
        baseline_mode=BASELINE_MEMORY_ENABLED,
        total=1,
        passed=1,
        failed=0,
        skip=0,
        cases=[],
        by_capability={"baseline_modes": {"passed": 1, "failed": 0}},
        average_duration_ms=12.0,
        average_rubric_score=1.0,
        average_write_latency_ms=3.0,
        average_retrieval_latency_ms=4.0,
        average_context_precision=1.0,
        average_context_recall=1.0,
        context_evaluated_cases=1,
        average_trace_completeness=1.0,
        trace_evaluated_cases=1,
        average_answer_faithfulness=1.0,
        average_answer_relevancy=1.0,
        average_memory_improvement=1.0,
        answer_evaluated_cases=1,
        memory_improvement_evaluated_cases=1,
        average_memory_event_rate=1.0,
        failure_type_counts={},
        relevant_selected_count=1,
        irrelevant_selected_count=0,
    )
    baseline_report = BenchmarkReport(
        track="T",
        baseline_mode=BASELINE_NO_MEMORY,
        total=1,
        passed=0,
        failed=1,
        skip=0,
        cases=[],
        by_capability={"baseline_modes": {"passed": 0, "failed": 1}},
        average_duration_ms=10.0,
        average_rubric_score=0.0,
        average_write_latency_ms=0.0,
        average_retrieval_latency_ms=1.0,
        average_context_precision=0.0,
        average_context_recall=0.0,
        context_evaluated_cases=0,
        average_trace_completeness=0.0,
        trace_evaluated_cases=0,
        average_answer_faithfulness=0.0,
        average_answer_relevancy=0.0,
        average_memory_improvement=0.0,
        answer_evaluated_cases=0,
        memory_improvement_evaluated_cases=0,
        average_memory_event_rate=0.0,
        failure_type_counts={"missed_recall": 1},
    )

    markdown = build_markdown_report(
        {"T": report},
        cases=[_baseline_case("Older Baseline Marker")],
        baseline_reports={
            BASELINE_MEMORY_ENABLED: {"T": report},
            BASELINE_NO_MEMORY: {"T": baseline_report},
        },
        dataset_version="test-version",
    )

    assert "# Benchmark Report" in markdown
    assert "Dataset version: `test-version`" in markdown
    assert "## Pass Rate By Track" in markdown
    assert "| T | 1 | 1 | 0 | 1.00 | 4.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |" in markdown
    assert "### Dataset Readiness" in markdown
    assert "| ground_truth | 1 | 1 | 100.0% |" in markdown
    assert "| scoring_rubric | 1 | 1 | 100.0% |" in markdown
    assert "## Baseline Comparison" in markdown
    assert "### Baseline Delta" in markdown
    assert BASELINE_NO_MEMORY in markdown
    assert "## Failure Type Distribution" in markdown
    assert "No default-run failures." in markdown
    assert "## Memory Tax Summary" in markdown
    assert "Average rubric score: `1.00`" in markdown
    assert "Average context precision: `1.00`" in markdown
    assert "Average trace completeness: `1.00`" in markdown
    assert "Average answer faithfulness: `1.00`" in markdown
    assert "Average answer relevancy: `1.00`" in markdown
    assert "Average memory improvement: `1.00`" in markdown
    assert "## Known Blind Spots" in markdown


def test_build_ablation_markdown_report_includes_mode_delta() -> None:
    full_report = BenchmarkReport(
        track="T",
        baseline_mode=ABLATION_FULL_SYSTEM,
        total=1,
        passed=1,
        failed=0,
        skip=0,
        cases=[],
        by_capability={"ablation": {"passed": 1, "failed": 0}},
        average_rubric_score=1.0,
        average_context_precision=1.0,
        average_context_recall=1.0,
        context_evaluated_cases=1,
        average_trace_completeness=1.0,
        trace_evaluated_cases=1,
        average_memory_event_rate=1.0,
    )
    flat_report = BenchmarkReport(
        track="T",
        baseline_mode=ABLATION_FLAT_KEYWORD_ONLY,
        total=1,
        passed=0,
        failed=1,
        skip=0,
        cases=[],
        by_capability={"ablation": {"passed": 0, "failed": 1}},
        average_rubric_score=0.0,
        average_context_precision=0.0,
        average_context_recall=0.0,
        context_evaluated_cases=1,
        average_trace_completeness=0.0,
        trace_evaluated_cases=1,
        average_memory_event_rate=0.0,
        failure_type_counts={"missed_recall": 1},
    )

    markdown = build_ablation_markdown_report(
        {
            ABLATION_FLAT_KEYWORD_ONLY: {"T": flat_report},
            ABLATION_FULL_SYSTEM: {"T": full_report},
        },
        dataset_version="test-version",
    )

    assert "# Benchmark Ablation Report" in markdown
    assert "Reference mode: `full_system`" in markdown
    assert "| flat_keyword_only | 0 | 1 | 1 | -1/1 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |" in markdown
    assert "| full_system | 1 | 1 | 0 | +0/1 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |" in markdown
    assert "## Track Delta" in markdown
    assert "| flat_keyword_only | T | 0 | 1 | -1 | 1 | 0.00 | 0.00 |" in markdown
    assert "## Failure Types By Mode" in markdown
    assert "### flat_keyword_only" in markdown
    assert "| missed_recall | 1 |" in markdown


def test_regression_gate_evaluates_core_invariants() -> None:
    case = _baseline_case("Older Baseline Marker")
    case.evaluation_task = "Recall the baseline marker."
    case.expected_behavior = "Return the expected baseline marker."
    result = run_case(case)
    report = BenchmarkReport(
        track="T",
        baseline_mode=BASELINE_MEMORY_ENABLED,
        total=1,
        passed=1,
        failed=0,
        skip=0,
        cases=[result],
        by_capability={"baseline_modes": {"passed": 1, "failed": 0}},
    )

    gate = evaluate_regression_gate({"T": report}, [case], min_case_count=1)

    assert gate.passed
    assert not gate.errors
    assert "dataset readiness: 100%" in gate.checks
    assert "transcript coverage: 1/1" in gate.checks


def test_regression_gate_fails_case_count_threshold() -> None:
    gate = evaluate_regression_gate({}, [], min_case_count=1)

    assert not gate.passed
    assert any("case count below threshold" in error for error in gate.errors)
