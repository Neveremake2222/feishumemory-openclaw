"""Baseline comparison engine for Track L.

Runs benchmark cases under different memory configurations and computes
comparative metrics to measure memory system value.

Baselines:
  - no_memory: no memory setup, pure context-free recall
  - recent_context_only: only last N memories available
  - memory_enabled: full typed memory with event entries

Usage:
    python -m benchmarks.baseline_comparison
    python -m benchmarks.baseline_comparison --baselines no_memory memory_enabled
    python -m benchmarks.baseline_comparison --export benchmarks_runtime/baseline_comparison.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.runner import (
    BASELINE_MEMORY_ENABLED,
    BASELINE_NO_MEMORY,
    BASELINE_RECENT_CONTEXT_ONLY,
    BenchmarkReport,
    run_all_benchmarks,
    run_baseline_comparison as _run_legacy_baseline,
)


# ---------------------------------------------------------------------------
# Baseline definitions
# ---------------------------------------------------------------------------

BASELINE_DESCRIPTIONS = {
    BASELINE_NO_MEMORY: "No memory state. Pure context-free recall.",
    BASELINE_RECENT_CONTEXT_ONLY: "Only the most recent setup memory available.",
    BASELINE_MEMORY_ENABLED: "Full typed memory with event entries and governance.",
}


# ---------------------------------------------------------------------------
# Comparison runner
# ---------------------------------------------------------------------------

def run_full_baseline_comparison(
    baselines: list[str] | None = None,
    *,
    print_reports: bool = False,
) -> dict[str, dict[str, BenchmarkReport]]:
    """Run all benchmarks under each baseline configuration."""
    selected = baselines or [
        BASELINE_NO_MEMORY,
        BASELINE_RECENT_CONTEXT_ONLY,
        BASELINE_MEMORY_ENABLED,
    ]
    return _run_legacy_baseline(modes=selected, print_reports=print_reports)


# ---------------------------------------------------------------------------
# Comparative analysis
# ---------------------------------------------------------------------------

def analyze_baseline_comparison(
    results: dict[str, dict[str, BenchmarkReport]],
) -> dict[str, Any]:
    """Compute comparative metrics across baseline configurations."""
    memory_reports = results.get(BASELINE_MEMORY_ENABLED, {})
    no_memory_reports = results.get(BASELINE_NO_MEMORY, {})

    if not memory_reports or not no_memory_reports:
        return {"error": "missing baseline reports"}

    # Aggregate per-track deltas
    track_deltas: dict[str, dict[str, Any]] = {}
    for track, mem_report in memory_reports.items():
        no_mem_report = no_memory_reports.get(track)
        if no_mem_report is None:
            continue

        mem_passed = mem_report.passed
        no_mem_passed = no_mem_report.passed
        delta = mem_passed - no_mem_passed

        track_deltas[track] = {
            "memory_passed": mem_passed,
            "no_memory_passed": no_mem_passed,
            "delta": delta,
            "avg_rubric_memory": mem_report.average_rubric_score,
            "avg_rubric_no_memory": no_mem_report.average_rubric_score,
            "avg_latency_memory": mem_report.average_retrieval_latency_ms,
            "avg_latency_no_memory": no_mem_report.average_retrieval_latency_ms,
        }

    # Overall aggregates
    total_mem = sum(r.passed for r in memory_reports.values())
    total_no_mem = sum(r.passed for r in no_memory_reports.values())
    total_cases = sum(r.total for r in memory_reports.values())

    return {
        "total_cases": total_cases,
        "memory_passed": total_mem,
        "no_memory_passed": total_no_mem,
        "overall_delta": total_mem - total_no_mem,
        "memory_improvement_rate": round((total_mem - total_no_mem) / max(total_no_mem, 1), 4),
        "track_deltas": track_deltas,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_comparison_report(
    results: dict[str, dict[str, BenchmarkReport]],
    analysis: dict[str, Any],
) -> None:
    """Print a formatted baseline comparison report."""
    print("\n" + "=" * 70)
    print("Baseline Comparison Report")
    print("=" * 70)

    # Per-baseline summary
    print(f"\n{'Baseline':<30} {'Passed':>8} {'Total':>8} {'Avg Rubric':>12} {'Avg Recall ms':>14}")
    print("-" * 75)
    for mode, reports in results.items():
        total = sum(r.total for r in reports.values())
        passed = sum(r.passed for r in reports.values())
        rubric = sum(r.average_rubric_score * r.total for r in reports.values()) / max(total, 1)
        latency = sum(r.average_retrieval_latency_ms * r.total for r in reports.values()) / max(total, 1)
        desc = BASELINE_DESCRIPTIONS.get(mode, mode)
        label = f"{mode} ({desc[:30]})"
        print(f"{label:<30} {passed:>8} {total:>8} {rubric:>12.4f} {latency:>14.2f}")

    # Delta table
    print(f"\n{'Track':<8} {'Mem':>6} {'No-Mem':>8} {'Delta':>7} {'Rubric Δ':>10} {'Lat Δ':>8}")
    print("-" * 50)
    for track, delta in sorted(analysis.get("track_deltas", {}).items()):
        rubric_delta = delta["avg_rubric_memory"] - delta["avg_rubric_no_memory"]
        lat_delta = delta["avg_latency_memory"] - delta["avg_latency_no_memory"]
        print(
            f"{track:<8} {delta['memory_passed']:>6} {delta['no_memory_passed']:>8} "
            f"{delta['delta']:>+7} {rubric_delta:>+10.4f} {lat_delta:>+8.2f}"
        )

    # Summary
    print(f"\nOverall: {analysis['memory_passed']}/{analysis['total_cases']} (memory) vs "
          f"{analysis['no_memory_passed']}/{analysis['total_cases']} (no-memory)")
    print(f"Delta: {analysis['overall_delta']:+d} cases, "
          f"improvement rate: {analysis['memory_improvement_rate']:.2%}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_comparison(
    output_path: str | Path,
    results: dict[str, dict[str, BenchmarkReport]],
    analysis: dict[str, Any],
) -> int:
    """Export comparison results to JSONL."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        # Per-baseline per-track records
        for mode, reports in results.items():
            for track, report in reports.items():
                record = {
                    "type": "baseline_track_result",
                    "baseline": mode,
                    "track": track,
                    "passed": report.passed,
                    "total": report.total,
                    "failed": report.failed,
                    "avg_rubric": report.average_rubric_score,
                    "avg_recall_ms": report.average_retrieval_latency_ms,
                    "avg_write_ms": report.average_write_latency_ms,
                    "context_precision": report.average_context_precision,
                    "context_recall": report.average_context_recall,
                    "memory_event_rate": report.average_memory_event_rate,
                }
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                fh.write("\n")
                count += 1

        # Analysis record
        fh.write(json.dumps({"type": "comparison_analysis", **analysis}, ensure_ascii=False, sort_keys=True))
        fh.write("\n")
        count += 1
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Baseline Comparison Engine")
    parser.add_argument(
        "--baselines",
        nargs="+",
        default=None,
        choices=["baseline_no_memory", "recent_context_only", "memory_enabled"],
        help="Which baselines to compare (default: all three)",
    )
    parser.add_argument(
        "--export",
        metavar="PATH",
        help="Export comparison results to JSONL at PATH",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-track output",
    )
    args = parser.parse_args(argv)

    results = run_full_baseline_comparison(
        baselines=args.baselines,
        print_reports=False,
    )
    analysis = analyze_baseline_comparison(results)

    if not args.quiet:
        print_comparison_report(results, analysis)

    if args.export:
        count = export_comparison(args.export, results, analysis)
        print(f"Exported {count} records to {args.export}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
