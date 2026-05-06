"""Recall performance baseline — measures latency at different memory scales.

Creates 100, 1,000, and 10,000 active memories, then measures recall latency
and _compute_lexical_stats time. Outputs P50/P95 across 5 repeated runs.

Usage:
    python benchmarks/recall_baseline.py
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory_engine import MemoryCandidate, MemoryEngine, RecallRequest, SourceEvent

_SIZES = [100, 1_000]  # 10_000 skipped — OOM/kill on large SQLite writes; re-enable in isolated runner
_RUNS = 5


def _seed_memories(engine: MemoryEngine, count: int) -> None:
    """Write `count` active memories with varied content."""
    batch_size = 500
    for batch_start in range(0, count, batch_size):
        batch_end = min(batch_start + batch_size, count)
        for i in range(batch_start, batch_end):
            engine.write(
                event=SourceEvent(
                    source_type="message",
                    source_ref=f"perf://seed/{i}",
                    actors=["perf_user"],
                    timestamp="2026-04-30T00:00:00+00:00",
                    content=f"Benchmark memory item number {i} about project planning and architecture decisions.",
                    scope="project",
                    payload={},
                ),
                project_id="perf_proj",
                user_id="perf_user",
                memory_candidates=[
                    MemoryCandidate(
                        memory_type="decision" if i % 3 == 0 else "task_status",
                        title=f"Benchmark decision {i}",
                        summary=f"This is benchmark memory {i} covering project planning topic {i % 50}.",
                        content={"scope": "project"},
                        importance=0.5 + (i % 5) * 0.1,
                        confidence=0.7,
                        evidence=[{"source_ref": f"perf://seed/{i}"}],
                    )
                ],
            )


def _measure_recall(engine: MemoryEngine, query: str) -> dict[str, float]:
    """Measure recall latency and lexical stats time for a single query."""
    # Time the full recall
    t0 = time.perf_counter()
    results = engine.recall(
        RecallRequest(query=query, project_id="perf_proj"),
        limit=10,
    )
    total_ms = (time.perf_counter() - t0) * 1000

    # Time lexical stats separately
    from memory_engine.ranking import _compute_lexical_stats

    all_rows = engine.conn.execute(
        "SELECT id, title, summary, content_json, importance, confidence, created_at, updated_at, "
        "memory_type, scope, project_id, task_id, user_id, memory_layer "
        "FROM memories WHERE status = 'active'"
    ).fetchall()
    t1 = time.perf_counter()
    _compute_lexical_stats(all_rows)
    stats_ms = (time.perf_counter() - t1) * 1000

    return {
        "total_ms": total_ms,
        "stats_ms": stats_ms,
        "result_count": len(results),
        "active_count": len(all_rows),
    }


def _percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]


def main() -> None:
    base_dir = Path(__file__).parent.parent / "benchmarks_runtime"
    if base_dir.exists():
        shutil.rmtree(base_dir)
    base_dir.mkdir()

    print(f"{'Size':>8} | {'Active':>8} | {'Results':>8} | {'P50 (ms)':>10} | {'P95 (ms)':>10} | {'Stats P50':>10} | {'Stats P95':>10}")
    print("-" * 85)

    for size in _SIZES:
        db_path = base_dir / f"perf_{size}.db"
        engine = MemoryEngine(db_path)

        _seed_memories(engine, size)

        queries = [
            "project planning architecture",
            "benchmark decision topic",
            "memory item number",
        ]

        latencies: list[float] = []
        stats_latencies: list[float] = []
        result_counts: list[int] = []

        for _ in range(_RUNS):
            for q in queries:
                m = _measure_recall(engine, q)
                latencies.append(m["total_ms"])
                stats_latencies.append(m["stats_ms"])
                result_counts.append(m["result_count"])

        active_count = engine.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'"
        ).fetchone()[0]

        engine.close()

        p50 = _percentile(latencies, 50)
        p95 = _percentile(latencies, 95)
        sp50 = _percentile(stats_latencies, 50)
        sp95 = _percentile(stats_latencies, 95)

        print(
            f"{size:>8} | {active_count:>8} | {result_counts[0]:>8} | "
            f"{p50:>10.2f} | {p95:>10.2f} | {sp50:>10.2f} | {sp95:>10.2f}"
        )

    # Cleanup
    shutil.rmtree(base_dir, ignore_errors=True)
    print("\nBaseline complete. Results reflect the current engine implementation.")


if __name__ == "__main__":
    main()
