"""Tests for privacy guard (PII detection, masking) and audit logging."""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from memory_engine import MemoryCandidate, MemoryEngine, RecallRequest, SourceEvent
from memory_engine.guard import AuditAction, contains_api_key, scan_and_mask


class ScanAndMaskTest(unittest.TestCase):
    """Tests for guard.scan_and_mask and contains_api_key."""

    def test_detects_china_phone(self) -> None:
        result = scan_and_mask("请联系 13812345678 确认", "")
        self.assertTrue(result.has_sensitive)
        self.assertTrue(any(d["category"] == "china_phone" for d in result.detections))
        self.assertIn("[china_phone:REDACTED]", result.masked_content)
        self.assertNotIn("13812345678", result.masked_content)

    def test_detects_email(self) -> None:
        result = scan_and_mask("发到 test@example.com 就行", "")
        self.assertTrue(result.has_sensitive)
        self.assertTrue(any(d["category"] == "email" for d in result.detections))
        self.assertNotIn("test@example.com", result.masked_content)

    def test_detects_china_id(self) -> None:
        result = scan_and_mask("身份证号 110101199001011234", "")
        self.assertTrue(result.has_sensitive)
        self.assertTrue(any(d["category"] == "china_id" for d in result.detections))

    def test_detects_openai_key(self) -> None:
        result = scan_and_mask("key is sk-abcdefghijklmnopqrstuvwxyz123456", "")
        self.assertTrue(result.has_sensitive)
        self.assertTrue(any(d["category"] == "openai_key" for d in result.detections))

    def test_detects_bearer_token(self) -> None:
        result = scan_and_mask("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789", "")
        self.assertTrue(result.has_sensitive)
        self.assertTrue(any(d["category"] == "bearer_token" for d in result.detections))

    def test_detects_salary(self) -> None:
        result = scan_and_mask("薪资：25000", "")
        self.assertTrue(result.has_sensitive)
        self.assertTrue(any(d["category"] == "salary" for d in result.detections))

    def test_detects_password(self) -> None:
        result = scan_and_mask("password = MySecret123!", "")
        self.assertTrue(result.has_sensitive)
        self.assertTrue(any(d["category"] == "generic_secret" for d in result.detections))

    def test_masks_summary_too(self) -> None:
        result = scan_and_mask("clean content", "reach me at admin@corp.com")
        self.assertTrue(result.has_sensitive)
        self.assertNotIn("admin@corp.com", result.masked_summary)
        self.assertIn("[email:REDACTED]", result.masked_summary)

    def test_no_sensitive_returns_clean(self) -> None:
        result = scan_and_mask("这是一条普通的工作消息", "正常摘要")
        self.assertFalse(result.has_sensitive)
        self.assertEqual(result.masked_content, "这是一条普通的工作消息")
        self.assertEqual(result.masked_summary, "正常摘要")

    def test_contains_api_key_positive(self) -> None:
        self.assertTrue(contains_api_key("api_key=abcdef1234567890abcdef"))
        self.assertTrue(contains_api_key("sk-abcdef1234567890abcdef12"))

    def test_contains_api_key_negative(self) -> None:
        self.assertFalse(contains_api_key("这是一条普通消息"))


