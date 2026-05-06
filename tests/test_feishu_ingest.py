"""Tests for feishu-ingest MVP 1 fixture replay."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from feishu_ingest.adapters.fixture import FixtureAdapter, _parse_line
from feishu_ingest.adapters.lark_cli import (
    CommandRunner,
    LarkCLIAdapter,
    LarkCLISource,
    SetupError,
)
from feishu_ingest.adapters.live_event import LiveEventAdapter
from feishu_ingest.adapters.live_event import _parse_event as _parse_live_event
from feishu_ingest.evidence import build_evidence
from feishu_ingest.extractors import extract_candidates
from feishu_ingest.reply_triggers import (
    is_operation_trigger,
    is_related_trigger,
    is_summary_trigger,
    parse_preference_candidate_command,
    parse_workflow_strategy_command,
)
from feishu_ingest.pipeline import run_ingest
from feishu_ingest.scope import (
    configure_doc_project,
    configure_project_chat,
    infer_project_id,
    infer_scope,
    infer_task_id,
)
from memory_engine import MemoryCandidate, MemoryEngine, RecallRequest, Scope, SourceEvent


DECISION_TEXT = "\u51b3\u5b9a\u7528 SQLite \u4f5c\u4e3a\u672c\u5730 MVP \u5b58\u50a8\u65b9\u6848"
TASK_TEXT = "P2 review hardening \u5df2\u5b8c\u6210\uff0c\u6240\u6709\u6d4b\u8bd5\u901a\u8fc7"
PREFERENCE_TEXT = "\u4ee5\u540e\u9ed8\u8ba4\u5148\u5217\u8ba1\u5212\u518d\u4fee\u6539\u5e76\u5199\u65e5\u5fd7"
IMPLICIT_PREFERENCE_TEXT = "\u8bf7\u5148\u5206\u6790\u4e00\u4e0b\uff0c\u522b\u76f4\u63a5\u6539\u4ee3\u7801"
BLOCKED_TEXT = "\u8fd9\u4e2a\u9700\u6c42\u963b\u585e\u4e86\uff0c\u7b49\u540e\u7aef\u63a5\u53e3\u5b8c\u6210"
NO_MATCH_TEXT = "\u4eca\u5929\u5929\u6c14\u4e0d\u9519"


def _fixture_line(
    *,
    source_type: str = "message",
    source_ref: str = "om_test_001",
    source_url: str | None = "https://feishu.cn/message/om_test_001",
    content: str = DECISION_TEXT,
    scope: str = "project",
    project_id: str | None = "proj_alpha",
    task_id: str | None = None,
    user_id: str | None = "ou_user1",
    payload: dict | None = None,
    content_hash: str | None = None,
    source_version: str | None = None,
) -> str:
    return json.dumps(
        {
            "source_type": source_type,
            "source_ref": source_ref,
            "source_url": source_url,
            "actors": ["ou_user1"],
            "timestamp": "2026-04-28T09:00:00+08:00",
            "content": content,
            "scope": scope,
            "project_id": project_id,
            "task_id": task_id,
            "user_id": user_id,
            "payload": payload or {"chat_title": "dev-team", "msg_type": "text"},
            "content_hash": content_hash,
            "source_version": source_version,
        },
        ensure_ascii=False,
    )


FIXTURE_JSONL = _fixture_line()
FIXTURE_TASK = _fixture_line(source_ref="om_test_002", content=TASK_TEXT, task_id="task_42")
FIXTURE_PREF = _fixture_line(source_ref="om_test_003", content=PREFERENCE_TEXT, scope="user", project_id=None)
FIXTURE_IMPLICIT_PREF = _fixture_line(
    source_ref="om_test_003b",
    content=IMPLICIT_PREFERENCE_TEXT,
    scope="user",
    project_id=None,
)
FIXTURE_BLOCKED = _fixture_line(
    source_ref="om_test_004",
    content=BLOCKED_TEXT,
    scope="session",
    task_id="task_99",
    user_id="ou_user2",
)


@pytest.fixture
def runtime_dir() -> Path:
    path = Path("tests_runtime") / "feishu_ingest" / str(uuid.uuid4())
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def fixture_file(runtime_dir: Path) -> Path:
    p = runtime_dir / "events.jsonl"
    p.write_text(
        "\n".join([FIXTURE_JSONL, FIXTURE_TASK, FIXTURE_PREF, FIXTURE_IMPLICIT_PREF, FIXTURE_BLOCKED]),
        encoding="utf-8",
    )
    return p


class TestFeishuEvent:
    def test_content_hash_derived_from_content(self) -> None:
        event = _parse_line(FIXTURE_JSONL, 1)
        assert event._content_hash is not None
        assert len(event._content_hash) == 64

    def test_content_hash_from_explicit_field(self) -> None:
        event = _parse_line(_fixture_line(content_hash="abc123"), 1)
        assert event._content_hash == "abc123"

    def test_to_source_event_bridge(self) -> None:
        event = _parse_line(FIXTURE_JSONL, 1)
        se = event.to_source_event()
        assert se.source_type == "message"
        assert se.source_ref == "om_test_001"
        assert se.content == DECISION_TEXT
        assert se.scope == "project"

    def test_sanitised_payload_strips_raw_fields(self) -> None:
        event = _parse_line(
            _fixture_line(payload={"chat_title": "dev-team", "raw_api_response": {"secret": "x"}}),
            1,
        )
        payload = event._sanitised_payload()
        assert payload["source_url"] == "https://feishu.cn/message/om_test_001"
        assert "chat_title" in payload
        assert "raw_api_response" not in payload

    def test_sanitised_payload_keeps_source_fingerprint(self) -> None:
        event = _parse_line(_fixture_line(content_hash="hash_from_feishu", source_version="v3"), 1)
        payload = event._sanitised_payload()
        assert payload["content_hash"] == "hash_from_feishu"
        assert payload["source_version"] == "v3"


class TestFixtureAdapter:
    def test_stream_parses_valid_jsonl(self, fixture_file: Path) -> None:
        events = list(FixtureAdapter(fixture_file).stream_events())
        assert len(events) == 5

    def test_stream_skips_comment_lines(self, runtime_dir: Path) -> None:
        p = runtime_dir / "with_comments.jsonl"
        p.write_text("# comment\n" + FIXTURE_JSONL + "\n# another\n" + FIXTURE_TASK, encoding="utf-8")
        events = list(FixtureAdapter(p).stream_events())
        assert len(events) == 2

    def test_stream_skips_empty_lines(self, runtime_dir: Path) -> None:
        p = runtime_dir / "with_empty.jsonl"
        p.write_text("\n\n" + FIXTURE_JSONL + "\n\n", encoding="utf-8")
        events = list(FixtureAdapter(p).stream_events())
        assert len(events) == 1

    def test_stream_raises_on_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            list(FixtureAdapter("/nonexistent/path.jsonl").stream_events())

    def test_stream_continues_on_bad_line(self, runtime_dir: Path) -> None:
        p = runtime_dir / "mixed.jsonl"
        p.write_text(FIXTURE_JSONL + "\nnot valid json\n" + FIXTURE_TASK, encoding="utf-8")
        events = list(FixtureAdapter(p).stream_events())
        assert len(events) == 2


class TestScopeInference:
    def test_explicit_scope_is_preserved(self) -> None:
        event = _parse_line(FIXTURE_JSONL, 1)
        assert infer_scope(event) == Scope.PROJECT

    def test_tag_in_content_sets_project_scope(self) -> None:
        event = _parse_line(_fixture_line(content="#project:proj_beta " + DECISION_TEXT, scope="user"), 1)
        assert infer_scope(event) == Scope.PROJECT

    def test_task_tag_sets_session_scope(self) -> None:
        event = _parse_line(_fixture_line(content="#task:t_88 " + TASK_TEXT, scope="user"), 1)
        assert infer_scope(event) == Scope.SESSION

    def test_configured_chat_id_maps_to_project(self) -> None:
        configure_project_chat({"ch_abc123": "proj_mapped"})
        event = _parse_line(
            _fixture_line(payload={"chat_id": "ch_abc123", "chat_title": "team"}, scope="user", project_id=None),
            1,
        )
        try:
            assert infer_scope(event) == Scope.PROJECT
            assert infer_project_id(event) == "proj_mapped"
        finally:
            configure_project_chat({})

    def test_configured_doc_id_maps_to_project(self) -> None:
        configure_doc_project({"doc_1": "proj_doc"})
        event = _parse_line(
            _fixture_line(source_type="doc", source_ref="doc_1", content=DECISION_TEXT, scope="user", project_id=None),
            1,
        )
        try:
            assert infer_scope(event) == Scope.PROJECT
            assert infer_project_id(event) == "proj_doc"
        finally:
            configure_doc_project({})

    def test_infer_project_id_from_tag(self) -> None:
        event = _parse_line(_fixture_line(content="#project:proj_xyz " + DECISION_TEXT, project_id=None), 1)
        assert infer_project_id(event) == "proj_xyz"

    def test_infer_task_id_from_tag(self) -> None:
        event = _parse_line(_fixture_line(content="#task:t_77 " + TASK_TEXT, task_id=None), 1)
        assert infer_task_id(event) == "t_77"

    def test_infer_task_id_from_explicit_field(self) -> None:
        event = _parse_line(FIXTURE_TASK, 1)
        assert infer_task_id(event) == "task_42"


class TestCandidateExtraction:
    def test_decision_pattern_matches_real_chinese(self) -> None:
        candidates = extract_candidates(_parse_line(FIXTURE_JSONL, 1))
        assert any(c.memory_type == "decision" for c in candidates)

    def test_task_status_pattern_matches_real_chinese(self) -> None:
        candidates = extract_candidates(_parse_line(FIXTURE_TASK, 1))
        assert any(c.memory_type == "task_status" for c in candidates)

    def test_blocked_task_pattern_matches_real_chinese(self) -> None:
        candidates = extract_candidates(_parse_line(FIXTURE_BLOCKED, 1))
        assert any(c.memory_type == "task_status" for c in candidates)

    def test_project_risk_fields_are_structured_on_task_status(self) -> None:
        text = "当前客户交付项目接口联调完成 70%，但验收材料还缺最后一版安全说明，预计会影响周五验收。"
        event = _parse_line(
            _fixture_line(
                content=text,
                payload={"chat_title": "客户交付项目群", "sender_name": "李想", "sender_role": "项目经理"},
            ),
            1,
        )
        task = next(c for c in extract_candidates(event) if c.memory_type == "task_status")

        assert task.content["progress"] == "70%"
        assert task.content["risk_level"] == "high"
        assert task.content["deadline"] == "周五"
        assert task.content["customer"] == "客户交付场景"
        assert task.content["stakeholders"][0]["name"] == "李想"
        assert "risk" in task.tags
        assert "progress" in task.tags

    def test_task_status_deadline_derives_valid_until(self) -> None:
        event = _parse_line(
            _fixture_line(
                content=(
                    "\u5f53\u524d\u8fdb\u5ea6 60%\uff0c"
                    "\u622a\u6b62\u5230\u660e\u5929\u9700\u8981\u5b8c\u6210\u8054\u8c03\u3002"
                )
            ),
            1,
        )
        task = next(c for c in extract_candidates(event) if c.memory_type == "task_status")

        assert task.content["deadline"] == "\u660e\u5929"
        assert task.content["valid_until"] == "2026-04-29T23:59:59+08:00"
        assert task.content["valid_until_source"] == "deadline"

    def test_task_status_weekday_deadline_derives_valid_until(self) -> None:
        event = _parse_line(
            _fixture_line(
                content=(
                    "\u5f53\u524d\u5ba2\u6237\u4ea4\u4ed8\u8fdb\u5ea6 70%\uff0c"
                    "\u4f46\u9a8c\u6536\u6750\u6599\u8fd8\u7f3a\uff0c"
                    "\u9884\u8ba1\u4f1a\u5f71\u54cd\u5468\u4e94\u9a8c\u6536\u3002"
                )
            ),
            1,
        )
        task = next(c for c in extract_candidates(event) if c.memory_type == "task_status")

        assert task.content["deadline"] == "\u5468\u4e94"
        assert task.content["valid_until"] == "2026-05-01T23:59:59+08:00"

    def test_owner_assignment_is_structured_on_decision(self) -> None:
        text = "后端分工已经定了：王浩负责飞书开放平台对接和核心接口架构。"
        event = _parse_line(_fixture_line(content=text), 1)
        decision = next(c for c in extract_candidates(event) if c.memory_type == "decision")

        assert {"name": "王浩", "responsibility": "飞书开放平台对接和核心接口架构"} in decision.content["stakeholders"]
        assert "stakeholder" in decision.tags

    def test_preference_pattern_matches_real_chinese(self) -> None:
        candidates = extract_candidates(_parse_line(FIXTURE_PREF, 1))
        assert any(c.memory_type == "preference" for c in candidates)

    def test_implicit_preference_observation_matches_planning_behavior(self) -> None:
        candidates = extract_candidates(_parse_line(FIXTURE_IMPLICIT_PREF, 1))
        pref_candidates = [c for c in candidates if c.memory_type == "preference"]
        assert pref_candidates
        assert pref_candidates[0].confidence < 0.7
        assert pref_candidates[0].content["kind"] == "implicit_preference_observation"

    def test_no_match_returns_empty_list(self) -> None:
        event = _parse_line(_fixture_line(content=NO_MATCH_TEXT, scope="user", project_id=None), 1)
        assert extract_candidates(event) == []

    def test_study_log_progress_patterns_extract_task_status(self) -> None:
        text = (
            "\u5b66\u4e60\u7b14\u8bb0\n"
            "## \u5f53\u524d\u8fdb\u5ea6\n"
            "- \u5f53\u524d\u5df2\u8bb0\u5f55 8.43 \u5c0f\u65f6\u3002\n"
            "- \u5269\u4f59\u4efb\u52a1\u7b49\u5f85\u4e0b\u6b21\u7ee7\u7eed\u3002"
        )
        event = _parse_line(_fixture_line(content=text), 1)
        candidates = extract_candidates(event)
        assert any(c.memory_type == "task_status" for c in candidates)

    def test_expanded_preference_patterns_extract_preference(self) -> None:
        event = _parse_line(
            _fixture_line(content="\u6211\u5efa\u8bae\u4f18\u5148\u8003\u8651 FastAPI\uff0c\u9ed8\u8ba4\u63a8\u8350\u7528 pytest"),
            1,
        )
        candidates = extract_candidates(event)
        assert any(c.memory_type == "preference" for c in candidates)

    def test_bug_priority_status_is_not_misclassified_as_preference(self) -> None:
        event = _parse_line(
            _fixture_line(
                content=(
                    "\u7b2c\u4e00\u8f6e\u5192\u70df\u6d4b\u8bd5\u4e00\u5171\u63d0\u4e86 32 \u4e2a bug\uff0c"
                    "\u5176\u4e2d P0 \u7ea7 2 \u4e2a\uff0cP1 \u7ea7 8 \u4e2a\uff0cP2 \u7ea7 22 \u4e2a\uff0c"
                    "\u8bf7\u4f18\u5148\u4fee\u590d\u9ad8\u4f18\u5148\u7ea7 bug\u3002"
                )
            ),
            1,
        )
        types = {c.memory_type for c in extract_candidates(event)}
        assert "task_status" in types
        assert "preference" not in types

    def test_decision_and_task_both_extracted(self) -> None:
        event = _parse_line(_fixture_line(content="\u51b3\u5b9a\u91c7\u7528 PostgreSQL\uff0c\u4efb\u52a1\u5df2\u5b8c\u6210"), 1)
        types = {c.memory_type for c in extract_candidates(event)}
        assert "decision" in types
        assert "task_status" in types

    def test_decision_with_default_language_is_not_duplicated_as_preference(self) -> None:
        text = "\u6211\u4eec\u51b3\u5b9a\u540e\u7eed\u9ed8\u8ba4\u4f7f\u7528 daemon \u4f5c\u4e3a\u98de\u4e66\u4e3b\u52a8\u56de\u590d\u5165\u53e3"
        event = _parse_line(_fixture_line(content=text), 1)
        types = [c.memory_type for c in extract_candidates(event)]
        assert "decision" in types
        assert "preference" not in types

    def test_question_with_next_step_is_not_task_status(self) -> None:
        event = _parse_line(_fixture_line(content="\u5f53\u524d\u4efb\u52a1\u4e0b\u4e00\u6b65\u600e\u4e48\u5904\u7406\uff1f"), 1)
        assert extract_candidates(event) == []

    def test_confidence_high_for_strong_match(self) -> None:
        event = _parse_line(_fixture_line(content="\u7ed3\u8bba\uff1a\u51b3\u5b9a\u91c7\u7528 SQLite \u65b9\u6848"), 1)
        decision = next(c for c in extract_candidates(event) if c.memory_type == "decision")
        assert decision.confidence == 0.8

    def test_candidate_content_uses_resolved_scope(self) -> None:
        event = _parse_line(_fixture_line(content="#task:t_77 " + TASK_TEXT, scope="user", task_id=None), 1)
        candidate = extract_candidates(event, scope=Scope.SESSION, project_id="proj_alpha", task_id="t_77")[0]
        assert candidate.content["scope"] == "session"
        assert candidate.content["project_id"] == "proj_alpha"
        assert candidate.content["task_id"] == "t_77"


class TestEvidence:
    def test_evidence_contains_source_metadata(self) -> None:
        event = _parse_line(FIXTURE_JSONL, 1)
        ev = build_evidence(event)
        assert len(ev) == 1
        assert ev[0]["source_type"] == "feishu_message"
        assert ev[0]["source_ref"] == "om_test_001"
        assert ev[0]["source_url"] == "https://feishu.cn/message/om_test_001"
        assert ev[0]["actors"] == ["ou_user1"]
        assert ev[0]["snippet"] == DECISION_TEXT
        assert len(ev[0]["content_hash"]) == 64


class TestPipeline:
    def test_full_ingest_writes_memories(self, fixture_file: Path, runtime_dir: Path) -> None:
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            result = run_ingest(FixtureAdapter(fixture_file), engine)
            assert result.events_processed == 5
            assert result.events_written >= 3
            assert result.events_skipped_dup == 0
            assert result.events_skipped_no_candidate == 0
            assert len(result.memory_ids) >= 3

    def test_duplicate_event_skipped_by_hash(self, runtime_dir: Path) -> None:
        p = runtime_dir / "dup.jsonl"
        p.write_text(FIXTURE_JSONL + "\n" + FIXTURE_JSONL, encoding="utf-8")
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            result = run_ingest(FixtureAdapter(p), engine)
            assert result.events_processed == 2
            assert result.events_written == 1
            assert result.events_skipped_dup == 1

    def test_cross_run_no_duplicate_memories(self, fixture_file: Path, runtime_dir: Path) -> None:
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            result1 = run_ingest(FixtureAdapter(fixture_file), engine)
            assert len(result1.memory_ids) >= 3
            result2 = run_ingest(FixtureAdapter(fixture_file), engine)
            assert len(result2.memory_ids) == 0
            assert result2.events_skipped_dup == 5

    def test_same_source_ref_changed_hash_is_not_skipped(self, runtime_dir: Path) -> None:
        first = runtime_dir / "first.jsonl"
        second = runtime_dir / "second.jsonl"
        first.write_text(_fixture_line(source_ref="om_same", content=DECISION_TEXT), encoding="utf-8")
        second.write_text(
            _fixture_line(source_ref="om_same", content="\u51b3\u5b9a\u7528 Redis \u4f5c\u4e3a\u7f13\u5b58\u65b9\u6848"),
            encoding="utf-8",
        )
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            result1 = run_ingest(FixtureAdapter(first), engine)
            result2 = run_ingest(FixtureAdapter(second), engine)
            assert len(result1.memory_ids) == 1
            assert len(result2.memory_ids) == 1
            assert result2.events_skipped_dup == 0

    def test_recall_retrieves_written_memory(self, fixture_file: Path, runtime_dir: Path) -> None:
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            run_ingest(FixtureAdapter(fixture_file), engine)
            results = engine.recall(RecallRequest(query="SQLite"))
            assert len(results) >= 1
            assert "SQLite" in results[0]["summary"]

    def test_recall_retrieves_preference(self, fixture_file: Path, runtime_dir: Path) -> None:
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            run_ingest(FixtureAdapter(fixture_file), engine)
            results = engine.recall(RecallRequest(query="\u8ba1\u5212 \u65e5\u5fd7", user_id="ou_user1"))
            prefs = [r for r in results if r["memory_type"] == "preference"]
            assert len(prefs) >= 1

    def test_recall_filter_by_project_id(self, fixture_file: Path, runtime_dir: Path) -> None:
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            run_ingest(FixtureAdapter(fixture_file), engine)
            results = engine.recall(RecallRequest(query="", project_id="proj_alpha"))
            assert len(results) >= 3

    def test_evidence_in_written_memory(self, fixture_file: Path, runtime_dir: Path) -> None:
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            run_ingest(FixtureAdapter(fixture_file), engine)
            results = engine.recall(RecallRequest(query="SQLite"))
            assert len(results) >= 1
            assert "source_ref" in str(results[0]["evidence"])

    def test_no_candidate_events_skipped(self, runtime_dir: Path) -> None:
        p = runtime_dir / "no_match.jsonl"
        p.write_text(_fixture_line(source_ref="om_no_match", content=NO_MATCH_TEXT, scope="user", project_id=None), encoding="utf-8")
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            result = run_ingest(FixtureAdapter(p), engine)
            assert result.events_processed == 1
            assert result.events_skipped_no_candidate == 1
            assert result.events_written == 0

    def test_doc_event_extracted(self, runtime_dir: Path) -> None:
        p = runtime_dir / "doc.jsonl"
        p.write_text(
            _fixture_line(
                source_type="doc",
                source_ref="doc_fixture_001",
                source_url="https://feishu.cn/doc/doc_fixture_001",
                content="\u7ed3\u8bba\uff1a\u91c7\u7528 BM25 + freshness \u6df7\u5408\u8bc4\u5206\u65b9\u6848",
                payload={"doc_title": "\u641c\u7d22\u65b9\u6848\u8bc4\u5ba1"},
            ),
            encoding="utf-8",
        )
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            result = run_ingest(FixtureAdapter(p), engine)
            assert result.events_written >= 1
            results = engine.recall(RecallRequest(query="BM25", user_id="ou_user1", project_id="proj_alpha"))
            assert len(results) >= 1
            assert results[0]["memory_type"] == "decision"

    def test_inferred_scope_is_persisted_to_memory(self, runtime_dir: Path) -> None:
        p = runtime_dir / "scoped.jsonl"
        p.write_text(
            _fixture_line(content="#task:t_77 " + TASK_TEXT, scope="user", project_id="proj_alpha", task_id=None),
            encoding="utf-8",
        )
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            result = run_ingest(FixtureAdapter(p), engine)
            assert result.memory_ids
            row = engine.conn.execute("SELECT scope, content_json, task_id FROM memories WHERE id = ?", (result.memory_ids[0],)).fetchone()
            content = json.loads(row["content_json"])
            assert row["scope"] == "session"
            assert row["task_id"] == "t_77"
            assert content["scope"] == "session"
            assert content["task_id"] == "t_77"

    def test_source_fingerprint_persisted_to_events(self, runtime_dir: Path) -> None:
        p = runtime_dir / "fingerprint.jsonl"
        p.write_text(
            _fixture_line(content_hash="feishu_hash_123", source_version="v9"),
            encoding="utf-8",
        )
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            result = run_ingest(FixtureAdapter(p), engine)
            row = engine.conn.execute("SELECT content_hash, source_version FROM events WHERE id = ?", (result.events_processed,)).fetchone()
            assert row["content_hash"] == "feishu_hash_123"
            assert row["source_version"] == "v9"


# ---------------------------------------------------------------------------
# MVP 2: LarkCLIAdapter tests
# ---------------------------------------------------------------------------

AUTH_OK: dict = {"authenticated": True, "user": {"name": "Test User"}}
AUTH_FAIL: dict = {"authenticated": False}

MOCK_MESSAGE_RESPONSE = json.dumps(
    {
        "data": {
            "items": [
                {
                    "message_id": "om_cli_001",
                    "chat_id": "oc_dev_team",
                    "text": "决定用 PostgreSQL 作为生产数据库",
                    "sender": {"user_id": "ou_test_user", "name": "Test User"},
                    "create_time": "1714300000",
                    "msg_type": "text",
                },
                {
                    "message_id": "om_cli_002",
                    "chat_id": "oc_dev_team",
                    "text": "前端页面进度：已完成首页布局",
                    "sender": {"user_id": "ou_test_user2", "name": "Dev Two"},
                    "create_time": "1714300060",
                    "msg_type": "text",
                },
            ],
            "has_more": False,
        }
    }
)

MOCK_DOC_RESPONSE = json.dumps(
    {
        "data": {
            "document_id": "doc_cli_001",
            "title": "技术选型文档",
            "content": "结论：采用 Redis 作为缓存方案",
            "owner": {"user_id": "ou_doc_owner"},
            "create_time": "1714300200",
            "url": "https://feishu.cn/doc/doc_cli_001",
        }
    }
)


class MockCommandRunner(CommandRunner):
    """Mock command runner for testing without real Feishu credentials."""

    def __init__(self, auth_status: dict, responses: dict[str, str] | None = None):
        self._auth_status = auth_status
        self._responses = responses or {}

    def run(self, cmd: list[str], timeout: int = 30) -> str:
        key = " ".join(cmd)
        for k, v in self._responses.items():
            if key.startswith(k):
                return v
        raise RuntimeError(f"No mock response for: {key}")

    def check_auth(self) -> dict:
        return self._auth_status


class TestLarkCLIAdapter:
    def test_auth_ok_allows_stream(self) -> None:
        runner = MockCommandRunner(
            auth_status=AUTH_OK,
            responses={"lark-cli im": MOCK_MESSAGE_RESPONSE},
        )
        adapter = LarkCLIAdapter(
            command_runner=runner,
            sources=[LarkCLISource.im(chat_id="oc_dev_team", limit=10)],
        )
        events = list(adapter.stream_events())
        assert len(events) >= 1
        assert any("决定" in e.content for e in events)

    def test_unauthenticated_raises_setup_error(self) -> None:
        runner = MockCommandRunner(auth_status=AUTH_FAIL)
        adapter = LarkCLIAdapter(
            command_runner=runner,
            sources=[LarkCLISource.im(chat_id="oc_dev_team")],
        )
        with pytest.raises(SetupError, match="not authenticated"):
            list(adapter.stream_events())

    def test_cli_missing_raises_setup_error(self) -> None:
        class MissingRunner(CommandRunner):
            def run(self, cmd: list[str], timeout: int = 30) -> str:
                raise FileNotFoundError("lark-cli not found")

            def check_auth(self) -> dict:
                raise FileNotFoundError("lark-cli not found")

        adapter = LarkCLIAdapter(
            command_runner=MissingRunner(),
            sources=[LarkCLISource.im()],
        )
        with pytest.raises(SetupError, match="not found"):
            list(adapter.stream_events())

    def test_malformed_items_skipped(self) -> None:
        bad_response = json.dumps(
            {
                "data": {
                    "items": [
                        {"message_id": None, "text": ""},
                        {"message_id": "om_ok", "text": "决定用 Go", "sender": {"user_id": "ou_1"}, "create_time": "1714300000"},
                    ]
                }
            }
        )
        runner = MockCommandRunner(
            auth_status=AUTH_OK,
            responses={"lark-cli im": bad_response},
        )
        adapter = LarkCLIAdapter(
            command_runner=runner,
            sources=[LarkCLISource.im(chat_id="oc_dev_team")],
        )
        events = list(adapter.stream_events())
        assert len(events) == 1
        assert events[0].source_ref == "om_ok"

    def test_message_content_json_string_is_normalized(self) -> None:
        response = json.dumps(
            {
                "data": {
                    "items": [
                        {
                            "message_id": "om_json_content",
                            "chat_id": "oc_dev_team",
                            "content": json.dumps({"text": DECISION_TEXT}, ensure_ascii=False),
                            "sender": {"user_id": "ou_test_user"},
                            "create_time": "1714300000",
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )
        runner = MockCommandRunner(
            auth_status=AUTH_OK,
            responses={"lark-cli im": response},
        )
        adapter = LarkCLIAdapter(
            command_runner=runner,
            sources=[LarkCLISource.im(chat_id="oc_dev_team")],
        )
        events = list(adapter.stream_events())
        assert len(events) == 1
        assert events[0].content == DECISION_TEXT
        assert any(c.memory_type == "decision" for c in extract_candidates(events[0]))

    def test_unmapped_group_message_starts_as_user_scope(self) -> None:
        runner = MockCommandRunner(
            auth_status=AUTH_OK,
            responses={"lark-cli im": MOCK_MESSAGE_RESPONSE},
        )
        adapter = LarkCLIAdapter(
            command_runner=runner,
            sources=[LarkCLISource.im(chat_id="oc_dev_team")],
        )
        events = list(adapter.stream_events())
        assert len(events) >= 1
        assert events[0].scope == Scope.USER
        assert infer_scope(events[0]) == Scope.USER

    def test_mapped_chat_produces_project_scope(self) -> None:
        configure_project_chat({"oc_dev_team": "proj_alpha"})
        try:
            runner = MockCommandRunner(
                auth_status=AUTH_OK,
                responses={"lark-cli im": MOCK_MESSAGE_RESPONSE},
            )
            adapter = LarkCLIAdapter(
                command_runner=runner,
                sources=[LarkCLISource.im(chat_id="oc_dev_team")],
            )
            events = list(adapter.stream_events())
            assert len(events) >= 1
            assert events[0].scope == Scope.PROJECT
            assert events[0].project_id == "proj_alpha"
        finally:
            configure_project_chat({})

    def test_project_tag_in_content_overrides_scope(self) -> None:
        tagged_response = json.dumps(
            {
                "data": {
                    "items": [
                        {
                            "message_id": "om_tagged",
                            "chat_id": "oc_random",
                            "content": "#project:proj_beta " + DECISION_TEXT,
                            "sender": {"user_id": "ou_1"},
                            "create_time": "1714300000",
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )
        runner = MockCommandRunner(
            auth_status=AUTH_OK,
            responses={"lark-cli im": tagged_response},
        )
        adapter = LarkCLIAdapter(
            command_runner=runner,
            sources=[LarkCLISource.im(chat_id="oc_random")],
        )
        events = list(adapter.stream_events())
        assert len(events) == 1
        assert events[0].scope == Scope.PROJECT
        assert events[0].project_id == "proj_beta"

    def test_doc_fetch_produces_event(self) -> None:
        runner = MockCommandRunner(
            auth_status=AUTH_OK,
            responses={"lark-cli docs": MOCK_DOC_RESPONSE},
        )
        adapter = LarkCLIAdapter(
            command_runner=runner,
            sources=[LarkCLISource.doc("doc_cli_001")],
        )
        events = list(adapter.stream_events())
        assert len(events) == 1
        assert events[0].source_type == "doc"
        assert "Redis" in events[0].content

    def test_command_failure_does_not_crash_stream(self) -> None:
        class FailingRunner(CommandRunner):
            def run(self, cmd: list[str], timeout: int = 30) -> str:
                raise RuntimeError("connection timeout")

            def check_auth(self) -> dict:
                return AUTH_OK

        adapter = LarkCLIAdapter(
            command_runner=FailingRunner(),
            sources=[LarkCLISource.im()],
        )
        events = list(adapter.stream_events())
        assert events == []

    def test_pipeline_with_larkcli_adapter(self, runtime_dir: Path) -> None:
        runner = MockCommandRunner(
            auth_status=AUTH_OK,
            responses={"lark-cli im": MOCK_MESSAGE_RESPONSE},
        )
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            adapter = LarkCLIAdapter(
                command_runner=runner,
                sources=[LarkCLISource.im(chat_id="oc_dev_team")],
            )
            result = run_ingest(adapter, engine)
            assert result.events_written >= 1
            assert len(result.memory_ids) >= 1

    def test_pipeline_recall_after_larkcli_ingest(self, runtime_dir: Path) -> None:
        runner = MockCommandRunner(
            auth_status=AUTH_OK,
            responses={"lark-cli im": MOCK_MESSAGE_RESPONSE},
        )
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            adapter = LarkCLIAdapter(
                command_runner=runner,
                sources=[LarkCLISource.im(chat_id="oc_dev_team")],
            )
            run_ingest(adapter, engine)
            results = engine.recall(RecallRequest(query="PostgreSQL", user_id="ou_test_user"))
            assert len(results) >= 1
            assert any("PostgreSQL" in r["summary"] for r in results)

    def test_timestamp_normalized_from_epoch(self) -> None:
        runner = MockCommandRunner(
            auth_status=AUTH_OK,
            responses={"lark-cli im": MOCK_MESSAGE_RESPONSE},
        )
        adapter = LarkCLIAdapter(
            command_runner=runner,
            sources=[LarkCLISource.im(chat_id="oc_dev_team")],
        )
        events = list(adapter.stream_events())
        assert len(events) >= 1
        # 1714300000 should be converted to ISO 8601
        assert "T" in events[0].timestamp

    def test_timestamp_normalized_from_epoch_milliseconds(self) -> None:
        response = json.dumps(
            {
                "data": {
                    "items": [
                        {
                            "message_id": "om_ms",
                            "text": DECISION_TEXT,
                            "sender": {"user_id": "ou_test_user"},
                            "create_time": "1714300000000",
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )
        runner = MockCommandRunner(
            auth_status=AUTH_OK,
            responses={"lark-cli im": response},
        )
        adapter = LarkCLIAdapter(
            command_runner=runner,
            sources=[LarkCLISource.im(user_id="ou_test_user")],
        )
        events = list(adapter.stream_events())
        assert len(events) == 1
        assert events[0].timestamp.startswith("2024-04-28T")

    def test_no_sources_produces_no_events(self) -> None:
        runner = MockCommandRunner(auth_status=AUTH_OK)
        adapter = LarkCLIAdapter(command_runner=runner, sources=[])
        events = list(adapter.stream_events())
        assert events == []

    def test_larkcli_source_builds_im_command(self) -> None:
        source = LarkCLISource.im(chat_id="oc_abc", limit=20)
        cmd = source.build_command()
        assert cmd[0] == "lark-cli"
        assert "--chat-id" in cmd
        assert "oc_abc" in cmd
        assert "--page-size" in cmd
        assert "20" in cmd

    def test_larkcli_source_builds_im_user_command(self) -> None:
        source = LarkCLISource.im(user_id="ou_xxx")
        cmd = source.build_command()
        assert "--user-id" in cmd
        assert "ou_xxx" in cmd

    def test_larkcli_source_im_requires_id(self) -> None:
        source = LarkCLISource("im", {})
        with pytest.raises(ValueError, match="chat_id.*user_id"):
            source.build_command()

    def test_larkcli_source_builds_doc_command(self) -> None:
        source = LarkCLISource.doc("doc_xyz")
        cmd = source.build_command()
        assert "--doc" in cmd
        assert "doc_xyz" in cmd

    def test_larkcli_source_doc_requires_id(self) -> None:
        source = LarkCLISource("doc", {})
        with pytest.raises(ValueError, match="doc_id"):
            source.build_command()


# =============================================================================
# MVP 3: Live Event Adapter
# =============================================================================


class MockBackgroundProcess:
    """Mock background process that yields predefined NDJSON lines."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self._terminated = False

    def stdout_lines(self):
        for line in self._lines:
            yield line

    def terminate(self) -> None:
        self._terminated = True


