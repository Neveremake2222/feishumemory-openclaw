"""Demonstrate implicit learning -> rule confirmation -> proactive service."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from feishu_ingest.adapters.fixture import FixtureAdapter
from feishu_ingest.lark_ws_ingest_daemon import _push_preference_reminder
from feishu_ingest.models import FeishuEvent
from feishu_ingest.pipeline import run_ingest
from memory_engine import MemoryEngine, Scope


IMPLICIT_FIXTURE_LINES = [
    "please use markdown headings for weekly ticket project notes",
    "please use markdown table with smoke test defect counts",
    "please use bullet checklist for release rehearsal",
    "please keep markdown list for launch acceptance items",
    "please use bullet summary for callback integration notes",
]


class CaptureReplyClient:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send_text(self, chat_id: str, text: str, parent_id: str | None = None) -> bool:
        _ = (chat_id, parent_id)
        self.texts.append(text)
        return True


def write_implicit_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for index, content in enumerate(IMPLICIT_FIXTURE_LINES, start=1):
            fh.write(
                (
                    '{"source_type":"message",'
                    f'"source_ref":"implicit_pref_{index}",'
                    '"source_url":"https://feishu.cn/message/implicit_pref",'
                    '"actors":["pm_lixiang"],'
                    f'"timestamp":"2026-05-0{index}T09:00:00+08:00",'
                    f'"content":"{content}",'
                    '"scope":"project",'
                    '"project_id":"proj_feishu_ticket_v1",'
                    '"task_id":"demo_implicit",'
                    '"user_id":"pm_lixiang",'
                    '"payload":{"chat_id":"oc_ticket_project_demo","chat_title":"飞书智能工单管理系统 V1.0 项目攻坚群","msg_type":"text"},'
                    '"source_version":"implicit-demo-v1"}\n'
                )
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run implicit learning chain demo.")
    parser.add_argument("--db", default="tests_runtime/implicit_learning_demo.sqlite3")
    parser.add_argument("--fixture", default="tests_runtime/implicit_learning_demo.jsonl")
    args = parser.parse_args()

    fixture = Path(args.fixture)
    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    write_implicit_fixture(fixture)

    with MemoryEngine(db) as engine:
        ingest = run_ingest(FixtureAdapter(fixture), engine)
        observations = engine.conn.execute(
            "SELECT id, title FROM memories WHERE content_json LIKE '%implicit_preference_observation%'"
        ).fetchall()
        review = engine.review(user_id="pm_lixiang", project_id="proj_feishu_ticket_v1")
        candidate_ids = review["preference_candidates"]
        stable_id = None
        if candidate_ids:
            stable_id = engine.confirm_preference_candidate(candidate_ids[0], user_id="pm_lixiang")["stable_preference_id"]

        reply_client = CaptureReplyClient()
        trigger_event = FeishuEvent(
            source_type="message",
            source_ref="implicit_pref_trigger",
            source_url=None,
            actors=["pm_lixiang"],
            timestamp="2026-05-20T10:00:00+08:00",
            content="current task structured output for bug report",
            scope=Scope.PROJECT,
            project_id="proj_feishu_ticket_v1",
            task_id="demo_implicit",
            user_id="pm_lixiang",
            payload={"chat_id": "oc_ticket_project_demo", "sender_type": "user"},
        )
        _push_preference_reminder(engine, trigger_event, "oc_ticket_project_demo", reply_client)

        print(f"db={db}")
        print(f"ingest=processed:{ingest.events_processed} written:{len(ingest.memory_ids)} errors:{len(ingest.errors)}")
        print(f"implicit_observations={len(observations)}")
        print(f"preference_candidates={candidate_ids}")
        print(f"stable_preference_id={stable_id}")
        print("proactive_service:")
        if reply_client.texts:
            for text in reply_client.texts:
                print(text)
        else:
            print("- no reminder")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