class AuditLogTest(unittest.TestCase):
    """Tests for audit logging in MemoryEngine."""

    def setUp(self) -> None:
        self.temp_dir = Path("tests_runtime") / str(uuid.uuid4())
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp_dir / "memory.sqlite3"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _engine(self) -> MemoryEngine:
        return MemoryEngine(self.db_path)

    def _event(self, content: str = "Test event.", **kwargs) -> SourceEvent:
        defaults = dict(source_type="message", source_ref="msg://1", actors=["u1"],
                        timestamp="2026-04-24T09:00:00+00:00", content=content, scope="project")
        defaults.update(kwargs)
        return SourceEvent(**defaults)

    def _candidate(self, title: str = "Test", summary: str = "Test summary.", **kwargs) -> MemoryCandidate:
        defaults = dict(memory_type="decision", title=title, summary=summary,
                        content={"scope": "project"}, importance=0.7, confidence=0.9,
                        evidence=[{"source_ref": "msg://1"}])
        defaults.update(kwargs)
        return MemoryCandidate(**defaults)

    def test_write_creates_audit_entries(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate()],
            )
            rows = engine.conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
            # expect: 1 event write + 1 memory write = 2 entries
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["action"], AuditAction.WRITE)
            self.assertEqual(rows[0]["target_type"], "event")
            self.assertEqual(rows[1]["action"], AuditAction.WRITE)
            self.assertEqual(rows[1]["target_type"], "memory")
            self.assertEqual(rows[1]["target_id"], result["memory_ids"][0])
            self.assertEqual(rows[0]["actor"], "u1")

    def test_update_creates_audit_entry(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate()],
            )
            old_id = result["memory_ids"][0]
            engine.update(
                memory_id=old_id,
                candidate=self._candidate(title="Updated", change_reason="修正"),
                event=self._event(content="Updated content", source_ref="msg://2"),
                project_id="p1", user_id="u1",
            )
            rows = engine.conn.execute(
                "SELECT * FROM audit_log WHERE action = ?", (AuditAction.UPDATE,)
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertIn("修正", rows[0]["detail"])

    def test_archive_creates_audit_entry(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(), project_id="p1",
                memory_candidates=[self._candidate()],
            )
            engine.archive(result["memory_ids"][0], reason="Outdated")
            rows = engine.conn.execute(
                "SELECT * FROM audit_log WHERE action = ?", (AuditAction.ARCHIVE,)
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertIn("Outdated", rows[0]["detail"])

    def test_invalidate_creates_audit_entry(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(), project_id="p1",
                memory_candidates=[self._candidate()],
            )
            engine.invalidate(result["memory_ids"][0], reason="Error")
            rows = engine.conn.execute(
                "SELECT * FROM audit_log WHERE action = ?", (AuditAction.INVALIDATE,)
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertIn("Error", rows[0]["detail"])

    def test_compact_creates_audit_entries(self) -> None:
        with self._engine() as engine:
            event_id = engine._insert_event(self._event(), "p1", None, "u1")
            engine._insert_memory(
                self._candidate(title="Dup A", summary="Duplicate content for merge test."),
                event_id=event_id, project_id="p1", task_id=None, user_id="u1",
            )
            event_id2 = engine._insert_event(self._event(source_ref="msg://2"), "p1", None, "u1")
            engine._insert_memory(
                self._candidate(title="Dup B", summary="Duplicate content for merge test.", confidence=0.7),
                event_id=event_id2, project_id="p1", task_id=None, user_id="u1",
            )
            engine.conn.commit()

            engine.compact()
            rows = engine.conn.execute(
                "SELECT * FROM audit_log WHERE action IN (?, ?)",
                (AuditAction.COMPACT_MERGE, AuditAction.COMPACT_ARCHIVE),
            ).fetchall()
            self.assertGreaterEqual(len(rows), 1)
            merge_rows = [r for r in rows if r["action"] == AuditAction.COMPACT_MERGE]
            self.assertEqual(len(merge_rows), 1)

    def test_pii_masking_in_write(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(content="请将报告发送到张三 13812345678"),
                project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(summary="张三的联系方式")],
            )
            self.assertIn("privacy_warnings", result)
            row = engine.conn.execute("SELECT content FROM events WHERE id = ?", (result["event_id"],)).fetchone()
            self.assertNotIn("13812345678", row["content"])
            self.assertIn("[china_phone:REDACTED]", row["content"])

    def test_pii_masking_in_update(self) -> None:
        """Regression: update() must mask PII, not bypass the privacy guard."""
        with self._engine() as engine:
            result = engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate()],
            )
            old_id = result["memory_ids"][0]
            engine.update(
                memory_id=old_id,
                candidate=self._candidate(summary="联系 13987654321 确认新方案"),
                event=self._event(content="更新：联系 13987654321 确认新方案", source_ref="msg://2"),
                project_id="p1", user_id="u1",
            )
            # event content must be masked
            event_row = engine.conn.execute("SELECT content FROM events WHERE id = 2").fetchone()
            self.assertNotIn("13987654321", event_row["content"])
            # memory summary must be masked
            mem_row = engine.conn.execute("SELECT summary FROM memories WHERE id = ?", (result["memory_ids"][0] + 1,)).fetchone()
            self.assertNotIn("13987654321", mem_row["summary"])

    def test_pii_masking_in_candidate_title_content_and_event_payload(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(
                    content="普通事件",
                    payload={"owner_email": "alice@example.com", "api_key": "abcdef1234567890abcdef"},
                ),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="联系 bob@example.com",
                        summary="正常摘要内容",
                        content={"scope": "project", "note": "电话 13812345678"},
                        evidence=[{"source_ref": "msg://1", "snippet": "password = MySecret123!"}],
                    )
                ],
            )
            self.assertIn("privacy_warnings", result)
            event_row = engine.conn.execute("SELECT payload_json FROM events WHERE id = ?", (result["event_id"],)).fetchone()
            self.assertNotIn("alice@example.com", event_row["payload_json"])
            self.assertNotIn("abcdef1234567890abcdef", event_row["payload_json"])
            mem_row = engine.conn.execute("SELECT title, content_json, evidence_json FROM memories WHERE id = ?", (result["memory_ids"][0],)).fetchone()
            self.assertNotIn("bob@example.com", mem_row["title"])
            self.assertNotIn("13812345678", mem_row["content_json"])
            self.assertNotIn("MySecret123!", mem_row["evidence_json"])

    def test_audit_log_persists_after_reopen(self) -> None:
        """Regression: audit entries must survive connection close + reopen."""
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate()],
            )
        # reopen same DB
        with self._engine() as engine:
            rows = engine.conn.execute("SELECT * FROM audit_log").fetchall()
            self.assertGreaterEqual(len(rows), 2)  # at least event + memory audit entries

    def test_compact_superseded_by_points_to_keeper(self) -> None:
        """Regression: superseded_by must point to the keeper's UUID, not the removed record's."""
        with self._engine() as engine:
            event_id = engine._insert_event(self._event(), "p1", None, "u1")
            engine._insert_memory(
                self._candidate(title="Dup A", summary="Duplicate content for merge test.", confidence=0.95),
                event_id=event_id, project_id="p1", task_id=None, user_id="u1",
            )
            event_id2 = engine._insert_event(self._event(source_ref="msg://2"), "p1", None, "u1")
            engine._insert_memory(
                self._candidate(title="Dup B", summary="Duplicate content for merge test.", confidence=0.7),
                event_id=event_id2, project_id="p1", task_id=None, user_id="u1",
            )
            engine.conn.commit()

            engine.compact()
            # the superseded record must point to the keeper's UUID
            superseded = engine.conn.execute("SELECT uuid, superseded_by FROM memories WHERE status = 'superseded'").fetchone()
            keeper = engine.conn.execute("SELECT uuid FROM memories WHERE status = 'active'").fetchone()
            self.assertIsNotNone(superseded)
            self.assertIsNotNone(keeper)
            self.assertEqual(superseded["superseded_by"], keeper["uuid"])

    def test_recall_log_rank_index_uses_actual_return_order(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(title="Alpha release", summary="alpha launch release"),
                    self._candidate(title="Alpha release duplicate", summary="alpha launch release"),
                ],
            )
            results = engine.recall(RecallRequest(query="alpha release", project_id="p1", user_id="u1"), limit=2)
            rows = engine.conn.execute(
                "SELECT memory_id, rank_index, was_returned FROM recall_log WHERE query = ? AND was_returned = 1 ORDER BY rank_index",
                ("alpha release",),
            ).fetchall()
            self.assertEqual(len(rows), len(results))
            self.assertEqual([row["memory_id"] for row in rows], [item["id"] for item in results])


if __name__ == "__main__":
    unittest.main()