class MockLiveCommandRunner(CommandRunner):
    """Command runner that supports run_background for live event tests."""

    def __init__(
        self,
        auth_status: dict,
        background_lines: list[str] | None = None,
    ) -> None:
        self._auth_status = auth_status
        self._background_lines = background_lines or []
        self._bg_processes: list[MockBackgroundProcess] = []

    def run(self, cmd: list[str], timeout: int = 30) -> str:
        return ""

    def check_auth(self) -> dict:
        return self._auth_status

    def run_background(self, cmd: list[str]):
        proc = MockBackgroundProcess(self._background_lines)
        self._bg_processes.append(proc)
        return proc


# Sample NDJSON events from lark-cli event consume
MOCK_RECEIVE_EVENT = json.dumps({
    "type": "im.message.receive_v1",
    "event": {
        "message_id": "om_live_001",
        "content": "决定用 Python 开发后端服务",
        "sender_id": "ou_test_user",
        "chat_id": "oc_test_chat",
        "chat_type": "group",
        "message_type": "text",
        "create_time": "1714500000000",
    },
})

MOCK_RECEIVE_EVENT_2 = json.dumps({
    "type": "im.message.receive_v1",
    "event": {
        "message_id": "om_live_002",
        "content": "任务已完成，可以部署了",
        "sender_id": "ou_test_user",
        "chat_id": "oc_test_chat",
        "chat_type": "group",
        "message_type": "text",
        "create_time": "1714500001000",
    },
})

