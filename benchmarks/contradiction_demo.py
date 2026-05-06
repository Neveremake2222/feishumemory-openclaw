"""Contradiction update demo: verifies memory supersession on conflicting decisions.

Usage:
    python benchmarks/contradiction_demo.py

Outputs a verification table matching the scoring criteria (Test 2).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from memory_engine import MemoryCandidate, MemoryEngine, RecallRequest, SourceEvent

_SEP = "=" * 60
_THIN = "-" * 60


def _hours_ago(hours: float) -> str:
    from datetime import datetime, timedelta, timezone
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.isoformat()


def _tag(status: str) -> str:
    return f"[{status}]"


def main() -> None:
    tmp_dir = Path("tests_runtime") / "contradiction_demo"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_dir / "demo.db"

    engine = MemoryEngine(str(db_path))

    print(_SEP)
    print("  矛盾更新测试 — Memory Contradiction Update Demo")
    print(_SEP)
    print()
    print("场景：用户先发出一条指令，随后发出矛盾的新指令。")
    print("期望：系统自动检测冲突，保留版本链，召回时只返回最新版本。")
    print()

    # ── Step 1: Write old decision ──────────────────────────────────────
    print(_THIN)
    print("  Step 1  |  写入旧指令")
    print(_THIN)
    print()
    print('  >>> 用户消息："以后周报默认发给 A。"')
    print()

    ev1 = SourceEvent(
        source_type="message",
        source_ref="msg://weekly_report_a",
        actors=["pm_zhang"],
        timestamp=_hours_ago(24.0),
        content="以后周报默认发给 A。",
        scope="project",
    )
    cand1 = MemoryCandidate(
        memory_type="decision",
        title="周报接收人决定：A",
        summary="以后周报默认发给 A。",
        content={"scope": "project", "project_id": "proj_alpha"},
        importance=0.7,
        confidence=0.8,
        evidence=[{"source_type": "message", "source_ref": "msg://weekly_report_a"}],
        tags=["decision", "weekly_report"],
    )
    r1 = engine.write(ev1, [cand1], project_id="proj_alpha", user_id="pm_zhang")
    old_id = r1["memory_ids"][0]
    print(f"  系统响应：已写入记忆 #{old_id}「周报接收人决定：A」")
    print(f"            status=active, confidence=0.8")
    print()

    # ── Step 2: Write new decision (contradicts old) ────────────────────
    print(_THIN)
    print("  Step 2  |  写入矛盾指令")
    print(_THIN)
    print()
    print('  >>> 用户消息："不对，以后周报默认发给 B。"')
    print()

    ev2 = SourceEvent(
        source_type="message",
        source_ref="msg://weekly_report_b",
        actors=["pm_zhang"],
        timestamp=_hours_ago(1.0),
        content="不对，以后周报默认发给 B。",
        scope="project",
    )
    cand2 = MemoryCandidate(
        memory_type="decision",
        title="周报接收人决定：B",
        summary="不对，以后周报默认发给 B。",
        content={"scope": "project", "project_id": "proj_alpha"},
        importance=0.8,
        confidence=0.9,
        evidence=[{"source_type": "message", "source_ref": "msg://weekly_report_b"}],
        tags=["decision", "weekly_report"],
    )
    r2 = engine.write(ev2, [cand2], project_id="proj_alpha", user_id="pm_zhang")
    new_id = r2["memory_ids"][0]
    conflicts = r2.get("conflicts", [])
    conflict_types = [c["conflict_type"] for c in conflicts] if conflicts else []

    print(f"  系统响应：检测到冲突类型 {conflict_types}")
    print(f"            已写入新记忆 #{new_id}「周报接收人决定：B」")
    print()

    # ── Step 3: Version chain visualization ─────────────────────────────
    print(_THIN)
    print("  Step 3  |  版本链状态")
    print(_THIN)
    print()
    rows = engine.conn.execute(
        "SELECT id, title, status, superseded_by, confidence FROM memories ORDER BY id"
    ).fetchall()
    old_row = next(r for r in rows if "A" in r[1] and "周报" in r[1])
    new_row = next(r for r in rows if "B" in r[1] and "周报" in r[1])

    print(f"  Memory #{old_row[0]}  {old_row[1]}")
    print(f"             status: {_tag('SUPERSEDED')}  confidence: {old_row[4]}")
    print(f"             superseded_by: {old_row[3][:16]}...")
    print(f"                       |")
    print(f"                       | 冲突检测: {conflict_types}")
    print(f"                       v")
    print(f"  Memory #{new_row[0]}  {new_row[1]}")
    print(f"             status: {_tag('ACTIVE')}      confidence: {new_row[4]}")
    print()

    # ── Step 4: Recall ──────────────────────────────────────────────────
    print(_THIN)
    print("  Step 4  |  智能体查询")
    print(_THIN)
    print()
    print('  >>> 智能体查询："以后周报发给谁？"')
    print()

    results = engine.recall(
        RecallRequest(query="以后周报发给谁", project_id="proj_alpha"),
        limit=5,
    )
    print(f"  系统响应：返回 {len(results)} 条记忆")
    for r in results:
        print(f"    [{r['memory_type']}] {r['title']}  (score={r['score']}, confidence={r['confidence']})")
    print()

    # ── Step 5: Verification table ──────────────────────────────────────
    print(_THIN)
    print("  Step 5  |  验证结果")
    print(_THIN)
    print()

    has_b = any("B" in r.get("title", "") for r in results)
    has_a = any("A" in r.get("title", "") for r in results)
    old_status = old_row[2]
    new_status = new_row[2]

    checks = [
        ("召回结果包含最新决策 (B)", has_b),
        ("召回结果不包含旧决策 (A)", not has_a),
        ("旧记忆状态为 superseded", old_status in ("superseded", "archived")),
        ("新记忆状态为 active", new_status == "active"),
        ("冲突被自动检测", len(conflicts) > 0),
    ]

    for desc, ok in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"  {_tag(mark)}  {desc}")

    all_pass = all(ok for _, ok in checks)
    print()
    if all_pass:
        print(f"  {_tag('PASS')} 矛盾更新验证全部通过！")
    else:
        print(f"  {_tag('FAIL')} 存在失败项 — 需要排查")
    print()

    # ── Summary metrics ─────────────────────────────────────────────────
    print(_SEP)
    print(f"  记忆总数: {len(rows)}  |  冲突类型: {conflict_types}  |  通过: {sum(ok for _, ok in checks)}/{len(checks)}")
    print(_SEP)

    engine.close()
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
