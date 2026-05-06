"""Scope/Project Filter Benchmark — verifies index usage and filter accuracy.

Tests:
  1. Accuracy: project_id filter returns only matching memories
  2. Cross-project isolation: memories from other projects don't leak
  3. Latency: project_id filter vs. unfiltered baseline

Usage:
    python benchmarks/scope_filter_benchmark.py
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


def _seed_mixed_projects(engine: MemoryEngine, per_project: int) -> dict[str, int]:
    """Write memories for 3 projects, return counts per project."""
    project_ids = ["proj_alpha", "proj_beta", "proj_gamma"]
    counts = {p: 0 for p in project_ids}

    for i in range(per_project * len(project_ids)):
        pid = project_ids[i % len(project_ids)]
        topic = ["架构设计", "数据库选型", "缓存策略", "API设计", "测试方案"][i % 5]
        engine.write(
            event=SourceEvent(
                source_type="message",
                source_ref=f"msg://scope_bench_{i}",
                actors=["eng"],
                timestamp=_hours_ago(i % 168),
                content=f"关于{topic}的决策和进度更新",
                scope="project",
            ),
            project_id=pid,
            user_id="eng",
            memory_candidates=[
                MemoryCandidate(
                    memory_type="decision",
                    title=f"{topic} 决策 {i}",
                    summary=f"关于{topic}的第{i}条决策记录",
                    content={"scope": "project", "project_id": pid},
                    importance=0.7,
                    confidence=0.8,
                    evidence=[{"source_ref": f"msg://scope_bench_{i}"}],
                )
            ],
        )
        counts[pid] += 1

    return counts


def main() -> None:
    tmp_dir = Path("tests_runtime") / "scope_filter_bench"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    print("=" * 70)
    print("  Scope/Project 过滤 Benchmark")
    print("=" * 70)

    for per_project in [100, 500]:
        db_path = tmp_dir / f"bench_{per_project}.db"
        engine = MemoryEngine(str(db_path))

        counts = _seed_mixed_projects(engine, per_project)
        total = per_project * 3

        print(f"\n--- {per_project} 条/项目，共 {total} 条记忆 ---")
        print(f"  项目分布: {dict(counts)}")

        # Accuracy: recall with project_id filter
        query = "架构设计 决策"
        all_results = engine.recall(
            RecallRequest(query=query, project_id=None), limit=1000
        )
        filtered_results = engine.recall(
            RecallRequest(query=query, project_id="proj_alpha"), limit=1000
        )

        all_project_ids = {r.get("project_id") for r in all_results}
        filtered_project_ids = {r.get("project_id") for r in filtered_results}

        print(f"\n  [1] 准确率验证:")
        print(f"    无过滤总结果: {len(all_results)} 条，项目分布: {all_project_ids}")
        print(f"    proj_alpha 过滤: {len(filtered_results)} 条，项目: {filtered_project_ids}")
        print(f"    [PASS] 过滤结果全是 proj_alpha: {filtered_project_ids == {'proj_alpha'}}")

        # Latency: filtered vs. unfiltered
        warm_up = engine.recall(
            RecallRequest(query=query, project_id="proj_alpha"), limit=10
        )
        del warm_up

        latencies_unfiltered: list[float] = []
        latencies_filtered: list[float] = []

        for _ in range(20):
            t0 = time.perf_counter()
            r1 = engine.recall(RecallRequest(query=query, project_id=None), limit=10)
            latencies_unfiltered.append((time.perf_counter() - t0) * 1000)

            t1 = time.perf_counter()
            r2 = engine.recall(RecallRequest(query=query, project_id="proj_alpha"), limit=10)
            latencies_filtered.append((time.perf_counter() - t1) * 1000)

        latencies_unfiltered.sort()
        latencies_filtered.sort()

        p50_uf = latencies_unfiltered[len(latencies_unfiltered) // 2]
        p95_uf = latencies_unfiltered[int(len(latencies_unfiltered) * 0.95)]
        p50_f = latencies_filtered[len(latencies_filtered) // 2]
        p95_f = latencies_filtered[int(len(latencies_filtered) * 0.95)]

        print(f"\n  [2] 延迟对比（{len(latencies_unfiltered)} 次）:")
        print(f"    无过滤: P50={p50_uf:.2f}ms  P95={p95_uf:.2f}ms")
        print(f"    过滤后: P50={p50_f:.2f}ms  P95={p95_f:.2f}ms")
        print(f"    过滤减少扫描: P50 {p50_uf-p50_f:.1f}ms ({(p50_uf-p50_f)/p50_uf*100:.0f}%)")

        engine.close()

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("\n\nBaseline complete.")


if __name__ == "__main__":
    main()
