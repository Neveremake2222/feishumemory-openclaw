"""Concurrent Recall Benchmark — tests WAL mode under concurrent reads.

Verifies that multiple concurrent recall requests don't block each other
and that latency remains stable under concurrent load.

Usage:
    python benchmarks/concurrent_recall_benchmark.py
"""

from __future__ import annotations

import random
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory_engine import MemoryCandidate, MemoryEngine, RecallRequest, SourceEvent


def _seed(engine: MemoryEngine, count: int) -> None:
    for i in range(count):
        engine.write(
            event=SourceEvent(
                source_type="message",
                source_ref=f"msg://conc_bench_{i}",
                actors=["eng"],
                timestamp="2026-05-01T00:00:00+00:00",
                content=f"Benchmark memory {i} about concurrent recall testing.",
                scope="project",
            ),
            project_id="proj_conc",
            user_id="eng",
            memory_candidates=[
                MemoryCandidate(
                    memory_type="decision",
                    title=f"Concurrent decision {i}",
                    summary=f"Testing concurrent recall with memory {i}.",
                    content={"scope": "project"},
                    importance=0.7,
                    confidence=0.8,
                    evidence=[{"source_ref": f"msg://conc_bench_{i}"}],
                )
            ],
        )


def _do_recall(db_path: str, query: str) -> float:
    engine = MemoryEngine(db_path)
    try:
        t0 = time.perf_counter()
        engine.recall(RecallRequest(query=query, project_id="proj_conc"), limit=5)
        return (time.perf_counter() - t0) * 1000
    finally:
        engine.close()


def _percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]


def main() -> None:
    tmp_dir = Path("tests_runtime") / "concurrent_bench"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    print("=" * 70)
    print("  Concurrent Recall Benchmark (WAL Mode)")
    print("=" * 70)

    for mem_count in [100, 1000]:
        db_path = str(tmp_dir / f"bench_{mem_count}.db")
        engine = MemoryEngine(db_path)
        _seed(engine, mem_count)

        # Warm up
        _do_recall(db_path, "concurrent recall")
        _do_recall(db_path, "concurrent recall")

        queries = [
            "concurrent recall",
            "benchmark memory",
            "decision testing",
        ]

        for workers in [1, 5, 10]:
            latencies: list[float] = []

            def task(q: str) -> float:
                return _do_recall(db_path, q)

            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = []
                for _ in range(50):
                    q = random.choice(queries)
                    futures.append(ex.submit(task, q))

                for f in as_completed(futures):
                    latencies.append(f.result())

            p50 = _percentile(latencies, 50)
            p95 = _percentile(latencies, 95)
            p99 = _percentile(latencies, 99)

            label = f"{workers}并发" if workers > 1 else "串行"
            print(
                f"  {mem_count}条 {label:>4s} ({len(latencies)}次): "
                f"P50={p50:.2f}ms  P95={p95:.2f}ms  P99={p99:.2f}ms"
            )

        engine.close()
        print()

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("Concurrent benchmark complete.")


if __name__ == "__main__":
    main()
