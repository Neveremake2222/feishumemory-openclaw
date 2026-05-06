"""Replay a Feishu JSONL fixture into the memory engine and run recall checks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from feishu_ingest.adapters.fixture import FixtureAdapter
from feishu_ingest.pipeline import run_ingest
from memory_engine import MemoryEngine, RecallRequest


DEFAULT_QUERIES = [
    "飞书智能工单系统 核心目标 四大模块",
    "无公网 IP 域名 飞书回调 怎么解决",
    "长连接 WebSocket demo 跑通了吗",
    "第二周进度 后端接口 前端页面 测试用例",
    "第一轮冒烟测试 多少 bug P0 P1 P2",
    "卡片按钮回调 操作失败 原因",
    "第三周 bug 修复率 剩余 P2",
    "正式上线 是否验收通过",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a Feishu fixture and print recall results.")
    parser.add_argument(
        "fixture",
        nargs="?",
        default="tests/fixtures/feishu_ticket_project_30day.jsonl",
        help="Path to Feishu JSONL fixture.",
    )
    parser.add_argument(
        "--db",
        default="tests_runtime/feishu_ticket_project_demo.sqlite3",
        help="SQLite DB path to write.",
    )
    parser.add_argument(
        "--project-id",
        default="proj_feishu_ticket_v1",
        help="Project id used for recall checks.",
    )
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="Extra recall query. Can be passed multiple times.",
    )
    args = parser.parse_args()

    fixture_path = Path(args.fixture)
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with MemoryEngine(db_path) as engine:
        result = run_ingest(FixtureAdapter(fixture_path), engine)
        memory_count = engine.conn.execute("SELECT COUNT(*) FROM memories WHERE status = 'active'").fetchone()[0]
        event_count = engine.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        print(f"fixture={fixture_path}")
        print(f"db={db_path}")
        print(
            "ingest="
            f"processed:{result.events_processed} "
            f"written:{len(result.memory_ids)} "
            f"dup:{result.events_skipped_dup} "
            f"no_candidate:{result.events_skipped_no_candidate} "
            f"errors:{len(result.errors)}"
        )
        print(f"events={event_count} active_memories={memory_count}")
        if result.errors:
            print("errors:")
            for error in result.errors:
                print(f"- {error}")

        queries = list(DEFAULT_QUERIES)
        if args.queries:
            queries.extend(args.queries)
        for query in queries:
            rows = engine.recall(
                RecallRequest(query=query, project_id=args.project_id, scope="project"),
                limit=3,
            )
            print("")
            print(f"query: {query}")
            if not rows:
                print("- no recall result")
                continue
            for row in rows:
                title = str(row.get("title", "")).replace("\n", " ")[:120]
                print(
                    f"- [{row.get('memory_type')}] score={row.get('score', 0):.3f} "
                    f"confidence={row.get('confidence', 0):.2f} {title}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