MOCK_NON_MESSAGE_EVENT = json.dumps({
    "type": "im.chat.disbanded_v1",
    "event": {"chat_id": "oc_test_chat"},
})


class TestLiveEventAdapter:
    def test_consume_message_yields_feishu_event(self) -> None:
        runner = MockLiveCommandRunner(
            auth_status=AUTH_OK,
            background_lines=[MOCK_RECEIVE_EVENT],
        )
        adapter = LiveEventAdapter(command_runner=runner)
        events = list(adapter.stream_events())
        assert len(events) == 1
        e = events[0]
        assert e.source_type == "message"
        assert e.source_ref == "om_live_001"
        assert "Python" in e.content
        assert e.scope == Scope.USER
        assert e.actors == ["ou_test_user"]

    def test_live_event_json_content_is_normalized(self) -> None:
        raw = json.dumps({
            "type": "im.message.receive_v1",
            "event": {
                "message_id": "om_live_json",
                "content": json.dumps({"text": DECISION_TEXT}, ensure_ascii=False),
                "sender_id": "ou_test_user",
                "chat_id": "oc_test_chat",
                "message_type": "text",
                "create_time": "1714500000000",
            },
        }, ensure_ascii=False)
        event = _parse_live_event(raw)
        assert event is not None
        assert event.content == DECISION_TEXT
        assert event.scope == Scope.USER

    def test_live_event_configured_chat_maps_to_project(self) -> None:
        configure_project_chat({"oc_test_chat": "proj_live"})
        try:
            event = _parse_live_event(MOCK_RECEIVE_EVENT)
            assert event is not None
            assert event.scope == Scope.PROJECT
            assert event.project_id == "proj_live"
        finally:
            configure_project_chat({})

    def test_skips_duplicate_events(self) -> None:
        runner = MockLiveCommandRunner(
            auth_status=AUTH_OK,
            background_lines=[MOCK_RECEIVE_EVENT, MOCK_RECEIVE_EVENT],
        )
        adapter = LiveEventAdapter(command_runner=runner)
        events = list(adapter.stream_events())
        assert len(events) == 1

    def test_skips_non_message_event_types(self) -> None:
        runner = MockLiveCommandRunner(
            auth_status=AUTH_OK,
            background_lines=[MOCK_NON_MESSAGE_EVENT, MOCK_RECEIVE_EVENT],
        )
        adapter = LiveEventAdapter(command_runner=runner)
        events = list(adapter.stream_events())
        assert len(events) == 1
        assert events[0].source_ref == "om_live_001"

    def test_filters_by_allowed_chat_id(self) -> None:
        runner = MockLiveCommandRunner(
            auth_status=AUTH_OK,
            background_lines=[MOCK_RECEIVE_EVENT],
        )
        adapter = LiveEventAdapter(
            command_runner=runner,
            allowed_chat_ids={"oc_other_chat"},
        )
        events = list(adapter.stream_events())
        assert len(events) == 0

    def test_close_stops_stream(self) -> None:
        runner = MockLiveCommandRunner(
            auth_status=AUTH_OK,
            background_lines=[MOCK_RECEIVE_EVENT],
        )
        adapter = LiveEventAdapter(command_runner=runner)
        events = list(adapter.stream_events())
        adapter.close()
        assert adapter._closed

    def test_unauthenticated_raises_setup_error(self) -> None:
        runner = MockLiveCommandRunner(auth_status=AUTH_FAIL)
        adapter = LiveEventAdapter(command_runner=runner)
        with pytest.raises(SetupError):
            list(adapter.stream_events())

    def test_multiple_events_streamed(self) -> None:
        runner = MockLiveCommandRunner(
            auth_status=AUTH_OK,
            background_lines=[MOCK_RECEIVE_EVENT, MOCK_RECEIVE_EVENT_2],
        )
        adapter = LiveEventAdapter(command_runner=runner)
        events = list(adapter.stream_events())
        assert len(events) == 2
        assert events[0].source_ref == "om_live_001"
        assert events[1].source_ref == "om_live_002"

    def test_malformed_json_skipped(self) -> None:
        runner = MockLiveCommandRunner(
            auth_status=AUTH_OK,
            background_lines=["not json", MOCK_RECEIVE_EVENT],
        )
        adapter = LiveEventAdapter(command_runner=runner)
        events = list(adapter.stream_events())
        assert len(events) == 1

    def test_missing_fields_skipped(self) -> None:
        bad_event = json.dumps({"type": "im.message.receive_v1", "event": {}})
        runner = MockLiveCommandRunner(
            auth_status=AUTH_OK,
            background_lines=[bad_event, MOCK_RECEIVE_EVENT],
        )
        adapter = LiveEventAdapter(command_runner=runner)
        events = list(adapter.stream_events())
        assert len(events) == 1


