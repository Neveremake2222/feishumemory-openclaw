from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from memory_engine import MemoryCandidate, MemoryEngine, SourceEvent
from memory_engine.product_api import ProductMemoryView


def test_auto_project_with_mojibake_name_displays_unnamed_project() -> None:
    db_path = Path("tests_runtime") / "unnamed_project" / str(uuid.uuid4()) / "test.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with MemoryEngine(db_path) as engine:
            event = SourceEvent(
                source_type="message",
                source_ref="test://feishu/live-chat",
                actors=["u1"],
                timestamp="2026-05-06T10:00:00+08:00",
                content="decision",
                scope="project",
                payload={"chat_title": "\u00c3\u00c2\u00c3\u00c2\u00c3\u00c2"},
            )
            engine.write(
                event=event,
                project_id="auto_oc_live_chat",
                user_id="u1",
                memory_candidates=[
                    MemoryCandidate(
                        memory_type="decision",
                        title="Use live Feishu ingest",
                        summary="Use live Feishu ingest for the demo.",
                        content={"scope": "project"},
                        importance=0.8,
                        confidence=0.9,
                        evidence=[{"source_ref": "test://feishu/live-chat"}],
                    )
                ],
            )

        projects = ProductMemoryView(db_path).list_projects()

        assert projects[0]["project_id"] == "auto_oc_live_chat"
        assert projects[0]["name"] == "\u672a\u547d\u540d\u9879\u76ee1"
    finally:
        shutil.rmtree(db_path.parent, ignore_errors=True)
