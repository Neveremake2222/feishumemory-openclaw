"""Scale benchmark runner for Track K.

Tests memory system behavior under increasing scale:
  K-100    (100 memories)
  K-1k     (1,000 memories)
  K-5k     (5,000 memories)
  K-10k    (10,000 memories)
  K-10k-H  (10,000 memories + high interference)

Usage:
    python -m benchmarks.scale_eval
    python -m benchmarks.scale_eval --scale 1000
    python -m benchmarks.scale_eval --export benchmarks_runtime/scale_results.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory_engine import MemoryCandidate, MemoryEngine, RecallRequest, SourceEvent


# ---------------------------------------------------------------------------
# Scale levels
# ---------------------------------------------------------------------------

SCALE_LEVELS = {
    "K-100": 100,
    "K-1k": 1000,
    "K-5k": 5000,
    "K-10k": 10000,
    "K-10k-H": 10000,  # 10k with high interference
}


# ---------------------------------------------------------------------------
# Noise memory generation
# ---------------------------------------------------------------------------

_TOPICS = [
    "project meeting", "code review", "deployment", "bug fix", "feature request",
    "database migration", "API design", "documentation", "testing", "monitoring",
    "security audit", "performance optimization", "CI/CD pipeline", "infrastructure",
    "user feedback", "stakeholder update", "sprint planning", "retrospective",
    "tech talk", "architecture decision",
]

_MEMBERS = [
    "zhang", "li", "wang", "zhao", "chen", "liu", "wu", "lin",
    "huang", "zhou", "xu", "sun", "ma", "zhu", "hu",
]


def _generate_noise_memories(
    count: int,
    project_id: str = "proj_noise",
    interference: bool = False,
) -> list[tuple[SourceEvent, MemoryCandidate]]:
    """Generate noise memories for scale benchmark."""
    results = []
    for i in range(count):
        topic = random.choice(_TOPICS)
        member = random.choice(_MEMBERS)
        event = SourceEvent(
            source_type="message",
            source_ref=f"noise://{i}",
            actors=[member],
            timestamp=_hours_ago(random.randint(1, 720)),
            content=f"[{member}] {topic} discussion #{i}: internal notes from {topic} session.",
            scope="project",
        )
        memory = MemoryCandidate(
            memory_type="semantic",
            title=f"{topic.title()} Note {i}",
            summary=f"Internal note about {topic} from {member}. Item {i} in the noise set.",
            content={
                "kind": "noise",
                "topic": topic,
                "item": i,
                "interference": interference,
            },
            importance=random.uniform(0.1, 0.4),
            confidence=random.uniform(0.4, 0.7),
            evidence=[{"source_ref": f"noise://{i}"}],
            tags=[topic, "noise"],
        )
        results.append((event, memory))
    return results


# ---------------------------------------------------------------------------
# Target memory seeds (20 core memories for recall)
# ---------------------------------------------------------------------------

_TARGET_MEMORIES: list[tuple[SourceEvent, MemoryCandidate]] = []


def _init_target_memories() -> list[tuple[SourceEvent, MemoryCandidate]]:
    global _TARGET_MEMORIES
    if _TARGET_MEMORIES:
        return _TARGET_MEMORIES

    targets = [
        ("Project Alpha launch date", "June 30, 2026"),
        ("Project Alpha tech stack", "FastAPI + React"),
        ("Project Alpha security constraint", "OAuth2 + API Gateway"),
        ("Project Alpha rate limit", "1000 req/min"),
        ("Project Alpha architecture", "Modular Monolith"),
        ("Project Alpha database", "PostgreSQL 15"),
        ("Project Alpha deployment", "Kubernetes + Helm"),
        ("Project Alpha CI/CD", "GitHub Actions"),
        ("Project Alpha testing", "Pytest + Playwright"),
        ("Project Alpha monitoring", "Prometheus + Grafana"),
        ("Project Alpha API style", "REST + OpenAPI"),
        ("Project Alpha auth", "JWT + Refresh tokens"),
        ("Project Alpha cache", "Redis 7"),
        ("Project Alpha queue", "RabbitMQ"),
        ("Project Alpha search", "Elasticsearch 8"),
        ("Project Alpha storage", "S3-compatible object storage"),
        ("Project Alpha CDN", "CloudFlare"),
        ("Project Alpha logging", "ELK stack"),
        ("Project Alpha tracing", "Jaeger"),
        ("Project Alpha feature flags", "Unleash"),
    ]
    for i, (title_prefix, value) in enumerate(targets):
        event = SourceEvent(
            source_type="message",
            source_ref=f"target://{i}",
            actors=["pm_zhang"],
            timestamp=_hours_ago(24 * (i + 1)),
            content=f"[pm_zhang] {title_prefix}: {value}.",
            scope="project",
        )
        memory = MemoryCandidate(
            memory_type="decision",
            title=f"Alpha {title_prefix}: {value}",
            summary=f"Alpha {title_prefix.lower()} is confirmed as {value}.",
            content={"project": "proj_alpha", "decision": title_prefix.lower(), "value": value},
            importance=0.8,
            confidence=0.9,
            evidence=[{"source_ref": f"target://{i}"}],
            tags=["project_alpha", title_prefix.lower().replace(" ", "_")],
        )
        _TARGET_MEMORIES.append((event, memory))
    return _TARGET_MEMORIES


# ---------------------------------------------------------------------------
# Core query set (10 queries for recall)
# ---------------------------------------------------------------------------

_QUERY_SET = [
    "Project Alpha tech stack",
    "Project Alpha deployment method",
    "Project Alpha API authentication",
    "Project Alpha monitoring setup",
    "Project Alpha database",
    "Project Alpha launch date",
    "Project Alpha testing framework",
    "Project Alpha security constraint",
    "Project Alpha CI/CD pipeline",
    "Project Alpha caching strategy",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hours_ago(hours: float) -> str:
    from datetime import datetime, timedelta, timezone
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Scale benchmark runner
# ---------------------------------------------------------------------------

@dataclass
class ScaleResult:
    scale_label: str
    scale_level: int
    interference: bool
    target_count: int
    noise_count: int
    total_memories: int
    build_time_ms: float
    avg_recall_latency_ms: float
    p50_recall_ms: float
    p95_recall_ms: float
    p99_recall_ms: float
    avg_recall_at_5: float
    avg_precision_at_5: float
    reachable_ratio: float


@dataclass
class ScaleMetrics:
    total_cases: int
    results: list[ScaleResult]
    decay_analysis: dict[str, Any]


def run_single_scale(
    scale_label: str,
    total_memories: int,
    interference: bool = False,
    runs_per_query: int = 5,
) -> ScaleResult:
    """Run a single scale level benchmark."""
    import tempfile

    # Init DB
    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "scale.db"

    engine = MemoryEngine(db_path)

    target_memories = _init_target_memories()
    noise_memories = _generate_noise_memories(
        total_memories - len(target_memories),
        interference=interference,
    )

    # Write phase: measure build time
    write_start = time.perf_counter()
    target_ids: list[int] = []
    for event, memory in target_memories:
        result = engine.write(
            event=event,
            memory_candidates=[memory],
            project_id="proj_alpha",
            user_id="pm_zhang",
        )
        target_ids.extend(result["memory_ids"])

    noise_ids: list[int] = []
    for event, memory in noise_memories:
        result = engine.write(
            event=event,
            memory_candidates=[memory],
            project_id="proj_noise",
            user_id=random.choice(_MEMBERS),
        )
        noise_ids.extend(result["memory_ids"])

    write_time = (time.perf_counter() - write_start) * 1000

    # Read phase: measure recall latency
    all_latencies: list[float] = []
    all_recalls: list[list[dict[str, Any]]] = []

    for _ in range(runs_per_query):
        for query in _QUERY_SET:
            start = time.perf_counter()
            results = engine.recall(RecallRequest(query=query, project_id="proj_alpha"), limit=10)
            latency_ms = (time.perf_counter() - start) * 1000
            all_latencies.append(latency_ms)
            all_recalls.append(results)

    # Compute metrics
    sorted_lat = sorted(all_latencies)
    p50 = sorted_lat[len(sorted_lat) // 2]
    p95_idx = int(len(sorted_lat) * 0.95)
    p99_idx = int(len(sorted_lat) * 0.99)
    p95 = sorted_lat[min(p95_idx, len(sorted_lat) - 1)]
    p99 = sorted_lat[min(p99_idx, len(sorted_lat) - 1)]

    # Recall@5 and Precision@5 (average across all runs)
    target_titles = {mem[1].title for mem in target_memories}
    recall_scores: list[float] = []
    precision_scores: list[float] = []

    for results in all_recalls:
        retrieved_titles = [r["title"] for r in results[:5]]
        retrieved_set = set(retrieved_titles)
        if target_titles:
            recall = len(retrieved_set & target_titles) / len(target_titles)
        else:
            recall = 1.0 if not retrieved_set else 0.0
        precision = len(retrieved_set & target_titles) / max(5, 1)
        recall_scores.append(recall)
        precision_scores.append(precision)

    avg_recall = statistics.mean(recall_scores)
    avg_precision = statistics.mean(precision_scores)

    # Reachable ratio: how many target memories were ever recalled in top-10
    recalled_targets = set()
    for results in all_recalls:
        for r in results[:10]:
            if r["title"] in target_titles:
                recalled_targets.add(r["title"])
    reachable_ratio = len(recalled_targets) / len(target_titles) if target_titles else 0.0

    engine.close()

    # Cleanup
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    return ScaleResult(
        scale_label=scale_label,
        scale_level=total_memories,
        interference=interference,
        target_count=len(target_memories),
        noise_count=len(noise_memories),
        total_memories=total_memories,
        build_time_ms=round(write_time, 2),
        avg_recall_latency_ms=round(statistics.mean(all_latencies), 2),
        p50_recall_ms=round(p50, 2),
        p95_recall_ms=round(p95, 2),
        p99_recall_ms=round(p99, 2),
        avg_recall_at_5=round(avg_recall, 4),
        avg_precision_at_5=round(avg_precision, 4),
        reachable_ratio=round(reachable_ratio, 4),
    )


def run_scale_benchmark(
    scale_levels: list[str] | None = None,
    print_report: bool = True,
) -> dict[str, Any]:
    """Run the full scale benchmark across all configured levels."""
    if scale_levels is None:
        scale_levels = list(SCALE_LEVELS.keys())

    results: list[ScaleResult] = []
    for label in scale_levels:
        total = SCALE_LEVELS[label]
        interference = label.endswith("-H")
        if print_report:
            print(f"  Running {label} ({total} memories, interference={interference})...")
        result = run_single_scale(label, total, interference=interference)
        results.append(result)

    # Decay analysis
    decay = _analyze_decay(results)

    if print_report:
        _print_report(results, decay)

    return {
        "total_cases": len(results),
        "results": [_scale_result_to_dict(r) for r in results],
        "decay_analysis": decay,
    }


def _analyze_decay(results: list[ScaleResult]) -> dict[str, Any]:
    """Analyze how metrics decay as scale increases."""
    if not results:
        return {}

    baseline = results[0]  # K-100
    decay: dict[str, Any] = {}

    for r in results[1:]:
        label = r.scale_label
        decay[label] = {
            "latency_ratio": round(r.avg_recall_latency_ms / baseline.avg_recall_latency_ms, 2),
            "recall_ratio": round(r.avg_recall_at_5 / baseline.avg_recall_at_5, 2),
            "precision_ratio": round(r.avg_precision_at_5 / baseline.avg_precision_at_5, 2),
            "reachable_ratio": round(r.reachable_ratio, 4),
            "p95_latency_ratio": round(r.p95_recall_ms / baseline.p95_recall_ms, 2),
        }

    return {
        "baseline_label": baseline.scale_label,
        "baseline_metrics": _scale_result_to_dict(baseline),
        "decay_by_scale": decay,
        "is_acceptable": all(
            d["recall_ratio"] > 0.6 and d["latency_ratio"] < 10.0
            for d in decay.values()
        ),
    }


def _scale_result_to_dict(r: ScaleResult) -> dict[str, Any]:
    return {
        "scale_label": r.scale_label,
        "scale_level": r.scale_level,
        "interference": r.interference,
        "target_count": r.target_count,
        "noise_count": r.noise_count,
        "total_memories": r.total_memories,
        "build_time_ms": r.build_time_ms,
        "avg_recall_latency_ms": r.avg_recall_latency_ms,
        "p50_recall_ms": r.p50_recall_ms,
        "p95_recall_ms": r.p95_recall_ms,
        "p99_recall_ms": r.p99_recall_ms,
        "avg_recall_at_5": r.avg_recall_at_5,
        "avg_precision_at_5": r.avg_precision_at_5,
        "reachable_ratio": r.reachable_ratio,
    }


def _print_report(results: list[ScaleResult], decay: dict[str, Any]) -> None:
    print("\n" + "=" * 70)
    print("Track K: Scale Benchmark")
    print("=" * 70)
    print(f"\n{'Scale':<10} {'Level':>6} {'Build ms':>10} {'P50 ms':>8} {'P95 ms':>8} {'R@5':>6} {'P@5':>6} {'Reach':>6}")
    print("-" * 70)
    for r in results:
        print(
            f"{r.scale_label:<10} {r.scale_level:>6} "
            f"{r.build_time_ms:>10.1f} {r.avg_recall_latency_ms:>8.2f} "
            f"{r.p95_recall_ms:>8.2f} {r.avg_recall_at_5:>6.4f} "
            f"{r.avg_precision_at_5:>6.4f} {r.reachable_ratio:>6.4f}"
        )

    print("\nDecay Analysis (vs K-100 baseline):")
    print(f"{'Scale':<10} {'Lat Ratio':>10} {'R@5 Ratio':>10} {'P@5 Ratio':>10} {'Reach':>8}")
    print("-" * 50)
    for label, d in decay.get("decay_by_scale", {}).items():
        print(
            f"{label:<10} {d['latency_ratio']:>10.2f} "
            f"{d['recall_ratio']:>10.2f} {d['precision_ratio']:>10.2f} "
            f"{d['reachable_ratio']:>8.4f}"
        )
    print("=" * 70)


# ---------------------------------------------------------------------------
# Track K case definitions
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Track K case definitions
# ---------------------------------------------------------------------------

_TRACK_K_CASES: list[dict[str, Any]] = [
    {"case_id": "K-100", "scale_level": 100, "interference": False},
    {"case_id": "K-1k", "scale_level": 1000, "interference": False},
    {"case_id": "K-5k", "scale_level": 5000, "interference": False},
    {"case_id": "K-10k", "scale_level": 10000, "interference": False},
    {"case_id": "K-10k-H", "scale_level": 10000, "interference": True},
]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_scale_results(
    output_path: str | Path,
    results: dict[str, Any] | None = None,
) -> int:
    """Export scale benchmark results to JSONL."""
    if results is None:
        results = run_scale_benchmark(print_report=False)

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in results.get("results", []):
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True))
            fh.write("\n")
            count += 1
        # Write decay analysis
        fh.write(json.dumps({"type": "decay_analysis", **results.get("decay_analysis", {})}, ensure_ascii=False, sort_keys=True))
        fh.write("\n")
        count += 1
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Track K Scale Benchmark")
    parser.add_argument(
        "--scale",
        choices=list(SCALE_LEVELS.keys()) + ["all"],
        default="all",
        help="Which scale level(s) to run (default: all)",
    )
    parser.add_argument(
        "--export",
        metavar="PATH",
        help="Export results to JSONL at PATH",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output",
    )
    args = parser.parse_args(argv)

    if args.scale == "all":
        scale_levels = list(SCALE_LEVELS.keys())
    else:
        scale_levels = [args.scale]

    results = run_scale_benchmark(
        scale_levels=scale_levels,
        print_report=not args.quiet,
    )

    if args.export:
        count = export_scale_results(args.export, results)
        print(f"Exported {count} records to {args.export}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