# ---------------------------------------------------------------------------
# LarkWsAdapter tests — WebSocket long-connection adapter via lark-oapi SDK
# ---------------------------------------------------------------------------

from feishu_ingest.adapters.lark_ws import (
    LarkWsAdapter,
    _load_lark,
    _sdk_event_to_feishu_event,
    _extract_text_content,
    _normalize_timestamp,
)


def _make_mock_sdk_event(
    *,
    message_id: str = "om_ws_001",
    chat_id: str = "oc_test_chat",
    sender_id: str = "ou_sender",
    content: str = '{"text": "决定使用SQLite"}',
    msg_type: str = "text",
    chat_type: str = "group",
    create_time: str = "1714277400000",
):
    """Create a mock matching real P2ImMessageReceiveV1 structure.

    Real structure:
      data.event.message   → EventMessage {message_id, chat_id, content, ...}
      data.event.sender    → EventSender  {sender_id, sender_type, ...}
    """
    from unittest.mock import MagicMock

    message = MagicMock()
    message.message_id = message_id
    message.root_id = None
    message.parent_id = None
    message.create_time = create_time
    message.update_time = None
    message.chat_id = chat_id
    message.thread_id = None
    message.chat_type = chat_type
    message.message_type = msg_type
    message.content = content
    message.mentions = None
    message.user_agent = None

    sender = MagicMock()
    sender.sender_type = "user"
    sender.tenant_key = ""
    # sender_id is a UserId object with open_id, not a plain string
    user_id_obj = MagicMock()
    user_id_obj.open_id = sender_id
    user_id_obj.union_id = f"on_{sender_id}"
    user_id_obj.user_id = None
    sender.sender_id = user_id_obj

    event_data = MagicMock()
    event_data.message = message
    event_data.sender = sender

    data = MagicMock()
    data.event = event_data
    data.header = MagicMock()
    data.header.event_id = "evt_" + message_id
    data.header.event_type = "im.message.receive_v1"

    return data


