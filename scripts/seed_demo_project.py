"""Seed a deterministic product-demo project database.

Usage:
    python scripts/seed_demo_project.py
    python scripts/seed_demo_project.py --db tests_runtime/product_demo.sqlite3 --reset
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from feishu_ingest.adapters.fixture import FixtureAdapter
from feishu_ingest.pipeline import run_ingest
from memory_engine import MemoryCandidate, MemoryEngine, SourceEvent
from memory_engine.models import utc_now
from memory_engine.product_api import ProductMemoryView


DEFAULT_DB = Path("tests_runtime/product_demo.sqlite3")
DEFAULT_FIXTURE = Path("tests/fixtures/feishu_ticket_project_30day.jsonl")
DEFAULT_PROJECT_ID = "proj_feishu_ticket_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the project-memory dashboard demo database.")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE), help="Feishu JSONL fixture to replay.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path to write.")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID, help="Project id to summarize after seeding.")
    parser.add_argument("--reset", action="store_true", help="Delete the target demo DB before seeding.")
    parser.add_argument("--summary-json", default="", help="Optional path for a JSON summary.")
    args = parser.parse_args()

    db_path = Path(args.db)
    fixture_path = Path(args.fixture)
    if args.reset:
        _remove_sqlite_files(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with MemoryEngine(db_path) as engine:
        ingest_result = run_ingest(FixtureAdapter(fixture_path), engine)
        synthetic_ids = _ensure_demo_business_memories(engine, args.project_id)
        active_memories = engine.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'"
        ).fetchone()[0]
        event_count = engine.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    view = ProductMemoryView(db_path)
    overview = view.get_project_overview(args.project_id)
    summary = {
        "db": str(db_path),
        "fixture": str(fixture_path),
        "project_id": args.project_id,
        "events_processed": ingest_result.events_processed,
        "events_written": ingest_result.events_written,
        "events_skipped_dup": ingest_result.events_skipped_dup,
        "events_skipped_no_candidate": ingest_result.events_skipped_no_candidate,
        "memory_ids_written": ingest_result.memory_ids,
        "synthetic_business_memory_ids": synthetic_ids,
        "errors": ingest_result.errors,
        "event_count": int(event_count),
        "active_memory_count": int(active_memories),
        "overview": overview,
    }

    if args.summary_json:
        output_path = Path(args.summary_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not ingest_result.errors else 1


def _ensure_demo_business_memories(engine: MemoryEngine, project_id: str) -> list[int]:
    """Add the three business-facing demo facts if the fixture did not already produce them."""
    existing_titles = {
        row["title"]
        for row in engine.conn.execute(
            "SELECT title FROM memories WHERE status = 'active' AND project_id = ?",
            (project_id,),
        ).fetchall()
    }
    event = SourceEvent(
        source_type="message",
        source_ref="demo://product-shell/business-memories",
        actors=["demo_pm"],
        timestamp=utc_now(),
        content="产品演示固定业务记忆：方案 B、接口联调 70%、验收安全说明风险。",
        scope="project",
        payload={"chat_title": "飞书智能工单管理系统 V1.0 项目攻坚群"},
    )
    candidates: list[MemoryCandidate] = []
    if "采用方案 B：WebSocket 长连接" not in existing_titles:
        candidates.append(MemoryCandidate(
            memory_type="decision",
            title="采用方案 B：WebSocket 长连接",
            summary="最终决定采用方案 B：WebSocket 长连接，不采用公网 IP 回调方案。",
            content={
                "scope": "project",
                "decision": "use_websocket",
                "reason": "公网 IP 和域名依赖会拖慢内网调试，WebSocket 更适合验收场景",
                "current": True,
            },
            importance=0.9,
            confidence=0.95,
            evidence=[{"source_ref": "demo://decision/websocket"}],
            tags=["current", "decision"],
        ))
    if "接口联调完成 70%" not in existing_titles:
        candidates.append(MemoryCandidate(
            memory_type="task_status",
            title="接口联调完成 70%",
            summary="当前客户交付项目接口联调完成 70%，核心链路已跑通。",
            content={"scope": "project", "task": "integration", "progress": "70%"},
            importance=0.8,
            confidence=0.9,
            evidence=[{"source_ref": "demo://status/integration-70"}],
            tags=["progress"],
        ))
    if "验收材料缺少安全说明" not in existing_titles:
        candidates.append(MemoryCandidate(
            memory_type="task_status",
            title="验收材料缺少安全说明",
            summary="验收材料还缺最后一版安全说明，预计会影响周五验收。",
            content={
                "scope": "project",
                "risk": "missing_security_note",
                "impact": "影响周五验收",
                "next_action": "补齐安全说明并同步客户",
            },
            importance=0.85,
            confidence=0.9,
            evidence=[{"source_ref": "demo://risk/security-note"}],
            tags=["risk", "next_action"],
        ))
    if not candidates:
        return []
    result = engine.write(event, candidates, project_id=project_id, user_id="demo_pm")
    return list(result.get("memory_ids", []))


def _remove_sqlite_files(db_path: Path) -> None:
    targets = [db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")]
    for target in targets:
        if target.exists() and target.is_file():
            target.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
