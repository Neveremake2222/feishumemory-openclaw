"""Retrieval quality metrics computation for Track J.

Computes Recall@K, Precision@K, NDCG@K, MRR, and latency metrics
for retrieval-only evaluation — separate from agent task evaluation.

Usage:
    python -m benchmarks.retrieval_eval
    python -m benchmarks.retrieval_eval --k-values 1 3 5 10
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.runner import run_track
from benchmarks.cases.track_j import TRACK_J_CASES


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

@dataclass
class RetrievalMetrics:
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    recall_at_10: float
    precision_at_1: float
    precision_at_3: float
    precision_at_5: float
    precision_at_10: float
    ndcg_at_10: float
    mrr: float
    latency_p50_ms: float
    latency_p95_ms: float


def compute_retrieval_metrics(
    retrieved_titles: list[str],
    expected_titles: list[str],
    forbidden_titles: list[str],
    latencies_ms: list[float],
    k_values: list[int] | None = None,
) -> RetrievalMetrics:
    """Compute retrieval quality metrics from a single recall result."""
    if k_values is None:
        k_values = [1, 3, 5, 10]

    expected_set = set(expected_titles)
    forbidden_set = set(forbidden_titles)

    # Recall@K: how many expected items appear in top-K
    recall: dict[int, float] = {}
    for k in k_values:
        top_k = set(retrieved_titles[:k])
        if expected_set:
            recall[k] = len(expected_set & top_k) / len(expected_set)
        else:
            recall[k] = 1.0 if len(top_k) == 0 else 0.0

    # Precision@K: fraction of top-K that is relevant
    precision: dict[int, float] = {}
    for k in k_values:
        top_k = set(retrieved_titles[:k])
        if k > 0:
            precision[k] = len(expected_set & top_k) / k
        else:
            precision[k] = 0.0

    # NDCG@10
    dcg = 0.0
    for i, title in enumerate(retrieved_titles[:10]):
        if title in expected_set:
            dcg += 1.0 / math.log2(i + 2)
    idcg = 0.0
    for i in range(min(len(expected_titles), 10)):
        idcg += 1.0 / math.log2(i + 2)
    ndcg = dcg / idcg if idcg > 0 else 0.0

    # MRR
    mrr = 0.0
    for i, title in enumerate(retrieved_titles):
        if title in expected_set:
            mrr = 1.0 / (i + 1)
            break

    # Latency
    if latencies_ms:
        sorted_lat = sorted(latencies_ms)
        p50 = sorted_lat[len(sorted_lat) // 2]
        p95_idx = int(len(sorted_lat) * 0.95)
        p95 = sorted_lat[min(p95_idx, len(sorted_lat) - 1)]
    else:
        p50, p95 = 0.0, 0.0

    return RetrievalMetrics(
        recall_at_1=round(recall.get(1, 0.0), 4),
        recall_at_3=round(recall.get(3, 0.0), 4),
        recall_at_5=round(recall.get(5, 0.0), 4),
        recall_at_10=round(recall.get(10, 0.0), 4),
        precision_at_1=round(precision.get(1, 0.0), 4),
        precision_at_3=round(precision.get(3, 0.0), 4),
        precision_at_5=round(precision.get(5, 0.0), 4),
        precision_at_10=round(precision.get(10, 0.0), 4),
        ndcg_at_10=round(ndcg, 4),
        mrr=round(mrr, 4),
        latency_p50_ms=round(p50, 2),
        latency_p95_ms=round(p95, 2),
    )


def metrics_to_dict(m: RetrievalMetrics) -> dict[str, float]:
    return {
        "recall_at_1": m.recall_at_1,
        "recall_at_3": m.recall_at_3,
        "recall_at_5": m.recall_at_5,
        "recall_at_10": m.recall_at_10,
        "precision_at_1": m.precision_at_1,
        "precision_at_3": m.precision_at_3,
        "precision_at_5": m.precision_at_5,
        "precision_at_10": m.precision_at_10,
        "ndcg_at_10": m.ndcg_at_10,
        "mrr": m.mrr,
        "latency_p50_ms": m.latency_p50_ms,
        "latency_p95_ms": m.latency_p95_ms,
    }


# ---------------------------------------------------------------------------
# Per-case metrics from benchmark results
# ---------------------------------------------------------------------------

def extract_case_metrics(result: Any) -> dict[str, Any]:
    """Extract retrieval metrics from a CaseResult transcript."""
    transcript = getattr(result, "transcript", None)
    if transcript is None:
        return {}

    recalls = transcript.get("recalls", [])
    if not recalls:
        return {}

    all_expected: set[str] = set()
    all_forbidden: set[str] = set()
    all_retrieved: list[str] = []

    for recall in recalls:
        for title in recall.get("results", []):
            all_retrieved.append(title.get("title", ""))

    assertions = transcript.get("assertions", {})
    expected_titles = assertions.get("expected_titles", [])
    forbidden_titles = assertions.get("forbidden_titles", [])
    all_expected.update(expected_titles)
    all_forbidden.update(forbidden_titles)

    latencies = [r.get("latency_ms", 0.0) for r in recalls]

    metrics = compute_retrieval_metrics(
        all_retrieved,
        list(all_expected),
        list(all_forbidden),
        latencies,
    )
    return metrics_to_dict(metrics)


# ---------------------------------------------------------------------------
# Track J runner
# ---------------------------------------------------------------------------

def run_retrieval_benchmark(
    k_values: list[int] | None = None,
    print_report: bool = True,
) -> dict[str, Any]:
    """Run Track J and compute retrieval quality metrics."""
    if k_values is None:
        k_values = [1, 3, 5, 10]

    from benchmarks.runner import BenchmarkReport

    # Run Track J
    report: BenchmarkReport = run_track(
        TRACK_J_CASES,
        track_label="J",
        baseline_mode="memory_enabled",
    )

    # Compute per-case and aggregate metrics
    case_metrics: list[dict[str, Any]] = []
    for result in report.cases:
        cm = extract_case_metrics(result)
        cm["case_id"] = result.case_id
        cm["capability"] = result.capability
        cm["passed"] = result.passed
        case_metrics.append(cm)

    # Aggregate
    agg = _aggregate_metrics(case_metrics)

    if print_report:
        _print_report(report, case_metrics, agg, k_values)

    return {
        "report": report,
        "case_metrics": case_metrics,
        "aggregate": agg,
    }


def _aggregate_metrics(case_metrics: list[dict[str, Any]]) -> dict[str, float]:
    """Compute weighted average metrics across all cases."""
    def avg(key: str) -> float:
        vals = [m[key] for m in case_metrics if key in m and m[key] > 0]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    def median(key: str) -> float:
        vals = sorted(m[key] for m in case_metrics if key in m and m[key] > 0)
        if not vals:
            return 0.0
        mid = len(vals) // 2
        return round(vals[mid], 4)

    return {
        "avg_recall_at_1": avg("recall_at_1"),
        "avg_recall_at_3": avg("recall_at_3"),
        "avg_recall_at_5": avg("recall_at_5"),
        "avg_recall_at_10": avg("recall_at_10"),
        "avg_precision_at_1": avg("precision_at_1"),
        "avg_precision_at_3": avg("precision_at_3"),
        "avg_precision_at_5": avg("precision_at_5"),
        "avg_precision_at_10": avg("precision_at_10"),
        "avg_ndcg_at_10": avg("ndcg_at_10"),
        "avg_mrr": avg("mrr"),
        "median_latency_p50_ms": median("latency_p50_ms"),
        "median_latency_p95_ms": median("latency_p95_ms"),
    }


def _print_report(
    report: Any,
    case_metrics: list[dict[str, Any]],
    agg: dict[str, float],
    k_values: list[int],
) -> None:
    print("\n" + "=" * 70)
    print("Track J: Retrieval Quality Benchmark")
    print("=" * 70)

    # Summary
    print(f"\nPass rate: {report.passed}/{report.total}")
    print(f"Average retrieval metrics (across {len(case_metrics)} cases):")

    k_header = "".join(f"R@{k:>5}  " for k in k_values)
    print(f"  Recall:   {k_header}")
    k_vals = "".join(f"{agg.get(f'avg_recall_at_{k}', 0.0):>7.2f}  " for k in k_values)
    print(f"           {k_vals}")

    p_header = "".join(f"P@{k:>5}  " for k in k_values)
    print(f"  Precision:{p_header}")
    p_vals = "".join(f"{agg.get(f'avg_precision_at_{k}', 0.0):>7.2f}  " for k in k_values)
    print(f"           {p_vals}")

    print(f"  NDCG@10: {agg.get('avg_ndcg_at_10', 0.0):.4f}")
    print(f"  MRR:     {agg.get('avg_mrr', 0.0):.4f}")
    print(f"  Lat P50: {agg.get('median_latency_p50_ms', 0.0):.2f} ms")
    print(f"  Lat P95: {agg.get('median_latency_p95_ms', 0.0):.2f} ms")

    # Per-case table
    print(f"\n{'Case ID':<10} {'Capability':<30} {'R@1':>5} {'R@3':>5} {'R@5':>5} {'NDCG':>6} {'MRR':>5} {'Pass':>5}")
    print("-" * 75)
    for cm in case_metrics:
        passed = "PASS" if cm.get("passed") else "FAIL"
        print(
            f"{cm.get('case_id', '?'):<10} "
            f"{cm.get('capability', '?')[:30]:<30} "
            f"{cm.get('recall_at_1', 0.0):>5.2f} "
            f"{cm.get('recall_at_3', 0.0):>5.2f} "
            f"{cm.get('recall_at_5', 0.0):>5.2f} "
            f"{cm.get('ndcg_at_10', 0.0):>6.4f} "
            f"{cm.get('mrr', 0.0):>5.4f} "
            f"{passed:>5}"
        )

    print("=" * 70)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_retrieval_metrics(
    output_path: str | Path,
    results: dict[str, Any] | None = None,
) -> int:
    """Export Track J retrieval metrics to JSONL."""
    if results is None:
        results = run_retrieval_benchmark(print_report=False)

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for cm in results.get("case_metrics", []):
            fh.write(json.dumps(cm, ensure_ascii=False, sort_keys=True))
            fh.write("\n")
            count += 1
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Track J Retrieval Quality Benchmark")
    parser.add_argument(
        "--k-values",
        nargs="+",
        type=int,
        default=[1, 3, 5, 10],
        help="K values for Recall@K and Precision@K (default: 1 3 5 10)",
    )
    parser.add_argument(
        "--export",
        metavar="PATH",
        help="Export metrics to JSONL at PATH",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-case table output",
    )
    args = parser.parse_args(argv)

    results = run_retrieval_benchmark(
        k_values=args.k_values,
        print_report=not args.quiet,
    )

    if args.export:
        count = export_retrieval_metrics(args.export, results)
        print(f"Exported {count} records to {args.export}")

    # Return 0 if all passed, 1 otherwise
    return 0 if results["report"].failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