class TestLarkWsConversion:
    """Test SDK event → FeishuEvent conversion."""

    def test_basic_message_conversion(self) -> None:
        data = _make_mock_sdk_event()
        result = _sdk_event_to_feishu_event(data)
        assert result is not None
        assert result.source_ref == "om_ws_001"
        assert result.source_type == "message"
        assert "SQLite" in result.content
        assert result.payload["chat_id"] == "oc_test_chat"

    def test_missing_message_id_returns_none(self) -> None:
        data = _make_mock_sdk_event(message_id="")
        result = _sdk_event_to_feishu_event(data)
        assert result is None

    def test_missing_content_returns_none(self) -> None:
        data = _make_mock_sdk_event(content="")
        result = _sdk_event_to_feishu_event(data)
        assert result is None

    def test_scope_is_user_for_unmapped_group_chat(self) -> None:
        data = _make_mock_sdk_event(chat_id="oc_group_123")
        result = _sdk_event_to_feishu_event(data)
        assert result is not None
        assert result.scope == Scope.USER

    def test_configured_chat_maps_to_project(self) -> None:
        configure_project_chat({"oc_group_123": "proj_ws"})
        try:
            data = _make_mock_sdk_event(chat_id="oc_group_123")
            result = _sdk_event_to_feishu_event(data)
            assert result is not None
            assert result.scope == Scope.PROJECT
            assert result.project_id == "proj_ws"
        finally:
            configure_project_chat({})

    def test_scope_is_user_for_empty_chat(self) -> None:
        data = _make_mock_sdk_event(chat_id="")
        result = _sdk_event_to_feishu_event(data)
        assert result is not None
        assert result.scope == Scope.USER

    def test_sender_becomes_actor(self) -> None:
        data = _make_mock_sdk_event(sender_id="ou_zhangsan")
        result = _sdk_event_to_feishu_event(data)
        assert result is not None
        assert "ou_zhangsan" in result.actors
        assert result.user_id == "ou_zhangsan"


class TestExtractTextContent:
    def test_plain_string(self) -> None:
        assert _extract_text_content("hello world") == "hello world"

    def test_json_with_text_key(self) -> None:
        assert _extract_text_content('{"text": "hello"}') == "hello"

    def test_json_with_content_key(self) -> None:
        assert _extract_text_content('{"content": "decision"}') == "decision"

    def test_empty_string(self) -> None:
        assert _extract_text_content("") == ""

    def test_dict_input(self) -> None:
        assert _extract_text_content({"text": "from dict"}) == "from dict"


class TestNormalizeTimestamp:
    def test_unix_millis(self) -> None:
        result = _normalize_timestamp("1714277400000")
        assert "T" in result
        assert "2024" in result

    def test_iso_string_passthrough(self) -> None:
        iso = "2026-04-28T10:30:00+08:00"
        assert _normalize_timestamp(iso) == iso

    def test_empty_returns_now(self) -> None:
        result = _normalize_timestamp("")
        assert "T" in result

    def test_none_returns_now(self) -> None:
        result = _normalize_timestamp(None)
        assert "T" in result


class TestLarkWsAdapterStream:
    """Test stream_events with a mocked SDK client thread."""

    def test_missing_sdk_raises_setup_error_when_loaded(self) -> None:
        import importlib.util

        if importlib.util.find_spec("lark_oapi") is None:
            with pytest.raises(SetupError, match="lark-oapi not installed"):
                _load_lark()

    def test_events_from_queue_are_yielded(self) -> None:
        adapter = LarkWsAdapter(app_id="test", app_secret="test")
        event = _sdk_event_to_feishu_event(_make_mock_sdk_event())
        assert event is not None

        adapter._q.put(event)
        adapter._q.put(None)  # sentinel to stop

        events = list(adapter.stream_events())
        assert len(events) == 1
        assert events[0].source_ref == "om_ws_001"

    def test_close_stops_stream(self) -> None:
        adapter = LarkWsAdapter(app_id="test", app_secret="test")
        adapter.close()
        assert adapter._stop_consumer.is_set()

    def test_chat_filter_skips_disallowed_chats(self) -> None:
        adapter = LarkWsAdapter(
            app_id="test",
            app_secret="test",
            allowed_chat_ids={"oc_allowed"},
        )
        event_wrong = _sdk_event_to_feishu_event(_make_mock_sdk_event(chat_id="oc_wrong"))
        event_right = _sdk_event_to_feishu_event(_make_mock_sdk_event(message_id="om_right", chat_id="oc_allowed"))
        assert event_wrong is not None
        assert event_right is not None

        # Simulate handler: only allowed chat event should be put in queue
        chat_id = event_wrong.payload.get("chat_id", "")
        assert adapter._allowed_chat_ids and chat_id not in adapter._allowed_chat_ids

        adapter._q.put(event_right)
        adapter._q.put(None)

        events = list(adapter.stream_events())
        assert len(events) == 1
        assert events[0].source_ref == "om_right"

    def test_multiple_events_streamed(self) -> None:
        adapter = LarkWsAdapter(app_id="test", app_secret="test")
        e1 = _sdk_event_to_feishu_event(_make_mock_sdk_event(message_id="om_a"))
        e2 = _sdk_event_to_feishu_event(_make_mock_sdk_event(message_id="om_b"))
        assert e1 is not None
        assert e2 is not None

        adapter._q.put(e1)
        adapter._q.put(e2)
        adapter._q.put(None)

        events = list(adapter.stream_events())
        assert len(events) == 2
        assert events[0].source_ref == "om_a"
        assert events[1].source_ref == "om_b"

    def test_queue_full_drops_event(self) -> None:
        adapter = LarkWsAdapter(app_id="test", app_secret="test", queue_size=1)
        e1 = _sdk_event_to_feishu_event(_make_mock_sdk_event(message_id="om_1"))
        assert e1 is not None
        adapter._q.put(e1)
        # Queue is full; put_nowait should raise Full
        e2 = _sdk_event_to_feishu_event(_make_mock_sdk_event(message_id="om_2"))
        assert e2 is not None
        import queue as q
        with pytest.raises(q.Full):
            adapter._q.put_nowait(e2)


