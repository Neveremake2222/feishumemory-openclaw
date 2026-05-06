"""Write+Recall Mix Benchmark — tests cache invalidation pressure.

Measures recall latency before and after writes to quantify the impact
of _lexical_stats_cache invalidation.

Usage:
    python benchmarks/write_recall_mix_benchmark.py
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from memory_engine import MemoryCandidate, MemoryEngine, RecallRequest, SourceEvent


def _hours_ago(hours: float) -> str:
    from datetime import datetime, timedelta, timezone
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.isoformat()


def main() -> None:
    tmp_dir = Path("tests_runtime") / "mix_bench"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    print("=" * 70)
    print("  Write+Recall Mix Benchmark (Cache Invalidation Pressure)")
    print("=" * 70)

    db_path = tmp_dir / "bench.db"
    engine = MemoryEngine(str(db_path))

    # Seed 500 memories
    for i in range(500):
        engine.write(
            event=SourceEvent(
                source_type="message",
                source_ref=f"msg://mix_seed_{i}",
                actors=["eng"],
                timestamp=_hours_ago(i % 168),
                content=f"Seed memory {i} about project design and architecture.",
                scope="project",
            ),
            project_id="proj_mix",
            user_id="eng",
            memory_candidates=[
                MemoryCandidate(
                    memory_type="decision",
                    title=f"Design decision {i}",
                    summary=f"Architecture decision {i} about project planning.",
                    content={"scope": "project"},
                    importance=0.7,
                    confidence=0.8,
                    evidence=[{"source_ref": f"msg://mix_seed_{i}"}],
                )
            ],
        )

    print(f"\nSeeded 500 memories")

    # Warm up cache
    engine.recall(RecallRequest(query="design architecture", project_id="proj_mix"), limit=5)

    # Phase 1: Cold recall (first after write)
    engine._invalidate_lexical_stats_cache()
    cold_latencies: list[float] = []
    for i in range(10):
        engine._invalidate_lexical_stats_cache()
        t0 = time.perf_counter()
        engine.recall(RecallRequest(query="design architecture", project_id="proj_mix"), limit=5)
        cold_latencies.append((time.perf_counter() - t0) * 1000)

    # Phase 2: Warm recall (cache hit)
    warm_latencies: list[float] = []
    for i in range(10):
        t0 = time.perf_counter()
        engine.recall(RecallRequest(query="design architecture", project_id="proj_mix"), limit=5)
        warm_latencies.append((time.perf_counter() - t0) * 1000)

    # Phase 3: Write then immediate recall (cache invalidation)
    mix_latencies: list[float] = []
    for i in range(50):
        # Write one memory (invalidates cache)
        engine.write(
            event=SourceEvent(
                source_type="message",
                source_ref=f"msg://mix_write_{i}",
                actors=["eng"],
                timestamp=_hours_ago(0.5),
                content=f"New update {i} about project design changes.",
                scope="project",
            ),
            project_id="proj_mix",
            user_id="eng",
            memory_candidates=[
                MemoryCandidate(
                    memory_type="task_status",
                    title=f"Progress update {i}",
                    summary=f"Design update {i} about project changes.",
                    content={"scope": "project"},
                    importance=0.6,
                    confidence=0.7,
                    evidence=[{"source_ref": f"msg://mix_write_{i}"}],
                )
            ],
        )
        # Immediate recall (cache cold)
        t0 = time.perf_counter()
        engine.recall(RecallRequest(query="design architecture", project_id="proj_mix"), limit=5)
        mix_latencies.append((time.perf_counter() - t0) * 1000)

    def pct(vals: list[float], p: float) -> float:
        s = sorted(vals)
        return s[min(int(len(s) * p / 100), len(s) - 1)]

    print(f"\n  Cold recall (cache miss, {len(cold_latencies)} runs):")
    print(f"    P50={pct(cold_latencies, 50):.2f}ms  P95={pct(cold_latencies, 95):.2f}ms")

    print(f"\n  Warm recall (cache hit, {len(warm_latencies)} runs):")
    print(f"    P50={pct(warm_latencies, 50):.2f}ms  P95={pct(warm_latencies, 95):.2f}ms")

    print(f"\n  Write→Recall (cache invalidation, {len(mix_latencies)} runs):")
    print(f"    P50={pct(mix_latencies, 50):.2f}ms  P95={pct(mix_latencies, 95):.2f}ms")

    cache_overhead = pct(mix_latencies, 50) - pct(warm_latencies, 50)
    print(f"\n  Cache失效开销: P50 +{cache_overhead:.1f}ms")

    total_memories = engine.conn.execute(
        "SELECT COUNT(*) FROM memories WHERE status = 'active'"
    ).fetchone()[0]
    print(f"\n  最终记忆总数: {total_memories}")

    engine.close()
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("\nMix benchmark complete.")


if __name__ == "__main__":
    main()
