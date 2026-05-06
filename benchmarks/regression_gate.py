"""Run the stable benchmark regression gate.

Usage:
    python -m benchmarks.regression_gate
    python -m benchmarks.regression_gate --output-dir benchmarks_runtime --min-case-count 93
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from benchmarks.export_dataset import benchmark_cases, case_to_record, export_cases_jsonl
from benchmarks.report import DATASET_VERSION, build_markdown_report
from benchmarks.runner import BenchmarkReport, export_transcripts_jsonl, run_all_benchmarks
from benchmarks.structures import BenchmarkCase


REQUIRED_DATASET_FIELDS = [
    "memory_target",
    "evaluation_task",
    "expected_behavior",
    "ground_truth",
    "scoring_rubric",
    "difficulty",
    "source_anchor",
]


@dataclass
class GateResult:
    passed: bool
    checks: list[str]
    errors: list[str]


def evaluate_regression_gate(
    reports: dict[str, BenchmarkReport],
    cases: list[BenchmarkCase],
    *,
    min_case_count: int = 5000,
) -> GateResult:
    """Evaluate pass/fail checks without running benchmark side effects."""
    checks: list[str] = []
    errors: list[str] = []

    total = sum(report.total for report in reports.values())
    failed = sum(report.failed for report in reports.values())
    if failed:
        errors.append(f"benchmark failures detected: {failed}/{total} failed")
    else:
        checks.append(f"benchmark pass: {total}/{total}")

    if len(cases) < min_case_count:
        errors.append(f"case count below threshold: {len(cases)} < {min_case_count}")
    else:
        checks.append(f"case count: {len(cases)} >= {min_case_count}")

    records = [case_to_record(case) for case in cases]
    missing_fields: list[str] = []
    for record in records:
        for field in REQUIRED_DATASET_FIELDS:
            if not record.get(field):
                missing_fields.append(f"{record['case_id']}:{field}")
    if missing_fields:
        errors.append("dataset readiness missing fields: " + ", ".join(missing_fields[:10]))
    else:
        checks.append("dataset readiness: 100%")

    transcript_cases = sum(1 for report in reports.values() for result in report.cases if result.transcript)
    if transcript_cases != total:
        errors.append(f"transcript coverage incomplete: {transcript_cases}/{total}")
    else:
        checks.append(f"transcript coverage: {transcript_cases}/{total}")

    report_cases = sum(report.total for report in reports.values())
    if report_cases != len(cases):
        errors.append(f"report/case count mismatch: report={report_cases}, cases={len(cases)}")
    else:
        checks.append("report/case count: matched")

    return GateResult(passed=not errors, checks=checks, errors=errors)


def run_regression_gate(
    *,
    output_dir: str | Path = Path("benchmarks_runtime"),
    min_case_count: int = 5000,
) -> GateResult:
    """Run benchmarks, export artifacts, and evaluate the regression gate."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    cases = benchmark_cases()
    reports = run_all_benchmarks(print_reports=False)

    cases_path = out / "benchmark_cases.jsonl"
    report_path = out / "benchmark_report.md"
    transcripts_path = out / "benchmark_transcripts.jsonl"

    case_count = export_cases_jsonl(cases_path, cases=cases)
    transcript_count = export_transcripts_jsonl(transcripts_path, reports=reports)
    markdown = build_markdown_report(reports, cases=cases, dataset_version=DATASET_VERSION)
    report_path.write_text(markdown, encoding="utf-8")

    result = evaluate_regression_gate(reports, cases, min_case_count=min_case_count)
    _check_artifact(result, cases_path, expected_lines=case_count)
    _check_artifact(result, transcripts_path, expected_lines=transcript_count)
    _check_artifact(result, report_path)
    return result


def _check_artifact(result: GateResult, path: Path, *, expected_lines: int | None = None) -> None:
    if not path.exists() or path.stat().st_size == 0:
        result.errors.append(f"artifact missing or empty: {path}")
        result.passed = False
        return
    if expected_lines is not None:
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count != expected_lines:
            result.errors.append(f"artifact line count mismatch: {path} has {line_count}, expected {expected_lines}")
            result.passed = False
            return
    result.checks.append(f"artifact ok: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the benchmark regression gate.")
    parser.add_argument(
        "--output-dir",
        default=str(Path("benchmarks_runtime")),
        help="Directory to write benchmark artifacts.",
    )
    parser.add_argument(
        "--min-case-count",
        type=int,
        default=5000,
        help="Minimum accepted benchmark case count (default: 5000, covers all tracks including J-gen/K/L)",
    )
    args = parser.parse_args(argv)

    result = run_regression_gate(output_dir=args.output_dir, min_case_count=args.min_case_count)
    for check in result.checks:
        print(f"PASS: {check}")
    for error in result.errors:
        print(f"FAIL: {error}")
    if result.passed:
        print("REGRESSION GATE PASSED")
        return 0
    print("REGRESSION GATE FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