class TestLarkWsIngestDaemon:
    def test_required_env_raises_when_missing(self, monkeypatch) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _required_env

        monkeypatch.delenv("LARK_APP_SECRET", raising=False)
        with pytest.raises(RuntimeError, match="LARK_APP_SECRET"):
            _required_env("LARK_APP_SECRET")

    def test_ingest_event_uses_feishu_pipeline(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _ingest_event

        event = _sdk_event_to_feishu_event(
            _make_mock_sdk_event(
                message_id="om_daemon_1",
                content=json.dumps({"text": DECISION_TEXT}, ensure_ascii=False),
            )
        )
        assert event is not None
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            assert _ingest_event(engine, event)
            row = engine.conn.execute(
                "SELECT source_type, source_ref, content_hash FROM events WHERE source_ref = ?",
                ("om_daemon_1",),
            ).fetchone()
            assert row["source_type"] == "message"
            assert row["content_hash"] == event._content_hash

    def test_ingest_event_sends_memory_card_when_reply_client_present(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _ingest_event

        class ReplyClient:
            def __init__(self) -> None:
                self.cards = []

            def send_memory_card(self, **kwargs):
                self.cards.append(kwargs)
                return True

        event = _sdk_event_to_feishu_event(
            _make_mock_sdk_event(
                message_id="om_daemon_reply_1",
                content=json.dumps({"text": DECISION_TEXT}, ensure_ascii=False),
            )
        )
        assert event is not None
        reply_client = ReplyClient()
        with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
            assert _ingest_event(engine, event, reply_client=reply_client)
        assert len(reply_client.cards) == 1
        assert reply_client.cards[0]["chat_id"] == "oc_test_chat"

    def test_check_triggers_runs_without_new_memory_write(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers

        class ReplyClient:
            def __init__(self) -> None:
                self.related = []

            def send_related_memories_card(self, chat_id, memories, parent_id=None):
                self.related.append((chat_id, memories, parent_id))
                return True

        event = _sdk_event_to_feishu_event(
            _make_mock_sdk_event(
                message_id="om_daemon_trigger_1",
                chat_id="oc_trigger_chat",
                content=json.dumps({"text": "what was the previous SQLite decision?"}, ensure_ascii=False),
            )
        )
        assert event is not None

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            seeded = runtime_dir / "seed.jsonl"
            seeded.write_text(FIXTURE_JSONL, encoding="utf-8")
            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                run_ingest(FixtureAdapter(seeded), engine)
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)
            assert len(reply_client.related) == 1
            assert reply_client.related[0][0] == "oc_test_chat"
        finally:
            configure_project_chat({})

    def test_related_push_requires_topic_overlap(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers

        class ReplyClient:
            def __init__(self) -> None:
                self.related = []

            def send_related_memories_card(self, chat_id, memories, parent_id=None):
                self.related.append((chat_id, memories, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            seeded = runtime_dir / "seed.jsonl"
            unrelated = "\u51b3\u5b9a\u91c7\u7528 PostgreSQL \u66ff\u4ee3 SQLite"
            seeded.write_text(_fixture_line(source_ref="om_unrelated", content=unrelated), encoding="utf-8")
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_daemon_trigger_2",
                    chat_id="oc_trigger_chat",
                    content=json.dumps(
                        {"text": "\u4e4b\u524d\u6211\u4eec\u51b3\u5b9a\u7684\u98de\u4e66\u4e3b\u52a8\u56de\u590d\u5165\u53e3\u662f\u4ec0\u4e48\uff1f"},
                        ensure_ascii=False,
                    ),
                )
            )
            assert event is not None
            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                run_ingest(FixtureAdapter(seeded), engine)
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)
            assert reply_client.related == []
        finally:
            configure_project_chat({})

    def test_related_push_falls_back_for_generic_previous_decision_question(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers

        class ReplyClient:
            def __init__(self) -> None:
                self.related = []

            def send_related_memories_card(self, chat_id, memories, parent_id=None):
                self.related.append((chat_id, memories, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_daemon_trigger_generic",
                    chat_id="oc_trigger_chat",
                    content=json.dumps(
                        {"text": "\u8fd9\u4e2a\u65b9\u6848\u548c\u4e4b\u524d\u7684\u51b3\u5b9a\u6709\u5173\u5417\uff1f"},
                        ensure_ascii=False,
                    ),
                )
            )
            assert event is not None
            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                engine.write(
                    event=SourceEvent(
                        source_type="message",
                        source_ref="om_sqlite_decision",
                        actors=["ou_user1"],
                        timestamp="2026-05-05T00:00:00+00:00",
                        content="\u51b3\u5b9a\uff1a\u672c\u9879\u76ee\u4f7f\u7528 SQLite \u4f5c\u4e3a\u672c\u5730 memory engine \u5b58\u50a8\u3002",
                        scope="project",
                    ),
                    memory_candidates=[
                        MemoryCandidate(
                            memory_type="decision",
                            title="\u51b3\u5b9a\uff1a\u4f7f\u7528 SQLite \u4f5c\u4e3a\u672c\u5730\u5b58\u50a8",
                            summary="\u672c\u9879\u76ee\u4f7f\u7528 SQLite \u4f5c\u4e3a\u672c\u5730 memory engine \u5b58\u50a8\u3002",
                            content={"scope": "project"},
                            confidence=0.6,
                            evidence=[{"source_ref": "om_sqlite_decision"}],
                        )
                    ],
                    project_id="proj_alpha",
                    user_id="ou_user1",
                )
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)
            assert len(reply_client.related) == 1
            assert "SQLite" in reply_client.related[0][1][0]["title"]
        finally:
            configure_project_chat({})

    def test_related_push_matches_daemon_topic(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers

        class ReplyClient:
            def __init__(self) -> None:
                self.related = []

            def send_related_memories_card(self, chat_id, memories, parent_id=None):
                self.related.append((chat_id, memories, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            seeded = runtime_dir / "seed.jsonl"
            daemon_decision = "\u6211\u4eec\u51b3\u5b9a\u540e\u7eed\u9ed8\u8ba4\u4f7f\u7528 daemon \u4f5c\u4e3a\u98de\u4e66\u4e3b\u52a8\u56de\u590d\u5165\u53e3"
            seeded.write_text(_fixture_line(source_ref="om_daemon_decision", content=daemon_decision), encoding="utf-8")
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_daemon_trigger_3",
                    chat_id="oc_trigger_chat",
                    content=json.dumps(
                        {"text": "\u4e4b\u524d\u6211\u4eec\u51b3\u5b9a\u7684\u98de\u4e66\u4e3b\u52a8\u56de\u590d\u5165\u53e3\u662f\u4ec0\u4e48\uff1f"},
                        ensure_ascii=False,
                    ),
                )
            )
            assert event is not None
            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                run_ingest(FixtureAdapter(seeded), engine)
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)
            assert len(reply_client.related) == 1
            assert "daemon" in reply_client.related[0][1][0]["title"]
        finally:
            configure_project_chat({})

    def test_summary_push_includes_l1_decision_at_live_capture_confidence(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_daemon_summary",
                    chat_id="oc_trigger_chat",
                    content=json.dumps({"text": "\u603b\u7ed3\u4e00\u4e0b\u5f53\u524d\u9879\u76ee\u8bb0\u5fc6"}, ensure_ascii=False),
                )
            )
            assert event is not None
            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                engine.write(
                    event=SourceEvent(
                        source_type="message",
                        source_ref="om_summary_sqlite_decision",
                        actors=["ou_user1"],
                        timestamp="2026-05-05T00:00:00+00:00",
                        content="\u51b3\u5b9a\uff1a\u672c\u9879\u76ee\u4f7f\u7528 SQLite \u4f5c\u4e3a\u672c\u5730 memory engine \u5b58\u50a8\u3002",
                        scope="project",
                    ),
                    memory_candidates=[
                        MemoryCandidate(
                            memory_type="decision",
                            title="\u51b3\u5b9a\uff1a\u4f7f\u7528 SQLite \u4f5c\u4e3a\u672c\u5730\u5b58\u50a8",
                            summary="\u672c\u9879\u76ee\u4f7f\u7528 SQLite \u4f5c\u4e3a\u672c\u5730 memory engine \u5b58\u50a8\u3002",
                            content={"scope": "project"},
                            confidence=0.6,
                            evidence=[{"source_ref": "om_summary_sqlite_decision"}],
                        )
                    ],
                    project_id="proj_alpha",
                    user_id="ou_user1",
                )
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)
            assert len(reply_client.texts) == 1
            assert "最新决策" in reply_client.texts[0][1]
            assert "SQLite" in reply_client.texts[0][1]
        finally:
            configure_project_chat({})

    def test_related_card_hides_debug_metadata(self) -> None:
        from feishu_ingest.adapters.reply import FeishuReplyClient

        client = FeishuReplyClient.__new__(FeishuReplyClient)
        sent: list[tuple[str, str, str | None]] = []

        def send_text(chat_id: str, text: str, parent_id: str | None = None) -> bool:
            sent.append((chat_id, text, parent_id))
            return True

        client.send_text = send_text
        ok = client.send_related_memories_card(
            "oc_test_chat",
            [
                {
                    "memory_type": "decision",
                    "title": "Use daemon-first Feishu replies",
                    "logical_layer": "L1",
                    "score": 0.89,
                }
            ],
        )

        assert ok
        assert sent[0][1] == "相关记忆：\n- Use daemon-first Feishu replies"
        assert "score=" not in sent[0][1]
        assert "Use this as historical context" not in sent[0][1]

    def test_memory_card_hides_internal_metadata_for_demo(self) -> None:
        from feishu_ingest.adapters.reply import FeishuReplyClient

        client = FeishuReplyClient.__new__(FeishuReplyClient)
        sent: list[tuple[str, str, str | None]] = []

        def send_text(chat_id: str, text: str, parent_id: str | None = None) -> bool:
            sent.append((chat_id, text, parent_id))
            return True

        client.send_text = send_text
        ok = client.send_memory_card(
            chat_id="oc_test_chat",
            memory_type="decision",
            title="\u51b3\u5b9a\uff1a\u4f7f\u7528 SQLite \u4f5c\u4e3a\u672c\u5730\u5b58\u50a8",
            summary="\u672c\u9879\u76ee\u4f7f\u7528 SQLite \u4f5c\u4e3a\u672c\u5730 memory engine \u5b58\u50a8\u3002",
            confidence=0.6,
            layer="L1",
            evidence_ref="om_test",
        )

        assert ok
        assert sent[0][1] == "已记录\n本项目使用 SQLite 作为本地 memory engine 存储。"
        assert "Type:" not in sent[0][1]
        assert "Confidence:" not in sent[0][1]
        assert "Layer:" not in sent[0][1]
        assert "Evidence:" not in sent[0][1]

    def test_preference_reminder_ignores_implicit_candidates(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _push_preference_reminder
        from memory_engine.models import MemoryCandidate
        from feishu_ingest.scope import configure_project_chat, infer_project_id, infer_scope

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_pref_candidate_1",
                    chat_id="oc_trigger_chat",
                    content=json.dumps({"text": "current task: daemon reply plan"}, ensure_ascii=False),
                )
            )
            assert event is not None
            assert infer_scope(event).value == "project"
            assert infer_project_id(event) == "proj_alpha"
            candidate = MemoryCandidate(
                memory_type="preference",
                title="Possible preference: daemon reply plan",
                summary="Aggregated observations for daemon reply plan",
                content={"scope": "project", "kind": "preference_candidate"},
                importance=0.4,
                confidence=0.7,
                evidence=[{"source_ref": "msg://pref-candidate"}],
            )

            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                engine.write(
                    event=event.to_source_event(),
                    project_id="proj_alpha",
                    user_id=event.user_id,
                    memory_candidates=[candidate],
                )
                reply_client = ReplyClient()
                _push_preference_reminder(engine, event, "oc_test_chat", reply_client)

            assert reply_client.texts == []
        finally:
            configure_project_chat({})

    def test_preference_reminder_uses_stable_preference_and_cooldown(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _push_preference_reminder
        from memory_engine.models import MemoryCandidate
        from feishu_ingest.scope import configure_project_chat

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_pref_stable_1",
                    chat_id="oc_trigger_chat",
                    content=json.dumps({"text": "current task: daemon reply plan"}, ensure_ascii=False),
                )
            )
            assert event is not None
            preference = MemoryCandidate(
                memory_type="preference",
                title="Prefer daemon reply plan before action",
                summary="User prefers daemon reply plan before action.",
                content={"scope": "project"},
                importance=0.6,
                confidence=0.9,
                evidence=[{"source_ref": "msg://stable-pref"}],
            )

            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                engine.write(
                    event=event.to_source_event(),
                    project_id="proj_alpha",
                    user_id=event.user_id,
                    memory_candidates=[preference],
                )
                reply_client = ReplyClient()
                _push_preference_reminder(engine, event, "oc_test_chat", reply_client)
                _push_preference_reminder(engine, event, "oc_test_chat", reply_client)

            assert len(reply_client.texts) == 1
            assert "Prefer daemon reply plan" in reply_client.texts[0][1]
        finally:
            configure_project_chat({})

    def test_preference_candidate_confirm_command_confirms_candidate(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers
        from memory_engine.models import MemoryCandidate
        from feishu_ingest.scope import configure_project_chat

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_pref_confirm_1",
                    chat_id="oc_trigger_chat",
                    content=json.dumps({"text": "\u786e\u8ba4\u504f\u597d pref.output.structured_format"}, ensure_ascii=False),
                )
            )
            assert event is not None
            candidate = MemoryCandidate(
                memory_type="preference",
                title="Possible preference: output_format",
                summary="Aggregated 3 observations for pref.output.structured_format",
                content={
                    "scope": "project",
                    "kind": "preference_candidate",
                    "preference_kind": "output_format",
                    "pattern_key": "pref.output.structured_format",
                    "positive_evidence_count": "3",
                },
                importance=0.5,
                confidence=0.65,
                evidence=[{"source_ref": "msg://pref-candidate"}],
            )

            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                write = engine.write(
                    event=event.to_source_event(),
                    project_id="proj_alpha",
                    user_id=event.user_id,
                    memory_candidates=[candidate],
                )
                candidate_id = write["memory_ids"][0]
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)

                archived = engine.conn.execute("SELECT status FROM memories WHERE id = ?", (candidate_id,)).fetchone()
                stable_count = engine.conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE content_json LIKE '%stable_preference%'"
                ).fetchone()[0]

            assert "\u5df2\u786e\u8ba4\u504f\u597d" in reply_client.texts[0][1]
            assert archived["status"] == "archived"
            assert stable_count == 1
        finally:
            configure_project_chat({})

    def test_preference_candidate_reject_command_archives_candidate(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers
        from memory_engine.models import MemoryCandidate
        from feishu_ingest.scope import configure_project_chat

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_pref_reject_1",
                    chat_id="oc_trigger_chat",
                    content=json.dumps({"text": "\u62d2\u7edd\u504f\u597d pref.output.structured_format"}, ensure_ascii=False),
                )
            )
            assert event is not None
            candidate = MemoryCandidate(
                memory_type="preference",
                title="Possible preference: output_format",
                summary="Aggregated 3 observations for pref.output.structured_format",
                content={
                    "scope": "project",
                    "kind": "preference_candidate",
                    "preference_kind": "output_format",
                    "pattern_key": "pref.output.structured_format",
                    "positive_evidence_count": "3",
                },
                importance=0.5,
                confidence=0.65,
                evidence=[{"source_ref": "msg://pref-candidate"}],
            )

            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                write = engine.write(
                    event=event.to_source_event(),
                    project_id="proj_alpha",
                    user_id=event.user_id,
                    memory_candidates=[candidate],
                )
                candidate_id = write["memory_ids"][0]
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)

                archived = engine.conn.execute("SELECT status FROM memories WHERE id = ?", (candidate_id,)).fetchone()
                stable_count = engine.conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE content_json LIKE '%stable_preference%'"
                ).fetchone()[0]

            assert "\u5df2\u62d2\u7edd\u504f\u597d" in reply_client.texts[0][1]
            assert archived["status"] == "archived"
            assert stable_count == 0
        finally:
            configure_project_chat({})

    def test_preference_confirm_command_reconfirms_stable_preference_under_review(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers
        from memory_engine.models import MemoryCandidate
        from feishu_ingest.scope import configure_project_chat

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_stable_pref_confirm_1",
                    chat_id="oc_trigger_chat",
                    content=json.dumps({"text": "\u786e\u8ba4\u504f\u597d pref.output.structured_format"}, ensure_ascii=False),
                )
            )
            assert event is not None
            stable = MemoryCandidate(
                memory_type="preference",
                title="Confirmed preference: output_format",
                summary="User prefers structured output.",
                content={
                    "scope": "project",
                    "kind": "stable_preference",
                    "preference_kind": "output_format",
                    "pattern_key": "pref.output.structured_format",
                    "confirmed": "true",
                    "needs_confirmation": "false",
                    "needs_review": "true",
                    "review_reason": "negative implicit preference evidence observed",
                },
                importance=0.65,
                confidence=0.55,
                evidence=[{"source_ref": "msg://stable-pref-review"}],
            )

            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                write = engine.write(
                    event=event.to_source_event(),
                    project_id="proj_alpha",
                    user_id=event.user_id,
                    memory_candidates=[stable],
                )
                stable_id = write["memory_ids"][0]
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)

                row = engine.conn.execute(
                    "SELECT content_json, confidence, status FROM memories WHERE id = ?",
                    (stable_id,),
                ).fetchone()
                content = json.loads(row["content_json"])
                entries = engine.find_related_events(relation="reconfirmed_stable_preference", project_id="proj_alpha")

            assert "\u5df2\u91cd\u65b0\u786e\u8ba4\u504f\u597d" in reply_client.texts[0][1]
            assert row["status"] == "active"
            assert content["needs_review"] == "false"
            assert "review_reason" not in content
            assert float(row["confidence"]) >= 0.75
            assert len(entries) == 1
        finally:
            configure_project_chat({})

    def test_preference_reject_command_rejects_stable_preference_under_review(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers
        from memory_engine.models import MemoryCandidate
        from feishu_ingest.scope import configure_project_chat

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_stable_pref_reject_1",
                    chat_id="oc_trigger_chat",
                    content=json.dumps({"text": "\u62d2\u7edd\u504f\u597d pref.output.structured_format"}, ensure_ascii=False),
                )
            )
            assert event is not None
            stable = MemoryCandidate(
                memory_type="preference",
                title="Confirmed preference: output_format",
                summary="User prefers structured output.",
                content={
                    "scope": "project",
                    "kind": "stable_preference",
                    "preference_kind": "output_format",
                    "pattern_key": "pref.output.structured_format",
                    "confirmed": "true",
                    "needs_confirmation": "false",
                    "needs_review": "true",
                    "review_reason": "negative implicit preference evidence observed",
                },
                importance=0.65,
                confidence=0.55,
                evidence=[{"source_ref": "msg://stable-pref-review"}],
            )

            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                write = engine.write(
                    event=event.to_source_event(),
                    project_id="proj_alpha",
                    user_id=event.user_id,
                    memory_candidates=[stable],
                )
                stable_id = write["memory_ids"][0]
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)

                row = engine.conn.execute(
                    "SELECT content_json, confidence, status FROM memories WHERE id = ?",
                    (stable_id,),
                ).fetchone()
                content = json.loads(row["content_json"])
                entries = engine.find_related_events(relation="rejected_stable_preference", project_id="proj_alpha")

            assert "\u5df2\u62d2\u7edd\u7a33\u5b9a\u504f\u597d" in reply_client.texts[0][1]
            assert row["status"] == "archived"
            assert content["needs_review"] == "false"
            assert content["confirmed"] == "false"
            assert "review_reason" not in content
            assert float(row["confidence"]) <= 0.4
            assert len(entries) == 1
        finally:
            configure_project_chat({})

    def test_workflow_strategy_confirm_command_creates_skill(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers
        from memory_engine.models import MemoryCandidate
        from feishu_ingest.scope import configure_project_chat

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_workflow_confirm_1",
                    chat_id="oc_trigger_chat",
                    content=json.dumps({"text": "\u786e\u8ba4\u5de5\u4f5c\u6d41 test_verification_workflow"}, ensure_ascii=False),
                )
            )
            assert event is not None
            candidate = MemoryCandidate(
                memory_type="procedural",
                title="Workflow strategy candidate: test_verification_workflow",
                summary="Reuse candidate for test verification workflows.",
                content={
                    "scope": "project",
                    "kind": "workflow_strategy_candidate",
                    "task_type": "test_verification_workflow",
                    "success_evidence_count": "2",
                    "failure_evidence_count": "0",
                    "needs_confirmation": "true",
                    "confirmed": "false",
                },
                importance=0.62,
                confidence=0.6,
                evidence=[{"source_ref": "msg://workflow-candidate"}],
            )

            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                write = engine.write(
                    event=event.to_source_event(),
                    project_id="proj_alpha",
                    user_id=event.user_id,
                    memory_candidates=[candidate],
                )
                candidate_id = write["memory_ids"][0]
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)

                archived = engine.conn.execute("SELECT status FROM memories WHERE id = ?", (candidate_id,)).fetchone()
                skill_count = engine.conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE content_json LIKE '%workflow_skill%'"
                ).fetchone()[0]

            assert "Confirmed workflow strategy" in reply_client.texts[0][1]
            assert archived["status"] == "archived"
            assert skill_count == 1
        finally:
            configure_project_chat({})

    def test_workflow_strategy_reject_command_archives_candidate(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers
        from memory_engine.models import MemoryCandidate
        from feishu_ingest.scope import configure_project_chat

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_workflow_reject_1",
                    chat_id="oc_trigger_chat",
                    content=json.dumps({"text": "\u62d2\u7edd\u5de5\u4f5c\u6d41 test_verification_workflow"}, ensure_ascii=False),
                )
            )
            assert event is not None
            candidate = MemoryCandidate(
                memory_type="procedural",
                title="Workflow strategy candidate: test_verification_workflow",
                summary="Reuse candidate for test verification workflows.",
                content={
                    "scope": "project",
                    "kind": "workflow_strategy_candidate",
                    "task_type": "test_verification_workflow",
                    "success_evidence_count": "2",
                    "failure_evidence_count": "0",
                    "needs_confirmation": "true",
                    "confirmed": "false",
                },
                importance=0.62,
                confidence=0.6,
                evidence=[{"source_ref": "msg://workflow-candidate"}],
            )

            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                write = engine.write(
                    event=event.to_source_event(),
                    project_id="proj_alpha",
                    user_id=event.user_id,
                    memory_candidates=[candidate],
                )
                candidate_id = write["memory_ids"][0]
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)

                archived = engine.conn.execute("SELECT status FROM memories WHERE id = ?", (candidate_id,)).fetchone()
                skill_count = engine.conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE content_json LIKE '%workflow_skill%'"
                ).fetchone()[0]

            assert "Rejected workflow strategy" in reply_client.texts[0][1]
            assert archived["status"] == "archived"
            assert skill_count == 0
        finally:
            configure_project_chat({})

    def test_workflow_skill_confirm_command_reconfirms_skill_under_review(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers
        from memory_engine.models import MemoryCandidate
        from feishu_ingest.scope import configure_project_chat

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_workflow_skill_confirm_1",
                    chat_id="oc_trigger_chat",
                    content=json.dumps(
                        {"text": "\u786e\u8ba4\u5de5\u4f5c\u6d41\u6280\u80fd test_verification_workflow"},
                        ensure_ascii=False,
                    ),
                )
            )
            assert event is not None
            skill = MemoryCandidate(
                memory_type="procedural",
                title="Workflow skill: test_verification_workflow",
                summary="Reusable workflow skill for pytest verification.",
                content={
                    "scope": "project",
                    "kind": "workflow_skill",
                    "task_type": "test_verification_workflow",
                    "confirmed": "true",
                    "needs_confirmation": "false",
                    "needs_review": "true",
                    "review_reason": "workflow skill negative outcome evidence observed",
                },
                importance=0.72,
                confidence=0.55,
                evidence=[{"source_ref": "msg://workflow-skill-review"}],
            )

            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                write = engine.write(
                    event=event.to_source_event(),
                    project_id="proj_alpha",
                    user_id=event.user_id,
                    memory_candidates=[skill],
                )
                skill_id = write["memory_ids"][0]
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)

                row = engine.conn.execute(
                    "SELECT content_json, confidence, status FROM memories WHERE id = ?",
                    (skill_id,),
                ).fetchone()
                content = json.loads(row["content_json"])
                entries = engine.find_related_events(relation="reconfirmed_workflow_skill", project_id="proj_alpha")

            assert "Reconfirmed workflow skill" in reply_client.texts[0][1]
            assert row["status"] == "active"
            assert content["needs_review"] == "false"
            assert "review_reason" not in content
            assert float(row["confidence"]) >= 0.75
            assert len(entries) == 1
        finally:
            configure_project_chat({})

    def test_workflow_skill_reject_command_archives_skill_under_review(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers
        from memory_engine.models import MemoryCandidate
        from feishu_ingest.scope import configure_project_chat

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        configure_project_chat({"oc_trigger_chat": "proj_alpha"})
        try:
            event = _sdk_event_to_feishu_event(
                _make_mock_sdk_event(
                    message_id="om_workflow_skill_reject_1",
                    chat_id="oc_trigger_chat",
                    content=json.dumps(
                        {"text": "\u62d2\u7edd\u5de5\u4f5c\u6d41\u6280\u80fd test_verification_workflow"},
                        ensure_ascii=False,
                    ),
                )
            )
            assert event is not None
            skill = MemoryCandidate(
                memory_type="procedural",
                title="Workflow skill: test_verification_workflow",
                summary="Reusable workflow skill for pytest verification.",
                content={
                    "scope": "project",
                    "kind": "workflow_skill",
                    "task_type": "test_verification_workflow",
                    "confirmed": "true",
                    "needs_confirmation": "false",
                    "needs_review": "true",
                    "review_reason": "workflow skill negative outcome evidence observed",
                },
                importance=0.72,
                confidence=0.55,
                evidence=[{"source_ref": "msg://workflow-skill-review"}],
            )

            with MemoryEngine(db_path=runtime_dir / "test.db") as engine:
                write = engine.write(
                    event=event.to_source_event(),
                    project_id="proj_alpha",
                    user_id=event.user_id,
                    memory_candidates=[skill],
                )
                skill_id = write["memory_ids"][0]
                reply_client = ReplyClient()
                _check_triggers(engine, event, "oc_test_chat", reply_client)

                row = engine.conn.execute(
                    "SELECT content_json, confidence, status FROM memories WHERE id = ?",
                    (skill_id,),
                ).fetchone()
                content = json.loads(row["content_json"])
                entries = engine.find_related_events(relation="rejected_workflow_skill", project_id="proj_alpha")

            assert "Rejected workflow skill" in reply_client.texts[0][1]
            assert row["status"] == "archived"
            assert content["needs_review"] == "false"
            assert content["confirmed"] == "false"
            assert "review_reason" not in content
            assert float(row["confidence"]) <= 0.4
            assert len(entries) == 1
        finally:
            configure_project_chat({})

    def test_daemon_links_feishu_outcome_to_pushed_workflow_skill(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers
        from feishu_ingest.models import FeishuEvent
        from memory_engine.models import MemoryCandidate

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        db_path = runtime_dir / "test.db"
        with MemoryEngine(db_path=db_path) as engine:
            seed_event = FeishuEvent(
                source_type="message",
                source_ref="om_workflow_skill_seed",
                source_url=None,
                actors=["ou_test"],
                timestamp="2026-05-01T12:00:00+08:00",
                content="seed workflow skill",
                scope=Scope.PROJECT,
                project_id="proj_alpha",
                task_id="task_alpha",
                user_id="ou_test",
                payload={"chat_id": "oc_trigger_chat"},
            )
            write = engine.write(
                event=seed_event.to_source_event(),
                project_id="proj_alpha",
                task_id="task_alpha",
                user_id="ou_test",
                memory_candidates=[
                    MemoryCandidate(
                        memory_type="procedural",
                        title="Workflow skill: test_verification_workflow",
                        summary="Reusable workflow skill for pytest verification.",
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                        },
                        importance=0.75,
                        confidence=0.82,
                        evidence=[{"source_ref": "om_workflow_skill_seed"}],
                        tags=["workflow", "workflow_skill", "test_verification_workflow"],
                    )
                ],
            )
            skill_id = write["memory_ids"][0]
            reply_client = ReplyClient()

            trigger_event = FeishuEvent(
                source_type="message",
                source_ref="om_workflow_skill_trigger",
                source_url=None,
                actors=["ou_test"],
                timestamp="2026-05-01T12:01:00+08:00",
                content="current task pytest verification",
                scope=Scope.PROJECT,
                project_id="proj_alpha",
                task_id="task_alpha",
                user_id="ou_test",
                payload={"chat_id": "oc_trigger_chat"},
            )
            _check_triggers(engine, trigger_event, "oc_trigger_chat", reply_client)

        with MemoryEngine(db_path=db_path) as engine:
            untrusted_outcome_event = FeishuEvent(
                source_type="message",
                source_ref="om_workflow_skill_untrusted_outcome",
                source_url=None,
                actors=["ou_test"],
                timestamp="2026-05-01T12:04:00+08:00",
                content="pytest 3 passed, 0 failed",
                scope=Scope.PROJECT,
                project_id="proj_alpha",
                task_id="task_alpha",
                user_id="ou_test",
                payload={"chat_id": "oc_trigger_chat", "sender_type": "user"},
            )
            _check_triggers(engine, untrusted_outcome_event, "oc_trigger_chat", reply_client)
            untrusted_outcome_count = engine.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE content_json LIKE '%workflow_skill_outcome%'"
            ).fetchone()[0]

            outcome_event = FeishuEvent(
                source_type="message",
                source_ref="om_workflow_skill_outcome",
                source_url=None,
                actors=["cli_bot"],
                timestamp="2026-05-01T12:05:00+08:00",
                content="pytest 3 passed, 0 failed",
                scope=Scope.PROJECT,
                project_id="proj_alpha",
                task_id="task_alpha",
                user_id="cli_bot",
                payload={"chat_id": "oc_trigger_chat", "sender_type": "app"},
            )
            _check_triggers(engine, outcome_event, "oc_trigger_chat", reply_client)

            skill = engine.conn.execute(
                "SELECT content_json FROM memories WHERE id = ?",
                (skill_id,),
            ).fetchone()
            skill_content = json.loads(skill["content_json"])
            outcome_count = engine.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE content_json LIKE '%workflow_skill_outcome%'"
            ).fetchone()[0]
            entries = engine.find_related_events(relation="workflow_skill_succeeded", project_id="proj_alpha")

        assert any("Workflow skill:" in text for _, text, _ in reply_client.texts)
        assert untrusted_outcome_count == 0
        assert outcome_count == 1
        assert skill_content["usage_count"] == "1"
        assert skill_content["adoption_success_count"] == "1"
        assert len(entries) == 1

    def test_daemon_does_not_push_workflow_skill_needing_review(self, runtime_dir: Path) -> None:
        from feishu_ingest.lark_ws_ingest_daemon import _check_triggers
        from feishu_ingest.models import FeishuEvent
        from memory_engine.models import MemoryCandidate

        class ReplyClient:
            def __init__(self) -> None:
                self.texts = []

            def send_text(self, chat_id, text, parent_id=None):
                self.texts.append((chat_id, text, parent_id))
                return True

        db_path = runtime_dir / "test.db"
        with MemoryEngine(db_path=db_path) as engine:
            seed_event = FeishuEvent(
                source_type="message",
                source_ref="om_workflow_skill_review_seed",
                source_url=None,
                actors=["ou_test"],
                timestamp="2026-05-01T12:00:00+08:00",
                content="seed stale workflow skill",
                scope=Scope.PROJECT,
                project_id="proj_alpha",
                task_id="task_alpha",
                user_id="ou_test",
                payload={"chat_id": "oc_trigger_chat"},
            )
            engine.write(
                event=seed_event.to_source_event(),
                project_id="proj_alpha",
                task_id="task_alpha",
                user_id="ou_test",
                memory_candidates=[
                    MemoryCandidate(
                        memory_type="procedural",
                        title="Workflow skill: test_verification_workflow",
                        summary="Reusable workflow skill for pytest verification.",
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                            "needs_review": "true",
                        },
                        importance=0.75,
                        confidence=0.82,
                        evidence=[{"source_ref": "om_workflow_skill_review_seed"}],
                        tags=["workflow", "workflow_skill", "test_verification_workflow"],
                    )
                ],
            )
            reply_client = ReplyClient()
            trigger_event = FeishuEvent(
                source_type="message",
                source_ref="om_workflow_skill_review_trigger",
                source_url=None,
                actors=["ou_test"],
                timestamp="2026-05-01T12:01:00+08:00",
                content="current task pytest verification",
                scope=Scope.PROJECT,
                project_id="proj_alpha",
                task_id="task_alpha",
                user_id="ou_test",
                payload={"chat_id": "oc_trigger_chat"},
            )

            _check_triggers(engine, trigger_event, "oc_trigger_chat", reply_client)

        assert not any("Workflow skill:" in text for _, text, _ in reply_client.texts)


