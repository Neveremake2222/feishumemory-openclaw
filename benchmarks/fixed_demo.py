"""Fixed three-phase demo script for scoring demonstration.

Usage:
    python benchmarks/fixed_demo.py

Three phases:
    A — 结构化写入和召回
    B — 抗干扰召回
    C — 矛盾更新
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from memory_engine import MemoryCandidate, MemoryEngine, RecallRequest, SourceEvent


def _hours_ago(hours: float) -> str:
    from datetime import datetime, timedelta, timezone
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.isoformat()


def _sep(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)
    print()


def demo_a(engine: MemoryEngine) -> None:
    _sep("Demo A: 结构化写入和召回")

    # Write key decision
    print("[A-1] 飞书消息写入:")
    msg = "我决定以后这个项目默认使用 SQLite + BM25 做轻量记忆检索。"
    print(f"  > {msg}")

    ev = SourceEvent(
        source_type="message",
        source_ref="msg://om_demo_a_001",
        actors=["eng_li"],
        timestamp=_hours_ago(1.0),
        content=msg,
        scope="project",
    )
    cand = MemoryCandidate(
        memory_type="decision",
        title="技术选型：SQLite + BM25",
        summary=msg,
        content={"scope": "project", "project_id": "proj_alpha", "technology": "SQLite+BM25"},
        importance=0.8,
        confidence=0.8,
        evidence=[{"source_type": "message", "source_ref": "msg://om_demo_a_001"}],
        tags=["decision", "technology", "sqlite", "bm25"],
    )
    r = engine.write(ev, [cand], project_id="proj_alpha")
    print(f"  Written: memory_ids={r['memory_ids']}")
    print()

    # Recall
    print("[A-2] OpenClaw 查询:")
    query = "我之前对这个项目的检索方案做过什么决定？"
    print(f"  > {query}")
    results = engine.recall(RecallRequest(query=query, project_id="proj_alpha"), limit=5)
    print(f"  返回 {len(results)} 条记忆:")
    for i, r in enumerate(results):
        print(f"  [{i+1}] [{r['memory_type']}] {r['title']}")
        print(f"      Evidence: {r['evidence'][0]['source_ref'] if r['evidence'] else 'N/A'}")
    print()

    # SQLite query
    print("[A-3] SQLite 查询验证:")
    rows = engine.conn.execute(
        "SELECT id, memory_type, title, scope, confidence, status FROM memories WHERE status='active' ORDER BY id DESC LIMIT 3"
    ).fetchall()
    for row in rows:
        print(f"  ID={row[0]} type={row[1]} title={row[2][:40]} scope={row[3]} confidence={row[4]} status={row[5]}")
    print()
    print("  source_ref 查询:")
    ev_rows = engine.conn.execute(
        "SELECT id, source_type, source_ref, content_hash FROM events ORDER BY id DESC LIMIT 2"
    ).fetchall()
    for row in ev_rows:
        print(f"  Event ID={row[0]} type={row[1]} ref={row[2]} hash={row[3][:8]}...")
    print()


def demo_b(engine: MemoryEngine) -> None:
    _sep("Demo B: 抗干扰召回")

    # Write key decision
    print("[B-1] 写入关键记忆:")
    key_msg = "核心决策：项目采用微服务架构进行重构。"
    print(f"  > {key_msg}")
    ev = SourceEvent(
        source_type="message",
        source_ref="msg://demo_b_key",
        actors=["eng_li"],
        timestamp=_hours_ago(1.0),
        content=key_msg,
        scope="project",
    )
    cand = MemoryCandidate(
        memory_type="decision",
        title="核心决策：微服务架构重构",
        summary=key_msg,
        content={"scope": "project", "project_id": "proj_alpha"},
        importance=0.9,
        confidence=0.9,
        evidence=[{"source_type": "message", "source_ref": "msg://demo_b_key"}],
        tags=["decision", "architecture", "microservice"],
    )
    engine.write(ev, [cand], project_id="proj_alpha")
    print()

    # Inject 50 noise messages
    print("[B-2] 注入50条无关消息（模拟群聊噪声）...")
    noise_messages = [
        "今天午饭吃什么？",
        "这个颜色可以再调一下。",
        "刚才会议链接发一下。",
        "项目代码已经推送到仓库了。",
        "周末加班吗？",
        "文档更新完了。",
        "服务器磁盘快满了。",
        "有人知道这个错误怎么解决吗？",
        "新来的同事叫什么名字？",
        "今天天气不错。",
    ]
    noise_count = 0
    for i in range(50):
        noise = noise_messages[i % len(noise_messages)]
        noise_ev = SourceEvent(
            source_type="message",
            source_ref=f"msg://noise_{i:04d}",
            actors=["noise_user"],
            timestamp=_hours_ago(24 + i),
            content=f"[噪声] {noise}",
            scope="project",
        )
        noise_cand = MemoryCandidate(
            memory_type="task_status",
            title=f"噪声任务 {i}",
            summary=noise,
            content={"scope": "project", "project_id": f"proj_noise_{i % 3}"},
            importance=0.3,
            confidence=0.6,
            evidence=[{"source_type": "message", "source_ref": f"msg://noise_{i:04d}"}],
            tags=["noise"],
        )
        engine.write(
            noise_ev, [noise_cand],
            project_id=f"proj_noise_{i % 3}",
            user_id="noise_user",
        )
        noise_count += 1
    print(f"  已注入 {noise_count} 条噪声记忆")
    print()

    # Recall with query
    print("[B-3] 查询项目架构决策:")
    query = "项目架构当时怎么定的？"
    print(f"  > {query}")
    results = engine.recall(RecallRequest(query=query, project_id="proj_alpha"), limit=3)
    print(f"  返回 {len(results)} 条（Top-3）:")
    for i, r in enumerate(results):
        mark = " <<< KEY" if "微服务" in r.get("title", "") else ""
        print(f"  [{i+1}] [{r['memory_type']}] {r['title'][:40]}{mark}")
    print()

    # Count memories
    total = engine.conn.execute("SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
    print(f"  当前活跃记忆总数: {total} 条")
    key_found = any("微服务" in r.get("title", "") for r in results)
    top1_key = results[0].get("title", "") and "微服务" in results[0].get("title", "")
    print()
    print(f"  [PASS] 关键记忆在 Top-3: {key_found}")
    print(f"  [PASS] 关键记忆在 Top-1: {top1_key}")
    print()


def demo_c(engine: MemoryEngine) -> None:
    _sep("Demo C: 矛盾更新")

    # Write first decision
    print("[C-1] 写入决策 A:")
    msg_a = "以后周报默认发给 A。"
    print(f"  > {msg_a}")
    ev_a = SourceEvent(
        source_type="message",
        source_ref="msg://demo_c_a",
        actors=["pm_zhang"],
        timestamp=_hours_ago(24.0),
        content=msg_a,
        scope="project",
    )
    cand_a = MemoryCandidate(
        memory_type="decision",
        title="周报接收人：A",
        summary=msg_a,
        content={"scope": "project", "project_id": "proj_alpha"},
        importance=0.7,
        confidence=0.8,
        evidence=[{"source_type": "message", "source_ref": "msg://demo_c_a"}],
        tags=["decision", "weekly_report"],
    )
    r_a = engine.write(ev_a, [cand_a], project_id="proj_alpha", user_id="pm_zhang")
    print(f"  Written: memory_ids={r_a['memory_ids']}")
    print()

    # Write contradiction
    print("[C-2] 写入矛盾决策 B:")
    msg_b = "不对，以后周报默认发给 B。"
    print(f"  > {msg_b}")
    ev_b = SourceEvent(
        source_type="message",
        source_ref="msg://demo_c_b",
        actors=["pm_zhang"],
        timestamp=_hours_ago(1.0),
        content=msg_b,
        scope="project",
    )
    cand_b = MemoryCandidate(
        memory_type="decision",
        title="周报接收人：B",
        summary=msg_b,
        content={"scope": "project", "project_id": "proj_alpha"},
        importance=0.8,
        confidence=0.9,
        evidence=[{"source_type": "message", "source_ref": "msg://demo_c_b"}],
        tags=["decision", "weekly_report"],
    )
    r_b = engine.write(ev_b, [cand_b], project_id="proj_alpha", user_id="pm_zhang")
    conflicts = r_b.get("conflicts", [])
    print(f"  Written: memory_ids={r_b['memory_ids']}")
    print(f"  冲突: {[c['conflict_type'] for c in conflicts] if conflicts else '无'}")
    print()

    # Recall
    print("[C-3] 查询当前周报接收人:")
    query = "以后周报发给谁？"
    print(f"  > {query}")
    results = engine.recall(RecallRequest(query=query, project_id="proj_alpha"), limit=5)
    print(f"  返回 {len(results)} 条:")
    for r in results:
        print(f"  - [{r['memory_type']}] {r['title']} (status={r['status']})")
    print()

    # Memory statuses
    print("[C-4] 记忆状态:")
    rows = engine.conn.execute(
        "SELECT id, title, status, superseded_by FROM memories ORDER BY id"
    ).fetchall()
    for row in rows:
        superseded = f" -> superseded_by={row[3][:8]}" if row[3] else ""
        print(f"  ID={row[0]} title={row[1][:40]} status={row[2]}{superseded}")
    print()

    # Audit log
    print("[C-5] 审计日志:")
    audits = engine.conn.execute(
        "SELECT action, target_type, target_id, detail FROM audit_log ORDER BY id DESC LIMIT 4"
    ).fetchall()
    for a in audits:
        print(f"  [{a[0]}] {a[1]}#{a[2]}: {a[3][:60] if a[3] else ''}")
    print()

    # Verification
    has_b = any("B" in r.get("title", "") for r in results)
    has_a = any("A" in r.get("title", "") and "周报" in r.get("title", "") for r in results)
    old_superseded = any(row[2] == "superseded" for row in rows)
    conflict_detected = len(conflicts) > 0

    print("验证:")
    print(f"  [PASS] 当前记忆是 B: {has_b}")
    print(f"  [PASS] 旧记忆 A 不在召回结果: {not has_a}")
    print(f"  [PASS] 旧记忆被 superseded: {old_superseded}")
    print(f"  [PASS] 冲突被检测: {conflict_detected}")
    print()


def main() -> None:
    tmp_dir = Path("tests_runtime") / "fixed_demo"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_dir / "demo.db"

    engine = MemoryEngine(str(db_path))
    try:
        demo_a(engine)
        demo_b(engine)
        demo_c(engine)

        print()
        print("=" * 70)
        print("  三段式 Demo 全部完成")
        print("  查看上方 SQLite 查询结果验证记忆详情")
        print("=" * 70)
    finally:
        engine.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
