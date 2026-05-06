"""Generate a Markdown benchmark report.

Usage:
    python -m benchmarks.report benchmarks_runtime/benchmark_report.md
    python -m benchmarks.report benchmarks_runtime/benchmark_report.md --no-baselines
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.export_dataset import benchmark_cases, case_to_record
from benchmarks.generator import (
    generate_decision_version_cases,
    generate_preference_cases,
    generate_zero_result_cases,
)
from benchmarks.runner import (
    ABLATION_FULL_SYSTEM,
    BASELINE_MEMORY_ENABLED,
    BenchmarkReport,
    _benchmark_tracks,
    _selected_benchmark_tracks,
    run_ablation_comparison,
    run_all_benchmarks,
    run_baseline_comparison,
    run_track,
)
from benchmarks.structures import BenchmarkCase


DATASET_VERSION = "2026-05-05"
CRITICAL_TRACKS = ("A", "B", "J", "L")


def run_critical_benchmarks() -> tuple[dict[str, BenchmarkReport], list[BenchmarkCase]]:
    """Run the bounded P0 gate suite used for fast current-state reporting."""
    reports: dict[str, BenchmarkReport] = {}
    selected_cases: list[BenchmarkCase] = []

    track_cases = {track_id: cases for track_id, _, cases in _benchmark_tracks()}
    for track_id in CRITICAL_TRACKS:
        cases = track_cases[track_id]
        selected_cases.extend(cases)
        reports[track_id] = run_track(cases, track_id, baseline_mode=BASELINE_MEMORY_ENABLED)

    generated_suites = [
        ("JGEN-decision-version", generate_decision_version_cases(track="JGEN")),
        ("JGEN-preference-recall", generate_preference_cases(track="JGEN")),
        ("JGEN-zero-result", generate_zero_result_cases(track="JGEN")),
    ]
    for label, cases in generated_suites:
        selected_cases.extend(cases)
        reports[label] = run_track(cases, label, baseline_mode=BASELINE_MEMORY_ENABLED)

    return reports, selected_cases


def build_markdown_report(
    reports: dict[str, BenchmarkReport],
    *,
    cases: list[BenchmarkCase] | None = None,
    baseline_reports: dict[str, dict[str, BenchmarkReport]] | None = None,
    dataset_version: str = DATASET_VERSION,
) -> str:
    """Build a compact Markdown report from benchmark reports and case metadata."""
    selected_cases = cases if cases is not None else benchmark_cases()
    total, passed, failed = _overall(reports)
    lines = [
        "# Benchmark Report",
        "",
        f"- Dataset version: `{dataset_version}`",
        f"- Case count: `{len(selected_cases)}`",
        f"- Default result: `{passed}/{total} passed`",
        f"- Default failures: `{failed}`",
        "",
        "## Pass Rate By Track",
        "",
        "| Track | Passed | Total | Failed | Avg Rubric | Avg Recall ms | Context Precision | Context Recall | Trace Completeness | Answer Faithfulness | Answer Relevancy | Memory Improvement | Memory Event Rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for track, report in sorted(reports.items()):
        lines.append(
            f"| {track} | {report.passed} | {report.total} | {report.failed} | "
            f"{report.average_rubric_score:.2f} | "
            f"{report.average_retrieval_latency_ms:.2f} | "
            f"{_format_metric(report.average_context_precision, report.context_evaluated_cases)} | "
            f"{_format_metric(report.average_context_recall, report.context_evaluated_cases)} | "
            f"{_format_metric(report.average_trace_completeness, report.trace_evaluated_cases)} | "
            f"{_format_metric(report.average_answer_faithfulness, report.answer_evaluated_cases)} | "
            f"{_format_metric(report.average_answer_relevancy, report.answer_evaluated_cases)} | "
            f"{_format_metric(report.average_memory_improvement, report.memory_improvement_evaluated_cases)} | "
            f"{report.average_memory_event_rate:.2f} |"
        )

    lines.extend([
        "",
        "## Case Counts",
        "",
        "### By Memory Type",
        "",
    ])
    lines.extend(_counter_table(_case_counter(selected_cases, "memory_type"), "Memory Type"))
    lines.extend([
        "",
        "### By Difficulty",
        "",
    ])
    lines.extend(_counter_table(_case_counter(selected_cases, "difficulty"), "Difficulty"))
    lines.extend([
        "",
        "### Dataset Readiness",
        "",
        "| Field | Present | Total | Coverage |",
        "| --- | ---: | ---: | ---: |",
    ])
    for field, present, total_count, coverage in _dataset_readiness(selected_cases):
        lines.append(f"| {field} | {present} | {total_count} | {coverage:.1%} |")

    lines.extend([
        "",
        "## Baseline Comparison",
        "",
    ])
    if baseline_reports:
        lines.extend([
            "| Mode | Passed | Total | Failed | Avg Recall ms | Memory Event Rate |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ])
        for mode, mode_reports in baseline_reports.items():
            mode_total, mode_passed, mode_failed = _overall(mode_reports)
            avg_recall = _weighted_average(
                [(report.average_retrieval_latency_ms, report.total) for report in mode_reports.values()]
            )
            avg_event_rate = _weighted_average(
                [(report.average_memory_event_rate, report.total) for report in mode_reports.values()]
            )
            lines.append(
                f"| {mode} | {mode_passed} | {mode_total} | {mode_failed} | "
                f"{avg_recall:.2f} | {avg_event_rate:.2f} |"
            )
        lines.extend([
            "",
            "### Baseline Delta",
            "",
        ])
        lines.extend(_baseline_delta_table(baseline_reports))
    else:
        lines.append("Baseline comparison was not run for this report.")

    failure_counts = _failure_counts(reports)
    lines.extend([
        "",
        "## Failure Type Distribution",
        "",
    ])
    if failure_counts:
        lines.extend(_named_counter_table(failure_counts, "Failure Type"))
    else:
        lines.append("No default-run failures.")

    lines.extend([
        "",
        "## Memory Tax Summary",
        "",
        f"- Average rubric score: `{_weighted_average([(r.average_rubric_score, r.total) for r in reports.values()]):.2f}`",
        f"- Average write latency: `{_weighted_average([(r.average_write_latency_ms, r.total) for r in reports.values()]):.2f} ms`",
        f"- Average recall latency: `{_weighted_average([(r.average_retrieval_latency_ms, r.total) for r in reports.values()]):.2f} ms`",
        f"- Average context precision: `{_weighted_context_metric(reports, 'average_context_precision')}`",
        f"- Average context recall: `{_weighted_context_metric(reports, 'average_context_recall')}`",
        f"- Average trace completeness: `{_weighted_trace_metric(reports)}`",
        f"- Average answer faithfulness: `{_weighted_answer_metric(reports, 'average_answer_faithfulness')}`",
        f"- Average answer relevancy: `{_weighted_answer_metric(reports, 'average_answer_relevancy')}`",
        f"- Average memory improvement: `{_weighted_memory_improvement_metric(reports)}`",
        f"- Average memory event rate: `{_weighted_average([(r.average_memory_event_rate, r.total) for r in reports.values()]):.2f}`",
        f"- Relevant selected count: `{sum(r.relevant_selected_count for r in reports.values())}`",
        f"- Irrelevant selected count: `{sum(r.irrelevant_selected_count for r in reports.values())}`",
        "",
        "## Known Blind Spots",
        "",
        "- The suite is deterministic and does not yet measure live Feishu network reliability.",
        "- Answer-level evaluation uses deterministic extractive answers, not open-ended LLM generation.",
        "- Memory tax is approximated with latency and selection counts, not prompt-token cost.",
        "- Dataset export is reviewable JSONL, but not yet a versioned external benchmark package.",
    ])
    return "\n".join(lines) + "\n"


def write_markdown_report(
    output_path: str | Path,
    *,
    include_baselines: bool = True,
    track_ids: list[str] | None = None,
) -> Path:
    """Run benchmarks and write the Markdown report."""
    path = Path(output_path)
    if include_baselines:
        baseline_reports = run_baseline_comparison(print_reports=False, track_ids=track_ids)
        reports = baseline_reports[BASELINE_MEMORY_ENABLED]
    else:
        baseline_reports = None
        reports = run_all_benchmarks(print_reports=False, track_ids=track_ids)
    markdown = build_markdown_report(
        reports,
        cases=_report_cases(track_ids),
        baseline_reports=baseline_reports,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path


def write_sharded_markdown_report(
    output_path: str | Path,
    *,
    include_baselines: bool = True,
    shards: list[list[str]] | None = None,
) -> Path:
    """Run benchmark shards, write per-shard reports, then write one aggregate report."""
    path = Path(output_path)
    selected_shards = shards or _default_report_shards()
    shard_dir = path.with_suffix("")
    shard_dir = shard_dir.parent / f"{shard_dir.name}_shards"
    merged_reports: dict[str, BenchmarkReport] = {}
    merged_baseline_reports: dict[str, dict[str, BenchmarkReport]] | None = {} if include_baselines else None
    selected_cases: list[BenchmarkCase] = []
    shard_rows: list[tuple[int, list[str], int, int, int, Path]] = []

    shard_dir.mkdir(parents=True, exist_ok=True)
    for index, shard_tracks in enumerate(selected_shards, 1):
        canonical_tracks = _canonical_track_ids(shard_tracks)
        shard_cases = _report_cases(canonical_tracks)
        selected_cases.extend(shard_cases)
        shard_output = shard_dir / f"shard_{index:02d}_{'_'.join(canonical_tracks).replace('-', '_')}.md"

        if include_baselines:
            shard_baselines = run_baseline_comparison(print_reports=False, track_ids=canonical_tracks)
            shard_reports = shard_baselines[BASELINE_MEMORY_ENABLED]
            assert merged_baseline_reports is not None
            for mode, mode_reports in shard_baselines.items():
                merged_baseline_reports.setdefault(mode, {}).update(mode_reports)
        else:
            shard_baselines = None
            shard_reports = run_all_benchmarks(print_reports=False, track_ids=canonical_tracks)

        merged_reports.update(shard_reports)
        shard_markdown = build_markdown_report(
            shard_reports,
            cases=shard_cases,
            baseline_reports=shard_baselines,
        )
        shard_output.write_text(shard_markdown, encoding="utf-8")
        total, passed, failed = _overall(shard_reports)
        shard_rows.append((index, canonical_tracks, total, passed, failed, shard_output))

    aggregate_markdown = build_sharded_markdown_report(
        merged_reports,
        cases=selected_cases,
        baseline_reports=merged_baseline_reports,
        shard_rows=shard_rows,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(aggregate_markdown, encoding="utf-8")
    return path


def build_sharded_markdown_report(
    reports: dict[str, BenchmarkReport],
    *,
    cases: list[BenchmarkCase],
    baseline_reports: dict[str, dict[str, BenchmarkReport]] | None,
    shard_rows: list[tuple[int, list[str], int, int, int, Path]],
    dataset_version: str = DATASET_VERSION,
) -> str:
    """Build an aggregate report with the shard manifest at the top."""
    total, passed, failed = _overall(reports)
    lines = [
        "# Sharded Benchmark Report",
        "",
        f"- Generated at: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Dataset version: `{dataset_version}`",
        "- Run mode: `sharded-full`",
        f"- Shard count: `{len(shard_rows)}`",
        f"- Case count: `{len(cases)}`",
        f"- Result: `{passed}/{total} passed`",
        f"- Failures: `{failed}`",
        "",
        "This report aggregates independently executed track shards. Per-shard reports are written next to this file so a long full-suite run can retain completed shard evidence.",
        "",
        "## Shard Manifest",
        "",
        "| Shard | Tracks | Passed | Total | Failed | Report |",
        "| ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for index, tracks, shard_total, shard_passed, shard_failed, shard_output in shard_rows:
        lines.append(
            f"| {index} | {', '.join(tracks)} | {shard_passed} | {shard_total} | "
            f"{shard_failed} | `{shard_output.as_posix()}` |"
        )
    lines.append("")
    return "\n".join(lines) + build_markdown_report(
        reports,
        cases=cases,
        baseline_reports=baseline_reports,
        dataset_version=dataset_version,
    )


def build_critical_markdown_report(
    reports: dict[str, BenchmarkReport],
    cases: list[BenchmarkCase],
    *,
    dataset_version: str = DATASET_VERSION,
) -> str:
    """Build a report for the bounded P0 regression gate."""
    total, passed, failed = _overall(reports)
    generated_labels = [label for label in reports if label.startswith("JGEN-")]
    header = [
        "# Critical Benchmark Report",
        "",
        f"- Generated at: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Dataset version: `{dataset_version}`",
        f"- Run mode: `critical`",
        f"- Included hand-written tracks: `{', '.join(CRITICAL_TRACKS)}`",
        f"- Included generated gates: `{', '.join(generated_labels)}`",
        f"- Case count: `{len(cases)}`",
        f"- Result: `{passed}/{total} passed`",
        f"- Failures: `{failed}`",
        "",
        "This report is the fast P0 gate for zero-result refusal, preference recall, decision version chains, and agent-task recall. It does not replace the full suite; use the full report when runtime permits.",
        "",
    ]
    return "\n".join(header) + build_markdown_report(
        reports,
        cases=cases,
        baseline_reports=None,
        dataset_version=dataset_version,
    )


def write_critical_report(output_path: str | Path) -> Path:
    """Run the bounded P0 gate and write a current-state Markdown report."""
    path = Path(output_path)
    reports, cases = run_critical_benchmarks()
    markdown = build_critical_markdown_report(reports, cases)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path


def _report_cases(track_ids: list[str] | None = None) -> list[BenchmarkCase]:
    if not track_ids:
        return benchmark_cases()
    cases: list[BenchmarkCase] = []
    for _, _, track_cases in _selected_benchmark_tracks(track_ids):
        cases.extend(track_cases)
    return cases


def _default_report_shards() -> list[list[str]]:
    """Default to one benchmark track per shard for resumable full-suite reporting."""
    return [[track_id] for track_id, _, _ in _benchmark_tracks()]


def _canonical_track_ids(track_ids: list[str]) -> list[str]:
    return [track_id for track_id, _, _ in _selected_benchmark_tracks(track_ids)]


def build_ablation_markdown_report(
    ablation_reports: dict[str, dict[str, BenchmarkReport]],
    *,
    dataset_version: str = DATASET_VERSION,
) -> str:
    """Build a compact Markdown report for internal ablation modes."""
    full_system_reports = ablation_reports.get(ABLATION_FULL_SYSTEM)
    full_total, full_passed, _ = _overall(full_system_reports or {})
    lines = [
        "# Benchmark Ablation Report",
        "",
        f"- Dataset version: `{dataset_version}`",
        f"- Reference mode: `{ABLATION_FULL_SYSTEM}`",
        "",
        "| Mode | Passed | Total | Failed | Delta vs Full | Avg Rubric | Context Precision | Context Recall | Trace Completeness | Memory Event Rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, reports in ablation_reports.items():
        total, passed, failed = _overall(reports)
        comparable_total = min(full_total, total) if full_total else total
        delta = passed - full_passed if full_system_reports else 0
        lines.append(
            f"| {mode} | {passed} | {total} | {failed} | {delta:+d}/{comparable_total} | "
            f"{_weighted_average([(r.average_rubric_score, r.total) for r in reports.values()]):.2f} | "
            f"{_weighted_context_metric(reports, 'average_context_precision')} | "
            f"{_weighted_context_metric(reports, 'average_context_recall')} | "
            f"{_weighted_trace_metric(reports)} | "
            f"{_weighted_average([(r.average_memory_event_rate, r.total) for r in reports.values()]):.2f} |"
        )

    lines.extend([
        "",
        "## Track Delta",
        "",
    ])
    lines.extend(_ablation_track_delta_table(ablation_reports))

    lines.extend([
        "",
        "## Failure Types By Mode",
        "",
    ])
    lines.extend(_ablation_failure_type_tables(ablation_reports))

    lines.extend([
        "",
        "## Interpretation",
        "",
        "- `flat_keyword_only` removes typed memory semantics and event-centric setup.",
        "- `typed_memory_no_event` keeps typed memory writes but removes event trace entries before grading.",
        "- `typed_memory_with_event` keeps typed memory and event traces but skips governance review and workflow outcome feedback.",
        "- `typed_memory_with_governance` adds governance review but still excludes workflow outcome feedback.",
        "- `full_system` is the reference path and should match the default memory-enabled runner.",
    ])
    return "\n".join(lines) + "\n"


def write_ablation_report(output_path: str | Path) -> Path:
    """Run internal ablations and write a separate Markdown report."""
    path = Path(output_path)
    markdown = build_ablation_markdown_report(run_ablation_comparison(print_reports=False))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path


def _overall(reports: dict[str, BenchmarkReport]) -> tuple[int, int, int]:
    total = sum(report.total for report in reports.values())
    passed = sum(report.passed for report in reports.values())
    failed = sum(report.failed for report in reports.values())
    return total, passed, failed


def _case_counter(cases: list[BenchmarkCase], field: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for case in cases:
        record = case_to_record(case)
        if field == "memory_type":
            for memory_type in case.memory_types:
                counter[str(memory_type)] += 1
        elif field == "difficulty":
            counter[str(record["difficulty"] or "unlabeled")] += 1
    return counter


def _dataset_readiness(cases: list[BenchmarkCase]) -> list[tuple[str, int, int, float]]:
    fields = [
        "memory_target",
        "evaluation_task",
        "expected_behavior",
        "ground_truth",
        "scoring_rubric",
        "difficulty",
        "source_anchor",
    ]
    total = len(cases)
    records = [case_to_record(case) for case in cases]
    rows: list[tuple[str, int, int, float]] = []
    for field in fields:
        present = sum(1 for record in records if bool(record.get(field)))
        coverage = (present / total) if total else 0.0
        rows.append((field, present, total, coverage))
    return rows


def _counter_table(counter: Counter[str], label: str) -> list[str]:
    if not counter:
        return [f"No {label.lower()} data."]
    return _named_counter_table(counter, label)


def _named_counter_table(counter: Counter[str], label: str) -> list[str]:
    lines = [
        f"| {label} | Count |",
        "| --- | ---: |",
    ]
    for name, count in sorted(counter.items()):
        lines.append(f"| {name} | {count} |")
    return lines


def _failure_counts(reports: dict[str, BenchmarkReport]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for report in reports.values():
        for name, count in (report.failure_type_counts or {}).items():
            counter[name] += count
    return counter


def _ablation_track_delta_table(ablation_reports: dict[str, dict[str, BenchmarkReport]]) -> list[str]:
    full_reports = ablation_reports.get(ABLATION_FULL_SYSTEM)
    if not full_reports:
        return ["Full-system reference is missing; track delta cannot be computed."]
    lines = [
        "| Mode | Track | Passed | Full Passed | Delta | Failed | Avg Rubric | Trace Completeness |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, reports in ablation_reports.items():
        if mode == ABLATION_FULL_SYSTEM:
            continue
        for track in sorted(set(reports) | set(full_reports)):
            report = reports.get(track)
            full = full_reports.get(track)
            passed = report.passed if report else 0
            failed = report.failed if report else 0
            full_passed = full.passed if full else 0
            delta = passed - full_passed
            avg_rubric = report.average_rubric_score if report else 0.0
            trace = _format_metric(
                report.average_trace_completeness if report else 0.0,
                report.trace_evaluated_cases if report else 0,
            )
            lines.append(
                f"| {mode} | {track} | {passed} | {full_passed} | {delta:+d} | "
                f"{failed} | {avg_rubric:.2f} | {trace} |"
            )
    return lines


def _ablation_failure_type_tables(ablation_reports: dict[str, dict[str, BenchmarkReport]]) -> list[str]:
    lines: list[str] = []
    for mode, reports in ablation_reports.items():
        if mode == ABLATION_FULL_SYSTEM:
            continue
        failure_counts = _failure_counts(reports)
        lines.extend([f"### {mode}", ""])
        if failure_counts:
            lines.extend(_named_counter_table(failure_counts, "Failure Type"))
        else:
            lines.append("No failures.")
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines or ["No ablation failure data."]


def _baseline_delta_table(baseline_reports: dict[str, dict[str, BenchmarkReport]]) -> list[str]:
    memory_reports = baseline_reports.get(BASELINE_MEMORY_ENABLED)
    if not memory_reports:
        return ["Memory-enabled baseline is missing; delta cannot be computed."]
    memory_total, memory_passed, _ = _overall(memory_reports)
    lines = [
        "| Compared Mode | Memory Passed | Compared Passed | Delta Passed |",
        "| --- | ---: | ---: | ---: |",
    ]
    for mode, reports in baseline_reports.items():
        if mode == BASELINE_MEMORY_ENABLED:
            continue
        mode_total, mode_passed, _ = _overall(reports)
        comparable_total = min(memory_total, mode_total)
        delta = memory_passed - mode_passed
        lines.append(f"| {mode} | {memory_passed}/{comparable_total} | {mode_passed}/{comparable_total} | {delta:+d} |")
    if len(lines) == 2:
        return ["No non-memory baseline modes were provided."]
    return lines


def _format_metric(value: float, evaluated_count: int) -> str:
    if evaluated_count == 0:
        return "n/a"
    return f"{value:.2f}"


def _weighted_context_metric(reports: dict[str, BenchmarkReport], attr: str) -> str:
    values = [
        (float(getattr(report, attr)), report.context_evaluated_cases)
        for report in reports.values()
        if report.context_evaluated_cases > 0
    ]
    if not values:
        return "n/a"
    return f"{_weighted_average(values):.2f}"


def _weighted_trace_metric(reports: dict[str, BenchmarkReport]) -> str:
    values = [
        (float(report.average_trace_completeness), report.trace_evaluated_cases)
        for report in reports.values()
        if report.trace_evaluated_cases > 0
    ]
    if not values:
        return "n/a"
    return f"{_weighted_average(values):.2f}"


def _weighted_answer_metric(reports: dict[str, BenchmarkReport], attr: str) -> str:
    values = [
        (float(getattr(report, attr)), report.answer_evaluated_cases)
        for report in reports.values()
        if report.answer_evaluated_cases > 0
    ]
    if not values:
        return "n/a"
    return f"{_weighted_average(values):.2f}"


def _weighted_memory_improvement_metric(reports: dict[str, BenchmarkReport]) -> str:
    values = [
        (float(report.average_memory_improvement), report.memory_improvement_evaluated_cases)
        for report in reports.values()
        if report.memory_improvement_evaluated_cases > 0
    ]
    if not values:
        return "n/a"
    return f"{_weighted_average(values):.2f}"


def _weighted_average(values: list[tuple[float, int]]) -> float:
    total_weight = sum(weight for _, weight in values)
    if total_weight == 0:
        return 0.0
    return sum(value * weight for value, weight in values) / total_weight


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a Markdown benchmark report.")
    parser.add_argument(
        "output",
        nargs="?",
        default=str(Path("benchmarks_runtime") / "benchmark_report.md"),
        help="Path to write the report.",
    )
    parser.add_argument(
        "--no-baselines",
        action="store_true",
        help="Skip baseline comparison and run only the default memory-enabled benchmark.",
    )
    parser.add_argument(
        "--ablation",
        action="store_true",
        help="Generate the separate internal ablation report.",
    )
    parser.add_argument(
        "--critical",
        action="store_true",
        help="Run the bounded P0 critical benchmark gate instead of the full suite.",
    )
    parser.add_argument(
        "--sharded",
        action="store_true",
        help="Run the full report as resumable track shards and aggregate the result.",
    )
    parser.add_argument(
        "--tracks",
        help="Comma-separated benchmark track ids to run, e.g. A,B,J,J-gen. Omit for all tracks.",
    )
    parser.add_argument(
        "--shards",
        help="Semicolon-separated shard groups, e.g. A,B;C;J,J-gen. Implies --sharded.",
    )
    args = parser.parse_args(argv)
    track_ids = _parse_track_ids(args.tracks)
    shard_specs = _parse_shard_specs(args.shards)
    sharded = args.sharded or shard_specs is not None

    if sharded and (args.critical or args.ablation):
        parser.error("--sharded/--shards cannot be combined with --critical or --ablation")

    if args.critical:
        path = write_critical_report(args.output)
    elif args.ablation:
        path = write_ablation_report(args.output)
    elif sharded:
        path = write_sharded_markdown_report(
            args.output,
            include_baselines=not args.no_baselines,
            shards=shard_specs or ([track_ids] if track_ids else None),
        )
    else:
        path = write_markdown_report(args.output, include_baselines=not args.no_baselines, track_ids=track_ids)
    print(f"Wrote benchmark report to {path}")
    return 0


def _parse_track_ids(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    track_ids = [item.strip() for item in raw.split(",") if item.strip()]
    return track_ids or None


def _parse_shard_specs(raw: str | None) -> list[list[str]] | None:
    if raw is None:
        return None
    shards: list[list[str]] = []
    for shard in raw.split(";"):
        track_ids = _parse_track_ids(shard)
        if track_ids:
            shards.append(track_ids)
    return shards or None


if __name__ == "__main__":
    raise SystemExit(main())