class TestReplyTriggers:
    def test_preference_candidate_command_parser(self) -> None:
        assert parse_preference_candidate_command("\u786e\u8ba4\u504f\u597d pref.output.structured_format") == (
            "confirm",
            "pref.output.structured_format",
        )
        assert parse_preference_candidate_command("\u62d2\u7edd\u504f\u597d pref.output.structured_format") == (
            "reject",
            "pref.output.structured_format",
        )
        assert parse_preference_candidate_command("current task progress") is None

    def test_workflow_strategy_command_parser(self) -> None:
        assert parse_workflow_strategy_command("\u786e\u8ba4\u5de5\u4f5c\u6d41 test_verification_workflow") == (
            "confirm",
            "test_verification_workflow",
        )
        assert parse_workflow_strategy_command("\u62d2\u7edd\u5de5\u4f5c\u6d41 test_verification_workflow") == (
            "reject",
            "test_verification_workflow",
        )
        assert parse_workflow_strategy_command(
            "\u786e\u8ba4\u5de5\u4f5c\u6d41\u6280\u80fd test_verification_workflow"
        ) == (
            "confirm",
            "test_verification_workflow",
        )
        assert parse_workflow_strategy_command(
            "\u62d2\u7edd\u5de5\u4f5c\u6d41\u6280\u80fd test_verification_workflow"
        ) == (
            "reject",
            "test_verification_workflow",
        )
        assert parse_workflow_strategy_command("confirm workflow skill test_verification_workflow") == (
            "confirm",
            "test_verification_workflow",
        )
        assert parse_workflow_strategy_command("reject workflow skill test_verification_workflow") == (
            "reject",
            "test_verification_workflow",
        )
        assert parse_workflow_strategy_command("current task progress") is None

    def test_related_trigger_matches_previous_context(self) -> None:
        assert is_related_trigger("what was the previous SQLite decision?")
        assert is_related_trigger("之前那个方案是什么？")

    def test_related_trigger_rejects_summary_text(self) -> None:
        assert not is_related_trigger("please summarize the project status")

    def test_summary_trigger_matches_summary_request(self) -> None:
        assert is_summary_trigger("summarize the current project")
        assert is_summary_trigger("给我总结一下当前项目")

    def test_summary_trigger_rejects_related_question(self) -> None:
        assert not is_summary_trigger("what was the previous decision?")

    def test_operation_trigger_matches_task_context(self) -> None:
        assert is_operation_trigger("current task progress")
        assert is_operation_trigger("当前任务进度")

    def test_operation_trigger_rejects_summary_text(self) -> None:
        assert not is_operation_trigger("please provide a summary")
