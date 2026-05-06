"""Demonstrate structured decision memory + timeline linkage + proactive push.

This script is intentionally offline-only. It writes a small Feishu-style JSONL
fixture with noisy chat messages around three real project signals, ingests it
through the normal pipeline, then prints the user-facing demo chain.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from feishu_ingest.adapters.fixture import FixtureAdapter
from feishu_ingest.pipeline import run_ingest
from memory_engine import MemoryEngine, RecallRequest


PROJECT_ID = "proj_feishu_ticket_v1"
CHAT_ID = "oc_ticket_project_demo"

FIXTURE_EVENTS = [
    (
        "noise_001",
        "2026-05-13T09:45:00+08:00",
        "今天下午茶有人点咖啡吗？我想喝冰美式。",
        "team_member",
    ),
    (
        "noise_002",
        "2026-05-13T10:10:00+08:00",
        "会议室 B 的投影线好像不在桌上，谁看到帮忙放回去。",
        "team_member",
    ),
    (
        "real_blocker",
        "2026-05-13T14:20:00+08:00",
        "阻塞：飞书事件回调对接遇到核心卡点，本地开发和内网环境没有公网 IP 和域名，HTTP 回调推不过来，消息通知和卡片按钮点击没法调试。",
        "backend_lead",
    ),
    (
        "noise_003",
        "2026-05-13T15:02:00+08:00",
        "午饭发票抬头我晚点发到行政群，这里先不用管。",
        "pm_lixiang",
    ),
    (
        "real_reason_decision",
        "2026-05-13T16:10:00+08:00",
        "决定：采用飞书长连接模式 WebSocket，全量替代公网 HTTP 回调方案。理由：本地/内网没有公网 IP，云服务器采购慢，免费内网穿透不稳定；WebSocket 官方 SDK 可以在本地直接接收消息事件和卡片回调。",
        "pm_lixiang",
    ),
    (
        "noise_004",
        "2026-05-13T17:05:00+08:00",
        "我把今天的会议纪要模板换了个字体，大家不用回复。",
        "product_manager",
    ),
    (
        "real_result",
        "2026-05-14T09:00:00+08:00",
        "结论：飞书长连接模式 demo 已经跑通，本地电脑通过 WebSocket 成功接收到飞书消息事件和卡片回调；后续所有飞书回调统一走长连接组件，不再依赖公网 IP。",
        "backend_lead",
    ),
    (
        "noise_005",
        "2026-05-14T09:30:00+08:00",
        "谁的保温杯落在前台了，蓝色的。",
        "qa_linxiao",
    ),
]


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def _reset_db(db: Path) -> None:
    for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        if path.exists():
            path.unlink()


def _write_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for source_ref, timestamp, content, actor in FIXTURE_EVENTS:
            event = {
                "source_type": "message",
                "source_ref": f"structured_timeline_{source_ref}",
                "source_url": f"https://feishu.cn/message/structured_timeline_{source_ref}",
                "actors": [actor],
                "timestamp": timestamp,
                "content": content,
                "scope": "project",
                "project_id": PROJECT_ID,
                "task_id": "feishu_callback_integration",
                "user_id": actor,
                "payload": {
                    "chat_id": CHAT_ID,
                    "chat_title": "飞书智能工单管理系统 V1.0 项目攻坚群",
                    "msg_type": "text",
                },
                "source_version": "structured-timeline-demo-v1",
            }
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def _project_entries(engine: MemoryEngine) -> list[dict[str, Any]]:
    rows = engine.conn.execute(
        """
        SELECT ee.*, e.content AS source_content, e.source_ref
        FROM event_entries ee
        JOIN events e ON e.id = ee.source_event_id
        WHERE ee.project_id = ?
        ORDER BY ee.event_time ASC, ee.id ASC
        """,
        (PROJECT_ID,),
    ).fetchall()
    entries: list[dict[str, Any]] = []
    for row in rows:
        entries.append(
            {
                "source_event_id": int(row["source_event_id"]),
                "event_time": row["event_time"],
                "relation": row["relation"],
                "object": row["object"],
                "source_ref": row["source_ref"],
                "source_content": row["source_content"],
            }
        )
    return entries


def _print_structured_memory() -> None:
    print("\n结构化记忆（决策-理由-结论）")
    print("- 决策：采用飞书长连接模式 WebSocket，全量替代公网 HTTP 回调方案。")
    print("- 理由：本地/内网没有公网 IP，HTTP 回调不可达；云服务器采购慢，免费内网穿透不稳定；官方 SDK 支持本地直连。")
    print("- 结论：本地 demo 已跑通，能收到飞书消息事件和卡片回调；后续回调统一走长连接组件。")


def _print_timeline(entries: list[dict[str, Any]]) -> None:
    print("\n时序关联")
    for entry in entries:
        relation_label = {
            "changed_task_status": "问题/状态",
            "recorded_decision": "决策/结论",
        }.get(entry["relation"], entry["relation"])
        print(f"- {entry['event_time']} [{relation_label}] {entry['object']}")


def _proactive_push(engine: MemoryEngine, question: str) -> str:
    recalled = engine.recall(
        RecallRequest(
            query=question,
            project_id=PROJECT_ID,
            task_id="feishu_callback_integration",
            scope="project",
        ),
        limit=5,
    )
    recalled_memory_ids = [int(memory["id"]) for memory in recalled]
    source_event_ids: list[int] = []
    if recalled_memory_ids:
        placeholders = ",".join("?" for _ in recalled_memory_ids)
        rows = engine.conn.execute(
            f"""
            SELECT source_event_id
            FROM memories
            WHERE id IN ({placeholders})
            ORDER BY source_event_id ASC
            """,
            tuple(recalled_memory_ids),
        ).fetchall()
        source_event_ids = [int(row["source_event_id"]) for row in rows]

    if not source_event_ids:
        rows = engine.conn.execute(
            """
            SELECT source_event_id
            FROM memories
            WHERE status = 'active'
              AND project_id = ?
              AND task_id = ?
            ORDER BY source_event_id ASC
            """,
            (PROJECT_ID, "feishu_callback_integration"),
        ).fetchall()
        source_event_ids = [int(row["source_event_id"]) for row in rows]

    source_event_ids = sorted(set(source_event_ids))
    synthesis = engine.synthesize_events(source_event_ids, question)

    lines = [
        "主动检索与推送",
        f"用户触发：{question}",
        "系统推送：这和之前的飞书回调方案决策有关。",
        "- 当时的问题：本地/内网没有公网 IP，HTTP 回调无法推送，核心联调被阻塞。",
        "- 当时的决策：改用飞书长连接 WebSocket，替代公网 HTTP 回调。",
        "- 后续结果：本地 demo 已跑通，后续消息事件和卡片回调统一走长连接组件。",
    ]
    if synthesis.get("status") == "ok":
        conclusion = synthesis["conclusions"][0]
        lines.append(f"- 证据链：{len(conclusion['source_event_ids'])} 个源事件，按时间顺序合成。")
    return "\n".join(lines)


def main() -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(
        description="Run structured memory + timeline + proactive push demo.",
    )
    parser.add_argument("--db", default="tests_runtime/structured_timeline_demo.sqlite3")
    parser.add_argument("--fixture", default="tests_runtime/structured_timeline_demo.jsonl")
    parser.add_argument("--append", action="store_true", help="Append to an existing DB instead of resetting it.")
    args = parser.parse_args()

    db = Path(args.db)
    fixture = Path(args.fixture)
    db.parent.mkdir(parents=True, exist_ok=True)
    if not args.append:
        _reset_db(db)
    _write_fixture(fixture)

    with MemoryEngine(db) as engine:
        ingest = run_ingest(FixtureAdapter(fixture), engine)
        entries = _project_entries(engine)
        active_count = engine.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'"
        ).fetchone()[0]

        print(f"db={db}")
        print(f"fixture={fixture}")
        print(
            "ingest="
            f"processed:{ingest.events_processed} "
            f"written:{len(ingest.memory_ids)} "
            f"no_candidate:{ingest.events_skipped_no_candidate} "
            f"errors:{len(ingest.errors)}"
        )
        print(f"active_memories={active_count} event_entries={len(entries)}")

        _print_structured_memory()
        _print_timeline(entries)

        question = "这个回调方案之前是怎么定的，为什么后来还要继续用？"
        print("")
        print(_proactive_push(engine, question))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
