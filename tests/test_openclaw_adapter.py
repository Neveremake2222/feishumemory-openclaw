"""Tests for openclaw_adapter."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openclaw_adapter.dedupe import AdapterDedupe
from openclaw_adapter.engine_client import DirectEngineClient
from openclaw_adapter.injection import format_injection
from openclaw_adapter.recall_hook import _build_query, _filter_already_recalled
from openclaw_adapter.types import OpenClawContext, OpenClawEvent, WriteDecision, WriteResult
from openclaw_adapter.write_hook import WriteFilter, write


# =============================================================================
# Fixtures
# =============================================================================

def make_context(**kw) -> OpenClawContext:
    defaults = dict(
        user_id="ou_test",
        project_id="proj_alpha",
        task_id="t_review",
        latest_message="决定用 SQLite 作为本地 MVP 存储方案",
        current_task="完成 Phase 1 评测",
        open_files=("memory_engine/engine.py",),
        already_recalled_ids=(),
        session_id="sess_001",
    )
    defaults.update(kw)
    return OpenClawContext(**defaults)


def make_event(**kw) -> OpenClawEvent:
    defaults = dict(
        user_id="ou_test",
        project_id="proj_alpha",
        task_id="t_p1",
        user_message="运行所有测试",
        tool_name="pytest",
        tool_output="171 passed in 9.0s\n0 failed",
        assistant_summary=None,
        timestamp="2026-05-01T12:00:00+08:00",
        session_id="sess_003",
    )
    defaults.update(kw)
    return OpenClawEvent(**defaults)


# =============================================================================
# WriteFilter tests
# =============================================================================

class WriteFilterTest(unittest.TestCase):

    def test_decision_language_triggers_write(self) -> None:
        event = make_event(user_message="决定用 SQLite")
        decision = WriteFilter().classify(event)
        self.assertEqual(decision.action, "write")
        self.assertEqual(decision.memory_type, "decision")
        self.assertGreater(decision.confidence, 0.8)

    def test_decision_chose_english_triggers_write(self) -> None:
        event = make_event(user_message="I chose SQLite for the storage layer")
        decision = WriteFilter().classify(event)
        self.assertEqual(decision.action, "write")
        self.assertEqual(decision.memory_type, "decision")

    def test_completion_marker_triggers_write(self) -> None:
        event = make_event(tool_output="3 passed, 0 failed")
        decision = WriteFilter().classify(event)
        self.assertEqual(decision.action, "write")
        self.assertEqual(decision.memory_type, "task_status")

    def test_completion_chinese_triggers_write(self) -> None:
        event = make_event(tool_output="测试通过，成功完成")
        decision = WriteFilter().classify(event)
        self.assertEqual(decision.action, "write")
        self.assertEqual(decision.memory_type, "task_status")

    def test_preference_triggers_write(self) -> None:
        event = make_event(user_message="以后默认先写测试再改代码", tool_output=None)
        decision = WriteFilter().classify(event)
        self.assertEqual(decision.action, "write")
        self.assertEqual(decision.memory_type, "preference")

    def test_preference_prefer_triggers_write(self) -> None:
        event = make_event(user_message="I prefer to use SQLite for local storage", tool_output=None)
        decision = WriteFilter().classify(event)
        self.assertEqual(decision.action, "write")
        self.assertEqual(decision.memory_type, "preference")

    def test_implicit_preference_is_captured_when_normal_write_rejects(self) -> None:
        event = make_event(user_message="先别直接改，先分析一下再给我方案", tool_output=None)
        decision = WriteFilter().classify(event)
        self.assertEqual(decision.action, "reject")

    def test_implicit_preference_text_still_writes_via_write(self) -> None:
        event = make_event(user_message="先别直接改，先分析一下再给我方案", tool_output=None)
        with patch("openclaw_adapter.write_hook.DirectEngineClient") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.write.return_value = {"memory_ids": [1], "conflicts": []}
            result = write(event, db_path=":memory:")
        self.assertTrue(result.written)
        self.assertEqual(result.memory_ids, [1])

    def test_irrelevant_message_rejected(self) -> None:
        event = make_event(user_message="今天天气不错", tool_output=None)
        decision = WriteFilter().classify(event)
        self.assertEqual(decision.action, "reject")

    def test_empty_tool_output_rejected_without_decision_language(self) -> None:
        event = make_event(user_message="帮我看看这个文件", tool_output=None)
        decision = WriteFilter().classify(event)
        self.assertEqual(decision.action, "reject")


# =============================================================================
# Dedup tests
# =============================================================================

class DedupTest(unittest.TestCase):

    def setUp(self) -> None:
        AdapterDedupe._global.clear()

    def tearDown(self) -> None:
        AdapterDedupe._global.clear()

    def test_same_session_same_content_is_skipped(self) -> None:
        dedupe = AdapterDedupe("sess_x")
        event1 = make_event(user_message="决定用 SQLite", session_id="sess_x")
        event2 = make_event(user_message="决定用 SQLite", session_id="sess_x")
        decision = WriteDecision(action="write", reason="test", memory_type="decision",
                                 confidence=0.85, importance=0.8)

        self.assertFalse(dedupe.should_skip(event1, decision))
        dedupe.record(event1, decision, [42])
        self.assertTrue(dedupe.should_skip(event2, decision))

    def test_different_session_same_content_not_skipped(self) -> None:
        dedupe1 = AdapterDedupe("sess_a")
        dedupe2 = AdapterDedupe("sess_b")
        event1 = make_event(user_message="决定用 SQLite", session_id="sess_a")
        event2 = make_event(user_message="决定用 SQLite", session_id="sess_b")
        decision = WriteDecision(action="write", reason="test", memory_type="decision",
                                 confidence=0.85, importance=0.8)

        self.assertFalse(dedupe1.should_skip(event1, decision))
        dedupe1.record(event1, decision, [1])
        self.assertFalse(dedupe2.should_skip(event2, decision))


# =============================================================================
# InjectionFormatter tests
# =============================================================================

class InjectionFormatterTest(unittest.TestCase):

    def test_empty_results_produces_empty_string(self) -> None:
        self.assertEqual(format_injection([]), "")

    def test_tier1_injected_directly(self) -> None:
        results = [
            {
                "confidence": 0.9,
                "memory_type": "decision",
                "title": "Use SQLite for MVP",
                "summary": "SQLite chosen as local storage.",
                "evidence": [{"source_type": "message", "source_ref": "msg_1"}],
            }
        ]
        snippet = format_injection(results)
        self.assertIn("[DECISION] Use SQLite for MVP", snippet)
        self.assertIn("SQLite chosen as local storage", snippet)
        self.assertIn("Evidence: message:msg_1", snippet)

    def test_tier2_injected_with_caution(self) -> None:
        results = [
            {
                "confidence": 0.55,
                "memory_type": "preference",
                "title": "Prefers dark mode",
                "summary": "User prefers dark mode.",
                "evidence": [],
            }
        ]
        snippet = format_injection(results)
        self.assertIn("[PREFERENCE] Prefers dark mode", snippet)
        self.assertIn("confidence=0.55", snippet)
        self.assertIn("Verify this information before acting.", snippet)

    def test_tier3_omitted(self) -> None:
        results = [
            {
                "confidence": 0.3,
                "memory_type": "semantic",
                "title": "Old note",
                "summary": "This should be filtered out.",
                "evidence": [],
            }
        ]
        snippet = format_injection(results)
        self.assertNotIn("Old note", snippet)

    def test_multiple_results_separated_by_newline(self) -> None:
        results = [
            {
                "confidence": 0.9, "memory_type": "decision",
                "title": "Decision A", "summary": "Summary A", "evidence": [],
            },
            {
                "confidence": 0.55, "memory_type": "task_status",
                "title": "Task B", "summary": "Summary B", "evidence": [],
            },
        ]
        snippet = format_injection(results)
        self.assertIn("[DECISION] Decision A", snippet)
        self.assertIn("[TASK_STATUS] Task B", snippet)


# =============================================================================
# recall_hook query builder tests
# =============================================================================

class RecallQueryBuilderTest(unittest.TestCase):

    def test_builds_query_from_message_only(self) -> None:
        ctx = make_context(latest_message="项目截止时间确认了吗？",
                           project_id=None, current_task=None, open_files=())
        self.assertEqual(_build_query(ctx), "项目截止时间确认了吗？")

    def test_builds_query_with_project_id(self) -> None:
        ctx = make_context(latest_message="Phase 3 进展如何？", project_id="proj_alpha")
        q = _build_query(ctx)
        self.assertIn("Phase 3 进展如何？", q)
        self.assertIn("project: proj_alpha", q)

    def test_builds_query_with_current_task(self) -> None:
        ctx = make_context(latest_message="继续", current_task="完成 Phase 1 评测")
        q = _build_query(ctx)
        self.assertIn("继续", q)
        self.assertIn("task: 完成 Phase 1 评测", q)

    def test_builds_query_with_open_files(self) -> None:
        ctx = make_context(latest_message="继续", open_files=("src/engine.py", "src/api.py"))
        q = _build_query(ctx)
        self.assertIn("files: engine, api", q)

    def test_filters_already_recalled(self) -> None:
        results = [
            {"id": "1", "title": "Keep"},
            {"id": "2", "title": "Skip"},
            {"id": "3", "title": "Keep Too"},
        ]
        filtered = _filter_already_recalled(results, ("2",))
        titles = [r["title"] for r in filtered]
        self.assertNotIn("Skip", titles)
        self.assertIn("Keep", titles)
        self.assertIn("Keep Too", titles)


# =============================================================================
# recall() fail-open tests
# =============================================================================

class RecallFailOpenTest(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = Path("tests_runtime") / str(uuid.uuid4())
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp_dir / "memory.sqlite3"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_recall_with_failing_client_returns_empty_output(self) -> None:
        from openclaw_adapter.recall_hook import recall
        mock_client = MagicMock()
        mock_client.recall.side_effect = RuntimeError("boom")
        ctx = make_context()
        result = recall(ctx, client=mock_client)
        self.assertEqual(result.injection_md, "")
        self.assertEqual(result.results, [])


# =============================================================================
# write() fail-open tests
# =============================================================================

class WriteFailOpenTest(unittest.TestCase):

    def test_write_with_failing_client_returns_reject_result(self) -> None:
        mock_client = MagicMock()
        mock_client.write.side_effect = RuntimeError("boom")
        event = make_event(user_message="决定用 SQLite")
        result = write(event, client=mock_client)
        self.assertEqual(result.action, "reject")
        self.assertFalse(result.written)
        self.assertEqual(result.skip_reason, "engine write exception")

    def test_write_captures_workflow_failure_when_normal_filter_rejects(self) -> None:
        mock_client = MagicMock()
        mock_client.write.return_value = {"memory_ids": [7], "conflicts": []}
        event = make_event(
            user_message="please check the task",
            tool_name="pytest",
            tool_output="Traceback: fixture path missing\n1 failed",
            session_id="sess_workflow_failure",
        )

        result = write(event, client=mock_client)
        candidates = mock_client.write.call_args.kwargs["candidates"]

        self.assertTrue(result.written)
        self.assertEqual(result.memory_ids, [7])
        self.assertEqual(len(candidates), 2)
        self.assertEqual([c.content["kind"] for c in candidates], ["workflow_trace", "workflow_failure_case"])
        self.assertEqual(candidates[0].content["steps"][1]["phase"], "tool_call")
        self.assertEqual(candidates[0].content["steps"][2]["status"], "failed")

    def test_write_appends_workflow_success_to_task_completion(self) -> None:
        mock_client = MagicMock()
        mock_client.write.return_value = {"memory_ids": [8, 9], "conflicts": []}
        event = make_event(
            user_message="implemented memory engine change",
            tool_name="pytest",
            tool_output="3 passed, 0 failed",
            session_id="sess_workflow_success",
        )

        result = write(event, client=mock_client)
        candidates = mock_client.write.call_args.kwargs["candidates"]

        self.assertTrue(result.written)
        self.assertEqual([c.memory_type for c in candidates], ["task_status", "procedural", "procedural"])
        self.assertEqual(candidates[1].content["kind"], "workflow_trace")
        self.assertEqual(candidates[2].content["kind"], "workflow_success_case")

    def test_write_records_outcome_for_recalled_workflow_skill(self) -> None:
        mock_client = MagicMock()
        mock_client.write.return_value = {"memory_ids": [8, 9], "conflicts": []}
        mock_client.record_workflow_skill_outcome.return_value = {"outcome_memory_id": 12}
        event = make_event(
            user_message="implemented memory engine change",
            tool_name="pytest",
            tool_output="3 passed, 0 failed",
            session_id="sess_workflow_outcome",
            recalled_memory_ids=("42",),
        )

        result = write(event, client=mock_client)

        self.assertTrue(result.written)
        self.assertEqual(result.workflow_outcome_memory_ids, [12])
        mock_client.record_workflow_skill_outcome.assert_called_once()
        kwargs = mock_client.record_workflow_skill_outcome.call_args.kwargs
        self.assertEqual(mock_client.record_workflow_skill_outcome.call_args.args[0], 42)
        self.assertEqual(kwargs["outcome"], "success")
        self.assertEqual(kwargs["project_id"], "proj_alpha")

    def test_write_ignores_non_workflow_recalled_memory_for_outcome(self) -> None:
        mock_client = MagicMock()
        mock_client.write.return_value = {"memory_ids": [8, 9], "conflicts": []}
        mock_client.record_workflow_skill_outcome.side_effect = ValueError("not a workflow skill")
        event = make_event(
            user_message="implemented memory engine change",
            tool_name="pytest",
            tool_output="3 passed, 0 failed",
            session_id="sess_workflow_outcome_skip",
            recalled_memory_ids=("99",),
        )

        result = write(event, client=mock_client)

        self.assertTrue(result.written)
        self.assertEqual(result.workflow_outcome_memory_ids, [])
        mock_client.record_workflow_skill_outcome.assert_called_once()


# =============================================================================
# API endpoint tests (require fastapi + httpx)
# =============================================================================

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class APIEndpointsTest(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = Path("tests_runtime") / str(uuid.uuid4())
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp_dir / "memory.sqlite3"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_recall_endpoint_returns_injection_md(self) -> None:
        from openclaw_adapter.api import app

        client = TestClient(app)
        response = client.post("/recall", json={
            "query": "SQLite decision",
            "limit": 3,
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("injection_md", data)
        self.assertIsInstance(data["injection_md"], str)

    def test_write_endpoint_returns_write_result(self) -> None:
        from openclaw_adapter.api import app

        client = TestClient(app)
        response = client.post("/write", json={
            "user_message": "今天天气不错",
            "timestamp": "2026-05-01T12:00:00+08:00",
            "session_id": "test_sess",
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("action", data)
        self.assertFalse(data["written"])

    def test_health_endpoint(self) -> None:
        from openclaw_adapter.api import app

        client = TestClient(app)
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
