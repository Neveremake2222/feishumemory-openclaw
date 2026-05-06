from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from memory_engine import MemoryCandidate, MemoryEngine, RecallContext, RecallRequest, SourceEvent


class MemoryEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path("tests_runtime") / str(uuid.uuid4())
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp_dir / "memory.sqlite3"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _engine(self) -> MemoryEngine:
        return MemoryEngine(self.db_path)

    def _event(self, content: str = "Test event.", source_ref: str = "msg://1", **kwargs) -> SourceEvent:
        defaults = dict(source_type="message", source_ref=source_ref, actors=["u1"],
                        timestamp="2026-04-24T09:00:00+00:00", content=content, scope="project")
        defaults.update(kwargs)
        return SourceEvent(**defaults)

    def _candidate(self, title: str = "Test", summary: str = "Test summary.", memory_type: str = "decision",
                   importance: float = 0.7, confidence: float = 0.9, **kwargs) -> MemoryCandidate:
        defaults = dict(memory_type=memory_type, title=title, summary=summary,
                        content={"scope": "project"}, importance=importance, confidence=confidence,
                        evidence=[{"source_ref": "msg://1"}])
        defaults.update(kwargs)
        return MemoryCandidate(**defaults)

    def _stable_preference_under_review(self, engine: MemoryEngine) -> int:
        from memory_engine.implicit_preferences import (
            build_observation_candidate,
            detect_implicit_preference_signals,
        )

        positive_signal = detect_implicit_preference_signals("please use markdown bullet list")[0]
        negative_signal = detect_implicit_preference_signals("don't use markdown tables")[0]
        for i in range(3):
            event = self._event(content=f"please use markdown bullet list {i}", source_ref=f"msg://positive-{i}")
            candidate = build_observation_candidate(
                signal=positive_signal,
                source_text=event.content,
                content_meta={"scope": "project"},
                evidence=[{"source_ref": event.source_ref}],
                observed_at=event.timestamp,
            )
            event_id = engine._insert_event(event, "p1", None, "u1")
            engine._insert_memory(candidate, event_id, "p1", None, "u1")
        engine.conn.commit()
        candidate_id = engine.review(user_id="u1", project_id="p1")["preference_candidates"][0]
        stable_id = engine.confirm_preference_candidate(candidate_id, user_id="u1")["stable_preference_id"]

        event = self._event(content="don't use markdown tables", source_ref="msg://negative-after-confirm")
        candidate = build_observation_candidate(
            signal=negative_signal,
            source_text=event.content,
            content_meta={"scope": "project"},
            evidence=[{"source_ref": event.source_ref}],
            observed_at=event.timestamp,
        )
        event_id = engine._insert_event(event, "p1", None, "u1")
        engine._insert_memory(candidate, event_id, "p1", None, "u1")
        engine.conn.commit()
        engine.review(user_id="u1", project_id="p1")
        return stable_id

    # ---- #1 batch atomic commit ----

    def test_write_and_recall(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(),
                project_id="p1", task_id="t1", user_id="u1",
                memory_candidates=[self._candidate(title="Use option B", summary="Option B is the selected plan.")],
            )
            self.assertIn("skipped", result)
            self.assertEqual(len(result["skipped"]), 0)

            results = engine.recall(RecallRequest(query="selected plan option B", project_id="p1"))
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["memory_type"], "decision")

    def test_concurrent_write_and_recall_shared_engine(self) -> None:
        with self._engine() as engine:
            def write_one(i: int) -> tuple[str, int]:
                result = engine.write(
                    event=self._event(
                        content=f"Concurrent event {i}",
                        source_ref=f"msg://concurrent-{i}",
                    ),
                    project_id="p1",
                    task_id=f"t{i % 3}",
                    user_id="u1",
                    memory_candidates=[
                        self._candidate(
                            title=f"Concurrent project memory {i}",
                            summary=f"Concurrent project memory {i} is available for recall.",
                        )
                    ],
                )
                return ("write", result["memory_ids"][0])

            def recall_one(_: int) -> tuple[str, int]:
                count = len(engine.recall(RecallRequest(query="concurrent project memory", project_id="p1")))
                return ("recall", count)

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = []
                for i in range(12):
                    futures.append(pool.submit(write_one, i))
                    futures.append(pool.submit(recall_one, i))
                results = [future.result(timeout=10) for future in futures]

            written_ids = [value for kind, value in results if kind == "write" and value > 0]
            self.assertEqual(len(written_ids), 12)
            count = engine.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            self.assertEqual(count, 12)

    def test_write_creates_event_entries_for_core_memory_types(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(content="Decision, status, and preference update."),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(title="Use SQLite", summary="Use SQLite for local storage."),
                    self._candidate(
                        title="Task is blocked",
                        summary="Task is blocked by backend API.",
                        memory_type="task_status",
                    ),
                    self._candidate(
                        title="Prefer concise updates",
                        summary="User prefers concise updates.",
                        memory_type="preference",
                        content={"scope": "project", "preference_kind": "communication"},
                    ),
                ],
            )

            rows = engine.conn.execute(
                "SELECT relation, subject, object, qualifiers_json FROM event_entries WHERE source_event_id = ? ORDER BY id",
                (result["event_id"],),
            ).fetchall()

            self.assertEqual([row["relation"] for row in rows], [
                "recorded_decision",
                "changed_task_status",
                "showed_preference_for",
            ])
            self.assertEqual({row["subject"] for row in rows}, {"t1"})
            self.assertEqual(json.loads(rows[0]["qualifiers_json"])["memory_id"], result["memory_ids"][0])

    def test_get_event_bundle_reconstructs_event_memories_and_entries(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(content="Use SQLite for local MVP.", source_ref="msg://bundle"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(title="Use SQLite", summary="Use SQLite for local MVP."),
                ],
            )

            bundle = engine.get_event_bundle(result["event_id"])

            self.assertEqual(bundle["event"]["source_ref"], "msg://bundle")
            self.assertEqual(bundle["event"]["actors"], ["u1"])
            self.assertEqual(bundle["memories"][0]["id"], result["memory_ids"][0])
            self.assertEqual(bundle["event_entries"][0]["relation"], "recorded_decision")
            self.assertEqual(bundle["event_entries"][0]["qualifiers"]["source_ref"], "msg://bundle")

    def test_find_related_events_filters_by_relation_and_scope(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(content="Use SQLite.", source_ref="msg://rel-1"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[self._candidate(title="Use SQLite", summary="Use SQLite.")],
            )
            engine.write(
                event=self._event(content="Task blocked.", source_ref="msg://rel-2"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Task blocked",
                        summary="Task blocked.",
                        memory_type="task_status",
                    )
                ],
            )

            related = engine.find_related_events(relation="recorded_decision", project_id="p1")

            self.assertEqual(len(related), 1)
            self.assertEqual(related[0]["relation"], "recorded_decision")
            self.assertEqual(related[0]["object"], "Use SQLite")

    def test_synthesize_events_builds_decision_change_chain(self) -> None:
        with self._engine() as engine:
            first = engine.write(
                event=self._event(content="Decided to use SQLite", source_ref="msg://synth-1"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Use SQLite",
                        summary="Use SQLite for local storage.",
                        memory_type="decision",
                        evidence=[{"source_ref": "msg://synth-1"}],
                    )
                ],
            )
            second = engine.write(
                event=self._event(content="Changed decision to PostgreSQL", source_ref="msg://synth-2"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Use PostgreSQL",
                        summary="Use PostgreSQL for shared deployment.",
                        memory_type="decision",
                        evidence=[{"source_ref": "msg://synth-2"}],
                    )
                ],
            )

            result = engine.synthesize_events(
                [first["event_id"], second["event_id"]],
                "Did the storage decision change?",
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["conclusions"][0]["kind"], "decision_change_chain")
            self.assertEqual(result["conclusions"][0]["source_event_ids"], [first["event_id"], second["event_id"]])
            self.assertEqual(
                result["conclusions"][0]["candidate"]["content"]["kind"],
                "cross_event_synthesis_candidate",
            )

    def test_synthesize_events_requires_cross_event_evidence(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(content="Decided to use SQLite", source_ref="msg://synth-one"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Use SQLite",
                        summary="Use SQLite for local storage.",
                        memory_type="decision",
                        evidence=[{"source_ref": "msg://synth-one"}],
                    )
                ],
            )

            synthesis = engine.synthesize_events([result["event_id"]], "Did the storage decision change?")

            self.assertEqual(synthesis["status"], "insufficient_evidence")
            self.assertEqual(synthesis["conclusions"], [])

    def test_write_rolls_back_event_and_memory_on_mid_batch_failure(self) -> None:
        with self._engine() as engine:
            original_insert = engine._insert_memory
            calls = 0

            def fail_second_insert(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("forced insert failure")
                return original_insert(*args, **kwargs)

            with patch.object(engine, "_insert_memory", side_effect=fail_second_insert):
                with self.assertRaises(RuntimeError):
                    engine.write(
                        event=self._event(),
                        project_id="p1",
                        user_id="u1",
                        memory_candidates=[
                            self._candidate(title="First", summary="First summary is valid."),
                            self._candidate(title="Second", summary="Second summary is valid."),
                        ],
                    )

            event_count = engine.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            memory_count = engine.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            audit_count = engine.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            self.assertEqual(event_count, 0)
            self.assertEqual(memory_count, 0)
            self.assertEqual(audit_count, 0)

    # ---- #2 recall observation log ----

    def test_recall_log_records_hit(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate()],
            )
            engine.recall(RecallRequest(query="test", project_id="p1"))

            stats = engine._get_recall_stats(1)
            self.assertGreaterEqual(stats["recall_count"], 1)
            self.assertGreaterEqual(stats["returned_count"], 1)

    def test_recall_log_records_zero_result(self) -> None:
        with self._engine() as engine:
            engine.recall(RecallRequest(query="nonexistent", project_id="p1"))
            zeros = engine._get_zero_result_queries()
            self.assertEqual(len(zeros), 1)
            self.assertEqual(zeros[0]["query"], "nonexistent")

    def test_recall_log_marks_threshold_filtered_candidates(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Low value memory",
                    summary="Low value memory with weak quality.",
                    importance=0.0,
                    confidence=0.31,
                )],
            )
            results = engine.recall(RecallRequest(query="unrelated", project_id="p1"))
            self.assertEqual(results, [])
            row = engine.conn.execute(
                "SELECT was_returned FROM recall_log WHERE memory_id = 1",
            ).fetchone()
            self.assertEqual(row["was_returned"], 4)

    def test_recall_refuses_unrelated_active_memories(self) -> None:
        with self._engine() as engine:
            for idx in range(3):
                engine.write(
                    event=self._event(source_ref=f"msg://unrelated-{idx}"),
                    project_id="p1",
                    user_id="u1",
                    memory_candidates=[self._candidate(
                        title=f"Legitimate project note {idx}",
                        summary=f"Operational planning item {idx} for the project.",
                        importance=0.9,
                        confidence=0.95,
                    )],
                )

            results = engine.recall(RecallRequest(query="quantum fusion reactor budget", project_id="p1"))

            self.assertEqual(results, [])

    def test_recall_refuses_generic_type_token_only_match(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(source_ref="msg://workflow-trace"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[self._candidate(
                    memory_type="procedural",
                    title="Alpha legitimate workflow_trace",
                    summary="Unrelated legitimate workflow_trace for alpha.",
                    content={"scope": "project", "kind": "workflow_trace"},
                    tags=["workflow_trace"],
                    importance=0.8,
                    confidence=0.9,
                )],
            )

            results = engine.recall(RecallRequest(query="time travel approval workflow", project_id="p1"))

            self.assertEqual(results, [])

    def test_private_visibility_requires_matching_user(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(source_ref="msg://private-visibility"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Private rollout preference",
                        summary="Private rollout preference says use a quiet launch checklist.",
                        memory_type="preference",
                        content={"scope": "user", "visibility": "private"},
                        tags=["preference", "current"],
                    )
                ],
            )

            owner_results = engine.recall(
                RecallRequest(query="rollout preference quiet launch", project_id="p1", user_id="u1")
            )
            other_results = engine.recall(
                RecallRequest(query="rollout preference quiet launch", project_id="p1", user_id="u2")
            )
            anonymous_results = engine.recall(
                RecallRequest(query="rollout preference quiet launch", project_id="p1")
            )

            self.assertEqual(len(owner_results), 1)
            self.assertEqual(other_results, [])
            self.assertEqual(anonymous_results, [])

    def test_private_project_preference_requires_matching_project(self) -> None:
        with self._engine() as engine:
            event_id = engine._insert_event(
                self._event(source_ref="msg://project-private-pref"),
                "p1",
                None,
                "u1",
            )
            engine._insert_memory(
                self._candidate(
                    title="U1 project one table preference",
                    summary="U1 prefers table view in project one.",
                    memory_type="preference",
                    content={
                        "scope": "project",
                        "visibility": "private",
                        "project_scope": "p1",
                        "preference_category": "view_layout",
                    },
                    tags=["preference", "view_layout", "p1"],
                ),
                event_id,
                "p1",
                None,
                "u1",
            )
            engine._insert_memory(
                self._candidate(
                    title="U1 project two list preference",
                    summary="U1 prefers list view in project two.",
                    memory_type="preference",
                    content={
                        "scope": "project",
                        "visibility": "private",
                        "project_scope": "p2",
                        "preference_category": "view_layout",
                    },
                    tags=["preference", "view_layout", "p2"],
                ),
                event_id,
                "p2",
                None,
                "u1",
            )
            engine.conn.commit()

            results = engine.recall(
                RecallRequest(query="U1 view layout preference", project_id="p1", user_id="u1")
            )

            self.assertEqual([row["title"] for row in results], ["U1 project one table preference"])

    def test_project_visibility_allows_same_project_member(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(source_ref="msg://project-visibility"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Project deploy decision",
                        summary="Project deploy decision says use staged rollout.",
                        content={"scope": "project", "visibility": "project"},
                    )
                ],
            )

            same_project = engine.recall(
                RecallRequest(query="deploy decision staged rollout", project_id="p1", user_id="u2")
            )
            other_project = engine.recall(
                RecallRequest(query="deploy decision staged rollout", project_id="p2", user_id="u2")
            )

            self.assertEqual(len(same_project), 1)
            self.assertEqual(other_project, [])

    def test_valid_until_excludes_expired_memory(self) -> None:
        with self._engine() as engine:
            expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            engine.write(
                event=self._event(source_ref="msg://expired-status"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Expired task status",
                        summary="Expired task status says rollout is blocked.",
                        memory_type="task_status",
                        content={"scope": "project", "valid_until": expired},
                    )
                ],
            )

            results = engine.recall(RecallRequest(query="rollout blocked", project_id="p1", user_id="u1"))

            self.assertEqual(results, [])

    def test_valid_from_excludes_not_yet_effective_memory(self) -> None:
        with self._engine() as engine:
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            engine.write(
                event=self._event(source_ref="msg://future-decision"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Future deploy decision",
                        summary="Future deploy decision says use canary rollout.",
                        content={"scope": "project", "valid_from": future},
                    )
                ],
            )

            results = engine.recall(RecallRequest(query="deploy decision canary", project_id="p1", user_id="u1"))

            self.assertEqual(results, [])

    def test_effective_memory_with_valid_window_is_returned(self) -> None:
        with self._engine() as engine:
            valid_from = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            valid_until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            engine.write(
                event=self._event(source_ref="msg://current-decision"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Current deploy decision",
                        summary="Current deploy decision says use staged rollout.",
                        content={
                            "scope": "project",
                            "valid_from": valid_from,
                            "valid_until": valid_until,
                        },
                    )
                ],
            )
            row = engine.conn.execute(
                "SELECT valid_from, valid_until FROM memories WHERE title = ?",
                ("Current deploy decision",),
            ).fetchone()

            results = engine.recall(RecallRequest(query="deploy decision staged", project_id="p1", user_id="u1"))

            self.assertEqual(row["valid_from"], valid_from)
            self.assertEqual(row["valid_until"], valid_until)
            self.assertEqual(len(results), 1)

    def test_task_status_gets_default_valid_until(self) -> None:
        with self._engine() as engine:
            before = datetime.now(timezone.utc)
            engine.write(
                event=self._event(source_ref="msg://default-status-ttl"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Default TTL task status",
                        summary="Default TTL task status says rollout is progressing.",
                        memory_type="task_status",
                        content={"scope": "project"},
                    )
                ],
            )
            after = datetime.now(timezone.utc)
            row = engine.conn.execute(
                "SELECT valid_until FROM memories WHERE title = ?",
                ("Default TTL task status",),
            ).fetchone()

            valid_until = datetime.fromisoformat(row["valid_until"])
            results = engine.recall(RecallRequest(query="rollout progressing", project_id="p1", user_id="u1"))

            self.assertGreaterEqual(valid_until, before + timedelta(days=14))
            self.assertLessEqual(valid_until, after + timedelta(days=14, seconds=1))
            self.assertEqual(len(results), 1)

    def test_task_status_keeps_explicit_valid_until(self) -> None:
        with self._engine() as engine:
            explicit = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
            engine.write(
                event=self._event(source_ref="msg://explicit-status-ttl"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Explicit TTL task status",
                        summary="Explicit TTL task status says rollout is being monitored.",
                        memory_type="task_status",
                        content={"scope": "project", "valid_until": explicit},
                    )
                ],
            )
            row = engine.conn.execute(
                "SELECT valid_until FROM memories WHERE title = ?",
                ("Explicit TTL task status",),
            ).fetchone()

            self.assertEqual(row["valid_until"], explicit)

    def test_task_status_ttl_hours_controls_valid_until(self) -> None:
        with self._engine() as engine:
            before = datetime.now(timezone.utc)
            engine.write(
                event=self._event(source_ref="msg://custom-status-ttl"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Custom TTL task status",
                        summary="Custom TTL task status says release monitoring is active.",
                        memory_type="task_status",
                        content={"scope": "project", "ttl_hours": 2},
                    )
                ],
            )
            after = datetime.now(timezone.utc)
            row = engine.conn.execute(
                "SELECT valid_until FROM memories WHERE title = ?",
                ("Custom TTL task status",),
            ).fetchone()

            valid_until = datetime.fromisoformat(row["valid_until"])
            self.assertGreaterEqual(valid_until, before + timedelta(hours=2))
            self.assertLessEqual(valid_until, after + timedelta(hours=2, seconds=1))

    def test_recall_returns_current_preference_not_stale(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(source_ref="msg://old-pref"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[self._candidate(
                    memory_type="preference",
                    title="U1 prefers communication_style: stale_verbose_old",
                    summary="U1 previously preferred stale_verbose_old for communication_style.",
                    content={"scope": "project", "communication_style": "stale_verbose_old", "status": "stale"},
                    tags=["u1", "preference", "communication_style", "stale"],
                    importance=0.2,
                    confidence=0.3,
                )],
            )
            engine.write(
                event=self._event(source_ref="msg://current-pref"),
                project_id="p1",
                user_id="u1",
                memory_candidates=[self._candidate(
                    memory_type="preference",
                    title="U1 prefers communication_style: current_concise_best",
                    summary="U1 updated preference for communication_style to current_concise_best.",
                    content={"scope": "project", "communication_style": "current_concise_best", "status": "current"},
                    tags=["u1", "preference", "communication_style", "current"],
                    importance=0.9,
                    confidence=0.95,
                )],
            )

            results = engine.recall(RecallRequest(query="U1 communication_style preference", project_id="p1", user_id="u1"), limit=3)

            self.assertEqual([r["title"] for r in results], ["U1 prefers communication_style: current_concise_best"])

    def test_recall_returns_current_decision_not_superseded(self) -> None:
        with self._engine() as engine:
            for version, current in [("architecture_v1", False), ("architecture_v2", False), ("architecture_v3", True)]:
                engine.write(
                    event=self._event(source_ref=f"msg://{version}"),
                    project_id="p1",
                    user_id="u1",
                    memory_candidates=[self._candidate(
                        title=f"ALPHA architecture {version}",
                        summary=f"ALPHA architecture decision: {version}. {'Current.' if current else 'Superseded.'}",
                        content={"scope": "project", "decision": "architecture", "version": version, "current": current},
                        tags=["project_alpha", "architecture", "current" if current else "superseded"],
                        importance=0.95 if current else 0.5,
                        confidence=0.98 if current else 0.6,
                    )],
                )

            results = engine.recall(RecallRequest(query="ALPHA current architecture", project_id="p1"), limit=3)

            self.assertEqual([r["title"] for r in results], ["ALPHA architecture architecture_v3"])

    def test_recall_log_marks_mmr_excluded_candidates(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(source_ref="msg://1"), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Alpha memory", summary="Alpha durable memory.")],
            )
            engine.write(
                event=self._event(source_ref="msg://2"), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Beta memory", summary="Beta durable memory.")],
            )
            results = engine.recall(RecallRequest(query="", project_id="p1"), limit=1)
            self.assertEqual(len(results), 1)
            values = {
                row["was_returned"]
                for row in engine.conn.execute("SELECT was_returned FROM recall_log WHERE memory_id IS NOT NULL")
            }
            self.assertIn(1, values)
            self.assertIn(3, values)

    # ---- #3 exponential freshness decay ----

    def test_freshness_uses_configurable_half_life(self) -> None:
        from memory_engine.engine import _freshness_score

        now = datetime.now(timezone.utc)
        fresh = now.isoformat()
        self.assertAlmostEqual(_freshness_score(fresh, "decision"), 1.0, places=2)

        # 14 days old task_status should be ~0.5
        old_ts = (now - timedelta(days=14)).isoformat()
        self.assertAlmostEqual(_freshness_score(old_ts, "task_status"), 0.5, places=1)

        # 60 days old decision should be ~0.5
        old_ts = (now - timedelta(days=60)).isoformat()
        self.assertAlmostEqual(_freshness_score(old_ts, "decision"), 0.5, places=1)

        # preference decays slower than task_status at same age
        mid_ts = (now - timedelta(days=30)).isoformat()
        self.assertGreater(_freshness_score(mid_ts, "preference"), _freshness_score(mid_ts, "task_status"))

    def test_freshness_treats_naive_timestamps_as_beijing_time(self) -> None:
        from memory_engine.engine import _freshness_score

        beijing = datetime.now(timezone(timedelta(hours=8))) - timedelta(hours=3)
        aware = beijing.isoformat()
        naive = beijing.replace(tzinfo=None).isoformat()

        self.assertAlmostEqual(
            _freshness_score(naive, "decision"),
            _freshness_score(aware, "decision"),
            places=4,
        )

    # ---- #4 write gate ----

    def test_write_gate_rejects_short_summary(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(), project_id="p1",
                memory_candidates=[self._candidate(summary="Hi")],
            )
            self.assertEqual(len(result["skipped"]), 1)
            self.assertEqual(result["skipped"][0]["action"], "reject")
            self.assertEqual(len(result["memory_ids"]), 0)

    def test_write_gate_skips_near_duplicate(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1",
                memory_candidates=[self._candidate(title="Launch plan", summary="We decided to use option B for launch.")],
            )
            result = engine.write(
                event=self._event(source_ref="msg://2"), project_id="p1",
                memory_candidates=[self._candidate(title="Launch plan", summary="We decided to use option B for launch.")],
            )
            self.assertEqual(len(result["skipped"]), 1)
            self.assertEqual(result["skipped"][0]["action"], "skip")

    def test_write_gate_penalizes_low_confidence(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(), project_id="p1",
                memory_candidates=[self._candidate(confidence=0.2)],
            )
            self.assertEqual(len(result["memory_ids"]), 1)
            # recall and check importance was halved
            recalled = engine.recall(RecallRequest(query="test", project_id="p1"))
            self.assertAlmostEqual(recalled[0]["importance"], 0.35, places=2)

    # ---- #5 BM25 lexical scoring ----

    def test_bm25_rare_term_ranks_higher(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1",
                memory_candidates=[
                    self._candidate(title="Common topic", summary="This is about the common project update."),
                    self._candidate(title="Rare xyzzy topic", summary="This is about the unique xyzzy architecture."),
                ],
            )
            results = engine.recall(RecallRequest(query="xyzzy", project_id="p1"))
            self.assertGreaterEqual(len(results), 1)
            self.assertIn("xyzzy", results[0]["title"])

    def test_lexical_stats_cache_reused_and_invalidated_on_write(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(source_ref="msg://cache-1"), project_id="p1",
                memory_candidates=[self._candidate(title="Cache alpha", summary="Cache alpha memory.")],
            )
            import memory_engine.engine as engine_module

            with patch.object(
                engine_module,
                "_compute_lexical_stats",
                wraps=engine_module._compute_lexical_stats,
            ) as compute:
                engine.recall(RecallRequest(query="cache", project_id="p1"))
                engine.recall(RecallRequest(query="alpha", project_id="p1"))
                self.assertEqual(compute.call_count, 1)

                engine.write(
                    event=self._event(source_ref="msg://cache-2"), project_id="p1",
                    memory_candidates=[self._candidate(title="Cache beta", summary="Cache beta memory.")],
                )
                engine.recall(RecallRequest(query="beta", project_id="p1"))
                self.assertEqual(compute.call_count, 2)

    # ---- #6 near-duplicate merge ----

    def test_compact_merges_near_duplicates(self) -> None:
        with self._engine() as engine:
            # bypass write gate by writing directly
            event_id = engine._insert_event(self._event(), "p1", None, "u1")
            engine._insert_memory(
                self._candidate(title="Deadline is May 5", summary="The project deadline is May 5."),
                event_id=event_id, project_id="p1", task_id=None, user_id="u1",
            )
            event_id2 = engine._insert_event(self._event(source_ref="msg://2"), "p1", None, "u1")
            engine._insert_memory(
                self._candidate(title="Deadline is May 5", summary="The project deadline is May 5.", confidence=0.7),
                event_id=event_id2, project_id="p1", task_id=None, user_id="u1",
            )
            engine.conn.commit()

            report = engine.compact()
            self.assertEqual(report["merged"], 1)

            results = engine.recall(RecallRequest(query="deadline", project_id="p1"))
            self.assertEqual(len(results), 1)

    def test_compact_limits_near_duplicate_scan_batch(self) -> None:
        with self._engine() as engine:
            old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
            recent_ts = datetime.now(timezone.utc).isoformat()
            event_id = engine._insert_event(self._event(), "p1", None, "u1")

            for i in range(501):
                mid = engine._insert_memory(
                    self._candidate(
                        title=f"Unique recent memory {i}",
                        summary=f"Recent memory {i} has distinct content.",
                    ),
                    event_id=event_id, project_id="p1", task_id=None, user_id="u1",
                )
                engine.conn.execute(
                    "UPDATE memories SET updated_at = ? WHERE id = ?",
                    (recent_ts, mid),
                )

            for i in range(2):
                mid = engine._insert_memory(
                    self._candidate(
                        title="Old duplicate deadline",
                        summary="The old duplicate deadline is May 5.",
                        confidence=0.8 - i * 0.1,
                    ),
                    event_id=event_id, project_id="p1", task_id=None, user_id="u1",
                )
                engine.conn.execute(
                    "UPDATE memories SET updated_at = ? WHERE id = ?",
                    (old_ts, mid),
                )
            engine.conn.commit()

            report = engine.compact()
            self.assertEqual(report["merged"], 0)
            active_duplicates = engine.conn.execute(
                """
                SELECT COUNT(*) FROM memories
                WHERE status = 'active' AND title = 'Old duplicate deadline'
                """
            ).fetchone()[0]
            self.assertEqual(active_duplicates, 2)

    # ---- #8 stale low-value archive ----

    def test_compact_archives_stale_low_value(self) -> None:
        with self._engine() as engine:
            # insert a memory with old created_at and low importance/confidence
            old_ts = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
            event_id = engine._insert_event(
                self._event(content="old memory"), "p1", None, "u1",
            )
            mem_uuid = str(uuid.uuid4())
            engine.conn.execute(
                """INSERT INTO memories (uuid, memory_type, title, summary, content_json, scope, project_id,
                   user_id, importance, confidence, status, version, replaces_memory_id, superseded_by,
                   source_event_id, evidence_json, tags_json, created_at, updated_at)
                   VALUES (?, 'decision', 'Old', 'Old low value memory', '{}', 'project', 'p1',
                   'u1', 0.2, 0.5, 'active', 1, NULL, NULL, ?, '[]', '[]', ?, ?)""",
                (mem_uuid, event_id, old_ts, old_ts),
            )
            engine.conn.commit()

            report = engine.compact()
            self.assertGreaterEqual(report["archived"], 1)

    # ---- existing tests (scope, update, archive, context manager) ----

    def test_scope_filter(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Prefers table", summary="User prefers table format.",
                                                   memory_type="preference", content={"scope": "user"})],
            )
            self.assertEqual(len(engine.recall(RecallRequest(query="prefers table", project_id="p1", scope="user", user_id="u1"))), 1)
            self.assertEqual(len(engine.recall(RecallRequest(query="prefers table", project_id="p1", scope="user"))), 0)
            self.assertEqual(len(engine.recall(RecallRequest(query="prefers table", project_id="p1", scope="project"))), 0)

    def test_derive_implicit_preference_candidates(self) -> None:
        from memory_engine.implicit_preferences import (
            build_observation_candidate,
            derive_preference_candidates,
            detect_implicit_preference_signals,
        )

        signal = detect_implicit_preference_signals("please use markdown bullet list")[0]
        with self._engine() as engine:
            for i in range(3):
                event = self._event(content=f"please use markdown bullet list {i}", source_ref=f"msg://implicit-{i}")
                candidate = build_observation_candidate(
                    signal=signal,
                    source_text=event.content,
                    content_meta={"scope": "project"},
                    evidence=[{"source_ref": event.source_ref}],
                    observed_at=event.timestamp,
                )
                event_id = engine._insert_event(event, "p1", None, "u1")
                engine._insert_memory(candidate, event_id, "p1", None, "u1")
            engine.conn.commit()

            candidates = derive_preference_candidates(engine.conn, user_id="u1", project_id="p1")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["pattern_key"], "pref.output.structured_format")
        self.assertEqual(candidates[0]["positive_evidence_count"], 3)

    def test_negative_implicit_preference_signal_is_captured(self) -> None:
        from memory_engine.implicit_preferences import detect_implicit_preference_signals

        signals = detect_implicit_preference_signals("don't use markdown tables")

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].pattern_key, "pref.output.structured_format")
        self.assertEqual(signals[0].polarity, "negative")

    def test_negative_evidence_forces_preference_candidate_confirmation(self) -> None:
        from memory_engine.implicit_preferences import (
            build_observation_candidate,
            derive_preference_candidates,
            detect_implicit_preference_signals,
        )

        positive_signal = detect_implicit_preference_signals("please use markdown bullet list")[0]
        negative_signal = detect_implicit_preference_signals("don't use markdown tables")[0]
        with self._engine() as engine:
            for i in range(3):
                event = self._event(content=f"please use markdown bullet list {i}", source_ref=f"msg://positive-{i}")
                candidate = build_observation_candidate(
                    signal=positive_signal,
                    source_text=event.content,
                    content_meta={"scope": "project"},
                    evidence=[{"source_ref": event.source_ref}],
                    observed_at=event.timestamp,
                )
                event_id = engine._insert_event(event, "p1", None, "u1")
                engine._insert_memory(candidate, event_id, "p1", None, "u1")
            event = self._event(content="don't use markdown tables", source_ref="msg://negative-1")
            candidate = build_observation_candidate(
                signal=negative_signal,
                source_text=event.content,
                content_meta={"scope": "project"},
                evidence=[{"source_ref": event.source_ref}],
                observed_at=event.timestamp,
            )
            event_id = engine._insert_event(event, "p1", None, "u1")
            engine._insert_memory(candidate, event_id, "p1", None, "u1")
            engine.conn.commit()

            candidates = derive_preference_candidates(engine.conn, user_id="u1", project_id="p1")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["positive_evidence_count"], 3)
        self.assertEqual(candidates[0]["negative_evidence_count"], 1)
        self.assertTrue(candidates[0]["needs_confirmation"])
        self.assertEqual(len(candidates[0]["negative_observation_memory_ids"]), 1)

    def test_negative_evidence_blocks_balanced_preference_candidate(self) -> None:
        from memory_engine.implicit_preferences import (
            build_observation_candidate,
            derive_preference_candidates,
            detect_implicit_preference_signals,
        )

        positive_signal = detect_implicit_preference_signals("please use markdown bullet list")[0]
        negative_signal = detect_implicit_preference_signals("don't use markdown tables")[0]
        with self._engine() as engine:
            for i in range(3):
                for signal, prefix in ((positive_signal, "positive"), (negative_signal, "negative")):
                    event = self._event(content=f"{signal.signal} {i}", source_ref=f"msg://{prefix}-{i}")
                    candidate = build_observation_candidate(
                        signal=signal,
                        source_text=event.content,
                        content_meta={"scope": "project"},
                        evidence=[{"source_ref": event.source_ref}],
                        observed_at=event.timestamp,
                    )
                    event_id = engine._insert_event(event, "p1", None, "u1")
                    engine._insert_memory(candidate, event_id, "p1", None, "u1")
            engine.conn.commit()

            candidates = derive_preference_candidates(engine.conn, user_id="u1", project_id="p1")

        self.assertEqual(candidates, [])

    def test_review_materializes_implicit_preference_candidate(self) -> None:
        from memory_engine.implicit_preferences import (
            build_observation_candidate,
            detect_implicit_preference_signals,
        )

        signal = detect_implicit_preference_signals("please use markdown bullet list")[0]
        with self._engine() as engine:
            for i in range(3):
                event = self._event(content=f"please use markdown bullet list {i}", source_ref=f"msg://implicit-{i}")
                candidate = build_observation_candidate(
                    signal=signal,
                    source_text=event.content,
                    content_meta={"scope": "project"},
                    evidence=[{"source_ref": event.source_ref}],
                    observed_at=event.timestamp,
                )
                event_id = engine._insert_event(event, "p1", None, "u1")
                engine._insert_memory(candidate, event_id, "p1", None, "u1")
            engine.conn.commit()

            result = engine.review(user_id="u1", project_id="p1")

            self.assertEqual(len(result["preference_candidates"]), 1)
            row = engine.conn.execute(
                "SELECT content_json FROM memories WHERE id = ?",
                (result["preference_candidates"][0],),
            ).fetchone()
            content = json.loads(row["content_json"])
            entries = engine.find_related_events(relation="synthesized_preference_candidate", project_id="p1")
            self.assertEqual(content["kind"], "preference_candidate")
            self.assertEqual(content["pattern_key"], "pref.output.structured_format")
            self.assertEqual(content["positive_evidence_count"], "3")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["object"], "pref.output.structured_format")
            self.assertEqual(entries[0]["qualifiers"]["content_kind"], "preference_candidate")

    def test_review_materializes_negative_evidence_count(self) -> None:
        from memory_engine.implicit_preferences import (
            build_observation_candidate,
            detect_implicit_preference_signals,
        )

        positive_signal = detect_implicit_preference_signals("please use markdown bullet list")[0]
        negative_signal = detect_implicit_preference_signals("don't use markdown tables")[0]
        with self._engine() as engine:
            for i in range(3):
                event = self._event(content=f"please use markdown bullet list {i}", source_ref=f"msg://positive-{i}")
                candidate = build_observation_candidate(
                    signal=positive_signal,
                    source_text=event.content,
                    content_meta={"scope": "project"},
                    evidence=[{"source_ref": event.source_ref}],
                    observed_at=event.timestamp,
                )
                event_id = engine._insert_event(event, "p1", None, "u1")
                engine._insert_memory(candidate, event_id, "p1", None, "u1")
            event = self._event(content="don't use markdown tables", source_ref="msg://negative-1")
            candidate = build_observation_candidate(
                signal=negative_signal,
                source_text=event.content,
                content_meta={"scope": "project"},
                evidence=[{"source_ref": event.source_ref}],
                observed_at=event.timestamp,
            )
            event_id = engine._insert_event(event, "p1", None, "u1")
            engine._insert_memory(candidate, event_id, "p1", None, "u1")
            engine.conn.commit()

            result = engine.review(user_id="u1", project_id="p1")

            row = engine.conn.execute(
                "SELECT content_json, confidence FROM memories WHERE id = ?",
                (result["preference_candidates"][0],),
            ).fetchone()
            content = json.loads(row["content_json"])
            self.assertEqual(content["positive_evidence_count"], "3")
            self.assertEqual(content["negative_evidence_count"], "1")
            self.assertEqual(content["needs_confirmation"], "true")
            self.assertLess(float(row["confidence"]), 0.59)

    def test_recall_excludes_unconfirmed_implicit_preference_candidates_by_default(self) -> None:
        from memory_engine.implicit_preferences import (
            build_observation_candidate,
            detect_implicit_preference_signals,
        )

        signal = detect_implicit_preference_signals("please use markdown bullet list")[0]
        with self._engine() as engine:
            for i in range(3):
                event = self._event(content=f"please use markdown bullet list {i}", source_ref=f"msg://implicit-{i}")
                candidate = build_observation_candidate(
                    signal=signal,
                    source_text=event.content,
                    content_meta={"scope": "project"},
                    evidence=[{"source_ref": event.source_ref}],
                    observed_at=event.timestamp,
                )
                event_id = engine._insert_event(event, "p1", None, "u1")
                engine._insert_memory(candidate, event_id, "p1", None, "u1")
            engine.conn.commit()
            review = engine.review(user_id="u1", project_id="p1")

            default_results = engine.recall(RecallRequest(query="structured output markdown", project_id="p1", user_id="u1"))
            candidate_results = engine.recall(
                RecallRequest(
                    query="structured output markdown",
                    project_id="p1",
                    user_id="u1",
                    include_candidates=True,
                )
            )

            self.assertEqual(default_results, [])
            self.assertTrue(any(r["id"] == review["preference_candidates"][0] for r in candidate_results))

    def test_confirm_preference_candidate_creates_stable_preference_and_archives_candidate(self) -> None:
        from memory_engine.implicit_preferences import (
            build_observation_candidate,
            detect_implicit_preference_signals,
        )

        signal = detect_implicit_preference_signals("please use markdown bullet list")[0]
        with self._engine() as engine:
            for i in range(3):
                event = self._event(content=f"please use markdown bullet list {i}", source_ref=f"msg://implicit-{i}")
                candidate = build_observation_candidate(
                    signal=signal,
                    source_text=event.content,
                    content_meta={"scope": "project"},
                    evidence=[{"source_ref": event.source_ref}],
                    observed_at=event.timestamp,
                )
                event_id = engine._insert_event(event, "p1", None, "u1")
                engine._insert_memory(candidate, event_id, "p1", None, "u1")
            engine.conn.commit()
            candidate_id = engine.review(user_id="u1", project_id="p1")["preference_candidates"][0]

            result = engine.confirm_preference_candidate(candidate_id, user_id="u1")

            archived = engine.conn.execute("SELECT status FROM memories WHERE id = ?", (candidate_id,)).fetchone()
            stable = engine.conn.execute(
                "SELECT content_json, confidence, logical_layer, replaces_memory_id FROM memories WHERE id = ?",
                (result["stable_preference_id"],),
            ).fetchone()
            stable_content = json.loads(stable["content_json"])
            recall_results = engine.recall(RecallRequest(query="structured output markdown", project_id="p1", user_id="u1"))
            entries = engine.find_related_events(relation="confirmed_stable_preference", project_id="p1")
            vote_count = engine.conn.execute(
                "SELECT COUNT(*) FROM memory_votes WHERE candidate_memory_id = ?",
                (candidate_id,),
            ).fetchone()[0]
            assembly = engine.conn.execute(
                """
                SELECT COUNT(DISTINCT assembly_id) AS assembly_count,
                       MIN(ballot_kind) AS ballot_kind,
                       MIN(reviewer_role) AS reviewer_role
                FROM memory_votes
                WHERE candidate_memory_id = ?
                """,
                (candidate_id,),
            ).fetchone()

            self.assertEqual(archived["status"], "archived")
            self.assertEqual(stable_content["kind"], "stable_preference")
            self.assertEqual(stable_content["derived_from_candidate_id"], str(candidate_id))
            self.assertGreaterEqual(float(stable["confidence"]), 0.75)
            self.assertEqual(stable["logical_layer"], "L2")
            self.assertEqual(stable["replaces_memory_id"], candidate_id)
            self.assertEqual([r["id"] for r in recall_results], [result["stable_preference_id"]])
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["qualifiers"]["memory_id"], result["stable_preference_id"])
            self.assertEqual(entries[0]["qualifiers"]["content_kind"], "stable_preference")
            self.assertEqual(result["governance"]["decision"], "approve")
            self.assertEqual(vote_count, 5)
            self.assertEqual(assembly["assembly_count"], 1)
            self.assertEqual(assembly["ballot_kind"], "deterministic_citizen_assembly")
            self.assertIsNotNone(assembly["reviewer_role"])

    def test_preference_candidate_external_ballot_reject_blocks_confirmation(self) -> None:
        from memory_engine.governance import GovernanceRejected
        from memory_engine.implicit_preferences import (
            build_observation_candidate,
            detect_implicit_preference_signals,
        )

        contexts = []

        def provider(context):
            contexts.append(context)
            return [
                {
                    "reviewer_name": "HabitRiskAgent",
                    "reviewer_role": "external_habit_reviewer",
                    "vote": "reject",
                    "score": 0.2,
                    "reason": "external habit reviewer requires manual confirmation",
                    "evidence_refs": ["agent://habit-review"],
                }
            ]

        signal = detect_implicit_preference_signals("please use markdown bullet list")[0]
        with MemoryEngine(self.db_path, governance_ballot_provider=provider) as engine:
            for i in range(3):
                event = self._event(content=f"please use markdown bullet list {i}", source_ref=f"msg://implicit-vote-{i}")
                candidate = build_observation_candidate(
                    signal=signal,
                    source_text=event.content,
                    content_meta={"scope": "project"},
                    evidence=[{"source_ref": event.source_ref}],
                    observed_at=event.timestamp,
                )
                event_id = engine._insert_event(event, "p1", None, "u1")
                engine._insert_memory(candidate, event_id, "p1", None, "u1")
            engine.conn.commit()
            candidate_id = engine.review(user_id="u1", project_id="p1")["preference_candidates"][0]

            with self.assertRaises(GovernanceRejected) as raised:
                engine.confirm_preference_candidate(candidate_id, user_id="u1")

            candidate_row = engine.conn.execute("SELECT status FROM memories WHERE id = ?", (candidate_id,)).fetchone()
            stable_count = engine.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE content_json LIKE '%stable_preference%'"
            ).fetchone()[0]
            votes = engine.conn.execute(
                """
                SELECT reviewer_name, reviewer_role, vote, evidence_refs_json
                FROM memory_votes
                WHERE candidate_memory_id = ?
                """,
                (candidate_id,),
            ).fetchall()

            self.assertEqual(contexts[0]["topic"], "implicit_preference_confirmation")
            self.assertEqual(contexts[0]["candidate_memory_id"], candidate_id)
            self.assertEqual(raised.exception.decision["assembly"]["external_vote_count"], 1)
            self.assertEqual(candidate_row["status"], "active")
            self.assertEqual(stable_count, 0)
            self.assertEqual(len(votes), 6)
            self.assertTrue(any(row["reviewer_name"] == "HabitRiskAgent" and row["vote"] == "reject" for row in votes))
            self.assertTrue(any(row["reviewer_role"] == "external_habit_reviewer" for row in votes))
            self.assertTrue(any("agent://habit-review" in row["evidence_refs_json"] for row in votes))

    def test_negative_observation_marks_stable_preference_for_review(self) -> None:
        from memory_engine.implicit_preferences import (
            build_observation_candidate,
            detect_implicit_preference_signals,
        )

        positive_signal = detect_implicit_preference_signals("please use markdown bullet list")[0]
        negative_signal = detect_implicit_preference_signals("don't use markdown tables")[0]
        with self._engine() as engine:
            for i in range(3):
                event = self._event(content=f"please use markdown bullet list {i}", source_ref=f"msg://positive-{i}")
                candidate = build_observation_candidate(
                    signal=positive_signal,
                    source_text=event.content,
                    content_meta={"scope": "project"},
                    evidence=[{"source_ref": event.source_ref}],
                    observed_at=event.timestamp,
                )
                event_id = engine._insert_event(event, "p1", None, "u1")
                engine._insert_memory(candidate, event_id, "p1", None, "u1")
            engine.conn.commit()
            candidate_id = engine.review(user_id="u1", project_id="p1")["preference_candidates"][0]
            stable_id = engine.confirm_preference_candidate(candidate_id, user_id="u1")["stable_preference_id"]

            event = self._event(content="don't use markdown tables", source_ref="msg://negative-after-confirm")
            candidate = build_observation_candidate(
                signal=negative_signal,
                source_text=event.content,
                content_meta={"scope": "project"},
                evidence=[{"source_ref": event.source_ref}],
                observed_at=event.timestamp,
            )
            event_id = engine._insert_event(event, "p1", None, "u1")
            engine._insert_memory(candidate, event_id, "p1", None, "u1")
            engine.conn.commit()

            engine.review(user_id="u1", project_id="p1")

            stable = engine.conn.execute(
                "SELECT content_json, confidence, change_reason FROM memories WHERE id = ?",
                (stable_id,),
            ).fetchone()
            content = json.loads(stable["content_json"])
            self.assertEqual(content["needs_review"], "true")
            self.assertEqual(content["review_reason"], "negative implicit preference evidence observed")
            self.assertLessEqual(float(stable["confidence"]), 0.6)
            self.assertEqual(stable["change_reason"], "negative implicit preference evidence observed")

    def test_reconfirm_stable_preference_clears_review_and_records_event_entry(self) -> None:
        with self._engine() as engine:
            stable_id = self._stable_preference_under_review(engine)

            result = engine.reconfirm_stable_preference(stable_id, user_id="u1")

            stable = engine.conn.execute(
                "SELECT content_json, confidence, status, change_reason FROM memories WHERE id = ?",
                (stable_id,),
            ).fetchone()
            content = json.loads(stable["content_json"])
            entries = engine.find_related_events(relation="reconfirmed_stable_preference", project_id="p1")

            self.assertTrue(result["reconfirmed"])
            self.assertEqual(result["stable_preference_id"], stable_id)
            self.assertEqual(stable["status"], "active")
            self.assertEqual(content["needs_review"], "false")
            self.assertEqual(content["confirmed"], "true")
            self.assertEqual(content["needs_confirmation"], "false")
            self.assertEqual(content["reconfirmed_by"], "u1")
            self.assertNotIn("review_reason", content)
            self.assertGreaterEqual(float(stable["confidence"]), 0.75)
            self.assertIn("reconfirmed", stable["change_reason"])
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["qualifiers"]["memory_id"], stable_id)
            self.assertEqual(entries[0]["qualifiers"]["content_kind"], "stable_preference")

    def test_reject_stable_preference_archives_and_records_event_entry(self) -> None:
        with self._engine() as engine:
            stable_id = self._stable_preference_under_review(engine)

            result = engine.reject_stable_preference(stable_id, user_id="u1")

            stable = engine.conn.execute(
                "SELECT content_json, confidence, status, change_reason FROM memories WHERE id = ?",
                (stable_id,),
            ).fetchone()
            content = json.loads(stable["content_json"])
            recalled = engine.recall(RecallRequest(query="structured output markdown", project_id="p1", user_id="u1"))
            entries = engine.find_related_events(relation="rejected_stable_preference", project_id="p1")

            self.assertTrue(result["rejected"])
            self.assertEqual(result["stable_preference_id"], stable_id)
            self.assertEqual(stable["status"], "archived")
            self.assertEqual(content["needs_review"], "false")
            self.assertEqual(content["confirmed"], "false")
            self.assertEqual(content["rejected_by"], "u1")
            self.assertEqual(content["rejection_reason"], "user rejected stable preference during review")
            self.assertNotIn("review_reason", content)
            self.assertLessEqual(float(stable["confidence"]), 0.4)
            self.assertIn("rejected", stable["change_reason"])
            self.assertFalse(any(item["id"] == stable_id for item in recalled))
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["qualifiers"]["memory_id"], stable_id)
            self.assertEqual(entries[0]["qualifiers"]["content_kind"], "stable_preference")

    def test_review_marks_stale_stable_preference_for_review_without_archiving(self) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        with self._engine() as engine:
            result = engine.write(
                event=self._event(content="Confirmed structured output preference.", source_ref="msg://stable-stale"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Confirmed preference: output_format stale",
                        summary="User prefers structured markdown output.",
                        memory_type="preference",
                        importance=0.65,
                        confidence=0.85,
                        content={
                            "scope": "project",
                            "kind": "stable_preference",
                            "preference_kind": "output_format",
                            "pattern_key": "pref.output.structured_format",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                            "confirmed_at": old_ts,
                        },
                        evidence=[{"source_ref": "msg://stable-stale"}],
                    )
                ],
            )
            stable_id = result["memory_ids"][0]
            engine.conn.execute(
                "UPDATE memories SET created_at = ?, logical_layer = 'L2' WHERE id = ?",
                (old_ts, stable_id),
            )
            engine.conn.commit()

            review = engine.review(user_id="u1", project_id="p1")

            row = engine.conn.execute(
                "SELECT content_json, confidence, status, change_reason FROM memories WHERE id = ?",
                (stable_id,),
            ).fetchone()
            content = json.loads(row["content_json"])
            entries = engine.find_related_events(
                relation="stable_preference_marked_stale_for_review",
                project_id="p1",
            )

            self.assertEqual(review["preference_reviews"], [stable_id])
            self.assertEqual(review["demotions"], [])
            self.assertEqual(row["status"], "active")
            self.assertEqual(content["needs_review"], "true")
            self.assertEqual(content["review_reason"], "stable preference stale or long unused")
            self.assertEqual(content["decay_stale_days"], "90")
            self.assertLessEqual(float(row["confidence"]), 0.6)
            self.assertEqual(row["change_reason"], "stable preference stale or long unused")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["qualifiers"]["memory_id"], stable_id)

    def test_review_does_not_mark_recently_reconfirmed_stale_preference(self) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        recent_ts = datetime.now(timezone.utc).isoformat()
        with self._engine() as engine:
            result = engine.write(
                event=self._event(content="Recently reconfirmed preference.", source_ref="msg://stable-recent"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Confirmed preference: output_format recent",
                        summary="User recently reconfirmed structured markdown output.",
                        memory_type="preference",
                        importance=0.65,
                        confidence=0.85,
                        content={
                            "scope": "project",
                            "kind": "stable_preference",
                            "preference_kind": "output_format",
                            "pattern_key": "pref.output.structured_format",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                            "confirmed_at": old_ts,
                            "reconfirmed_at": recent_ts,
                        },
                        evidence=[{"source_ref": "msg://stable-recent"}],
                    )
                ],
            )
            stable_id = result["memory_ids"][0]
            engine.conn.execute(
                "UPDATE memories SET created_at = ?, logical_layer = 'L2' WHERE id = ?",
                (old_ts, stable_id),
            )
            engine.conn.commit()

            review = engine.review(user_id="u1", project_id="p1")

            row = engine.conn.execute(
                "SELECT content_json, status FROM memories WHERE id = ?",
                (stable_id,),
            ).fetchone()
            content = json.loads(row["content_json"])

            self.assertEqual(review["preference_reviews"], [])
            self.assertEqual(row["status"], "active")
            self.assertNotEqual(content.get("needs_review"), "true")

    def test_reject_preference_candidate_archives_candidate(self) -> None:
        from memory_engine.implicit_preferences import (
            build_observation_candidate,
            detect_implicit_preference_signals,
        )

        signal = detect_implicit_preference_signals("please use markdown bullet list")[0]
        with self._engine() as engine:
            for i in range(3):
                event = self._event(content=f"please use markdown bullet list {i}", source_ref=f"msg://implicit-{i}")
                candidate = build_observation_candidate(
                    signal=signal,
                    source_text=event.content,
                    content_meta={"scope": "project"},
                    evidence=[{"source_ref": event.source_ref}],
                    observed_at=event.timestamp,
                )
                event_id = engine._insert_event(event, "p1", None, "u1")
                engine._insert_memory(candidate, event_id, "p1", None, "u1")
            engine.conn.commit()
            candidate_id = engine.review(user_id="u1", project_id="p1")["preference_candidates"][0]

            result = engine.reject_preference_candidate(candidate_id, user_id="u1")

            row = engine.conn.execute("SELECT status, change_reason FROM memories WHERE id = ?", (candidate_id,)).fetchone()
            entries = engine.find_related_events(relation="rejected_preference_candidate", project_id="p1")
            self.assertTrue(result["rejected"])
            self.assertEqual(row["status"], "archived")
            self.assertIn("user rejected", row["change_reason"])
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["qualifiers"]["action"], "reject")
            self.assertEqual(entries[0]["qualifiers"]["target_memory_id"], candidate_id)
            self.assertEqual(entries[0]["qualifiers"]["context_key"], "pref.output.structured_format")
            self.assertEqual(engine.recall(RecallRequest(query="structured output markdown", project_id="p1", user_id="u1")), [])

    def test_review_prunes_excess_preference_candidate_branches(self) -> None:
        with self._engine() as engine:
            created_ids = []
            for i, confidence in enumerate([0.2, 0.5, 0.8, 0.7]):
                event_id = engine._insert_event(
                    self._event(content=f"preference branch {i}", source_ref=f"msg://pref-branch-{i}"),
                    "p1",
                    None,
                    "u1",
                )
                mid = engine._insert_memory(
                    self._candidate(
                        title=f"Possible preference branch {i}",
                        summary=f"Candidate branch {i} for structured output.",
                        memory_type="preference",
                        confidence=confidence,
                        content={
                            "scope": "project",
                            "kind": "preference_candidate",
                            "preference_kind": "output_format",
                            "pattern_key": "pref.output.structured_format",
                            "needs_confirmation": "true",
                            "confirmed": "false",
                        },
                    ),
                    event_id,
                    "p1",
                    None,
                    "u1",
                )
                created_ids.append(mid)
            engine.conn.commit()

            review = engine.review(user_id="u1", project_id="p1")

            rows = engine.conn.execute(
                "SELECT id, status, change_reason FROM memories WHERE id IN (?, ?, ?, ?) ORDER BY id",
                tuple(created_ids),
            ).fetchall()
            active_ids = [int(row["id"]) for row in rows if row["status"] == "active"]
            archived = [row for row in rows if row["status"] == "archived"]
            self.assertEqual(review["preference_candidate_archives"], [created_ids[0]])
            self.assertEqual(set(active_ids), set(created_ids[1:]))
            self.assertEqual(len(archived), 1)
            self.assertIn("branch limit", archived[0]["change_reason"])

    def test_review_dedupes_implicit_preference_candidate(self) -> None:
        from memory_engine.implicit_preferences import (
            build_observation_candidate,
            detect_implicit_preference_signals,
        )

        signal = detect_implicit_preference_signals("please use markdown bullet list")[0]
        with self._engine() as engine:
            for i in range(3):
                event = self._event(content=f"please use markdown bullet list {i}", source_ref=f"msg://implicit-{i}")
                candidate = build_observation_candidate(
                    signal=signal,
                    source_text=event.content,
                    content_meta={"scope": "project"},
                    evidence=[{"source_ref": event.source_ref}],
                    observed_at=event.timestamp,
                )
                event_id = engine._insert_event(event, "p1", None, "u1")
                engine._insert_memory(candidate, event_id, "p1", None, "u1")
            engine.conn.commit()

            first = engine.review(user_id="u1", project_id="p1")
            second = engine.review(user_id="u1", project_id="p1")

            self.assertEqual(len(first["preference_candidates"]), 1)
            self.assertEqual(second["preference_candidates"], [])
            candidate_count = engine.conn.execute(
                """
                SELECT COUNT(*)
                FROM memories
                WHERE content_json LIKE '%preference_candidate%'
                """
            ).fetchone()[0]
            self.assertEqual(candidate_count, 1)

    def test_review_materializes_workflow_strategy_candidate(self) -> None:
        from memory_engine.workflows import build_workflow_case_candidate

        with self._engine() as engine:
            for i in range(2):
                event = self._event(
                    content=f"pytest workflow passed {i}",
                    source_ref=f"msg://workflow-success-{i}",
                )
                case = build_workflow_case_candidate(
                    kind="workflow_success_case",
                    task_type="test_verification_workflow",
                    trigger="code change requires tests",
                    steps=["run focused pytest", "inspect failures before claiming done"],
                    outcome="all focused tests passed",
                    evidence=[{"source_ref": event.source_ref}],
                    scope="project",
                    source_text=event.content,
                )
                engine.write(
                    event=event,
                    project_id="p1",
                    task_id="t1",
                    user_id="u1",
                    memory_candidates=[case],
                )

            result = engine.review(user_id="u1", project_id="p1")

            self.assertEqual(len(result["workflow_strategy_candidates"]), 1)
            row = engine.conn.execute(
                "SELECT content_json FROM memories WHERE id = ?",
                (result["workflow_strategy_candidates"][0],),
            ).fetchone()
            content = json.loads(row["content_json"])
            self.assertEqual(content["kind"], "workflow_strategy_candidate")
            self.assertEqual(content["task_type"], "test_verification_workflow")
            self.assertEqual(content["success_evidence_count"], "2")
            self.assertIn("run focused pytest", content["recommended_steps"])

    def test_workflow_strategy_uses_trace_diagnostics(self) -> None:
        from memory_engine.workflows import (
            build_workflow_case_candidate,
            build_workflow_trace_candidate,
            derive_workflow_trace_steps,
        )

        with self._engine() as engine:
            for i in range(2):
                event = self._event(
                    content=f"pytest workflow passed with diagnostics {i}",
                    source_ref=f"msg://workflow-diagnostics-success-{i}",
                )
                engine.write(
                    event=event,
                    project_id="p1",
                    task_id="t1",
                    user_id="u1",
                    memory_candidates=[
                        build_workflow_case_candidate(
                            kind="workflow_success_case",
                            task_type="diagnostic_test_workflow",
                            trigger="code change requires tests",
                            outcome="focused pytest passed",
                            evidence=[{"source_ref": event.source_ref}],
                            scope="project",
                            source_text=event.content,
                        )
                    ],
                )

            trace_event = self._event(
                content="verify diagnostic workflow",
                source_ref="msg://workflow-diagnostics-trace",
            )
            trace_steps = derive_workflow_trace_steps(
                user_message="verify diagnostic workflow",
                tool_name="pytest",
                tool_output="Traceback: fixture path missing\n1 failed, 2 passed",
                assistant_summary="pytest failed on missing fixture path",
            )
            engine.write(
                event=trace_event,
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    build_workflow_trace_candidate(
                        result={
                            "outcome": "failure",
                            "task_type": "diagnostic_test_workflow",
                            "trigger": "verify diagnostic workflow",
                            "summary": "pytest failed on missing fixture path",
                        },
                        steps=trace_steps,
                        evidence=[{"source_ref": trace_event.source_ref}],
                        scope="project",
                        source_text="Traceback: fixture path missing\n1 failed, 2 passed",
                    )
                ],
            )

            result = engine.review(user_id="u1", project_id="p1")

            row = engine.conn.execute(
                "SELECT content_json, evidence_json FROM memories WHERE id = ?",
                (result["workflow_strategy_candidates"][0],),
            ).fetchone()
            content = json.loads(row["content_json"])
            evidence = json.loads(row["evidence_json"])
            self.assertEqual(content["trace_memory_ids"].count(","), 0)
            self.assertIn("run the relevant tests before claiming completion", content["recommended_steps"])
            self.assertIn("Traceback: fixture path missing", content["known_limits"])
            self.assertEqual(content["verification_signals"], ["tests"])
            self.assertEqual(content["failure_signals"], ["Traceback: fixture path missing"])
            self.assertEqual(content["tool_families"], ["test"])
            self.assertTrue(any(item["source_ref"] == "msg://workflow-diagnostics-trace" for item in evidence))

    def test_workflow_strategy_candidate_is_deduped_and_hidden_by_default(self) -> None:
        from memory_engine.workflows import build_workflow_case_candidate

        with self._engine() as engine:
            for i in range(2):
                event = self._event(
                    content=f"benchmark workflow passed {i}",
                    source_ref=f"msg://workflow-dedupe-{i}",
                )
                engine.write(
                    event=event,
                    project_id="p1",
                    task_id="t1",
                    user_id="u1",
                    memory_candidates=[
                        build_workflow_case_candidate(
                            kind="workflow_success_case",
                            task_type="test_verification_workflow",
                            trigger="benchmark change requires verification",
                            outcome="benchmark passed",
                            evidence=[{"source_ref": event.source_ref}],
                            scope="project",
                            source_text=event.content,
                        )
                    ],
                )

            first = engine.review(user_id="u1", project_id="p1")
            second = engine.review(user_id="u1", project_id="p1")
            default_results = engine.recall(
                RecallRequest(query="workflow strategy test verification", project_id="p1", user_id="u1")
            )
            workflow_results = engine.recall(
                RecallRequest(
                    query="workflow strategy test verification",
                    project_id="p1",
                    user_id="u1",
                    intent="workflow",
                )
            )

            self.assertEqual(len(first["workflow_strategy_candidates"]), 1)
            self.assertEqual(second["workflow_strategy_candidates"], [])
            self.assertFalse(any(r["content"].get("kind") == "workflow_strategy_candidate" for r in default_results))
            self.assertTrue(any(r["id"] == first["workflow_strategy_candidates"][0] for r in workflow_results))

    def test_workflow_failure_case_recall_and_event_entry(self) -> None:
        from memory_engine.workflows import build_workflow_case_candidate

        with self._engine() as engine:
            event = self._event(
                content="pytest failed because fixture path was missing",
                source_ref="msg://workflow-failure",
            )
            result = engine.write(
                event=event,
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    build_workflow_case_candidate(
                        kind="workflow_failure_case",
                        task_type="test_verification_workflow",
                        trigger="run pytest after code change",
                        outcome="pytest failed before fixture path was fixed",
                        root_cause="fixture path was missing",
                        evidence=[{"source_ref": event.source_ref}],
                        scope="project",
                        source_text=event.content,
                    )
                ],
            )

            recalls = engine.recall(
                RecallRequest(query="fixture path missing pytest failure", project_id="p1", user_id="u1")
            )
            entries = engine.find_related_events(relation="recorded_workflow_failure", project_id="p1")

            self.assertEqual([r["id"] for r in recalls], result["memory_ids"])
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["object"], "test_verification_workflow")

    def test_confirm_workflow_strategy_candidate_creates_workflow_skill(self) -> None:
        from memory_engine.workflows import build_workflow_case_candidate

        with self._engine() as engine:
            for i in range(2):
                event = self._event(
                    content=f"pytest strategy passed {i}",
                    source_ref=f"msg://workflow-confirm-{i}",
                )
                engine.write(
                    event=event,
                    project_id="p1",
                    task_id="t1",
                    user_id="u1",
                    memory_candidates=[
                        build_workflow_case_candidate(
                            kind="workflow_success_case",
                            task_type="test_verification_workflow",
                            trigger="code change requires tests",
                            steps=["run focused pytest"],
                            outcome="focused tests passed",
                            evidence=[{"source_ref": event.source_ref}],
                            scope="project",
                            source_text=event.content,
                        )
                    ],
                )
            candidate_id = engine.review(user_id="u1", project_id="p1")["workflow_strategy_candidates"][0]

            result = engine.confirm_workflow_strategy_candidate(candidate_id, user_id="u1")

            archived = engine.conn.execute("SELECT status FROM memories WHERE id = ?", (candidate_id,)).fetchone()
            skill = engine.conn.execute(
                "SELECT content_json, confidence, logical_layer, replaces_memory_id FROM memories WHERE id = ?",
                (result["workflow_skill_id"],),
            ).fetchone()
            skill_content = json.loads(skill["content_json"])
            recalls = engine.recall(
                RecallRequest(query="workflow skill test verification focused pytest", project_id="p1", user_id="u1")
            )
            entries = engine.find_related_events(relation="confirmed_workflow_skill", project_id="p1")
            vote_count = engine.conn.execute(
                "SELECT COUNT(*) FROM memory_votes WHERE candidate_memory_id = ?",
                (candidate_id,),
            ).fetchone()[0]
            assembly = engine.conn.execute(
                """
                SELECT COUNT(DISTINCT assembly_id) AS assembly_count,
                       MIN(ballot_kind) AS ballot_kind,
                       MIN(reviewer_role) AS reviewer_role
                FROM memory_votes
                WHERE candidate_memory_id = ?
                """,
                (candidate_id,),
            ).fetchone()

            self.assertEqual(archived["status"], "archived")
            self.assertEqual(skill_content["kind"], "workflow_skill")
            self.assertEqual(skill_content["derived_from_candidate_id"], str(candidate_id))
            self.assertEqual(skill["logical_layer"], "L2")
            self.assertGreaterEqual(float(skill["confidence"]), 0.8)
            self.assertEqual(skill["replaces_memory_id"], candidate_id)
            self.assertEqual(recalls[0]["id"], result["workflow_skill_id"])
            self.assertTrue(any(r["id"] == result["workflow_skill_id"] for r in recalls))
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["qualifiers"]["action"], "confirm")
            self.assertEqual(entries[0]["qualifiers"]["target_memory_id"], result["workflow_skill_id"])
            self.assertEqual(entries[0]["qualifiers"]["context_key"], "test_verification_workflow")
            self.assertEqual(vote_count, 5)
            self.assertEqual(assembly["assembly_count"], 1)
            self.assertEqual(assembly["ballot_kind"], "deterministic_citizen_assembly")
            self.assertIsNotNone(assembly["reviewer_role"])

    def test_review_prunes_excess_workflow_strategy_candidate_branches(self) -> None:
        with self._engine() as engine:
            created_ids = []
            for i, confidence in enumerate([0.25, 0.55, 0.75, 0.65]):
                event_id = engine._insert_event(
                    self._event(content=f"workflow branch {i}", source_ref=f"msg://workflow-branch-{i}"),
                    "p1",
                    None,
                    "u1",
                )
                mid = engine._insert_memory(
                    self._candidate(
                        title=f"Workflow strategy candidate branch {i}",
                        summary=f"Candidate branch {i} for test verification workflow.",
                        memory_type="procedural",
                        confidence=confidence,
                        content={
                            "scope": "project",
                            "kind": "workflow_strategy_candidate",
                            "task_type": "test_verification_workflow",
                            "needs_confirmation": "true",
                            "confirmed": "false",
                        },
                    ),
                    event_id,
                    "p1",
                    None,
                    "u1",
                )
                created_ids.append(mid)
            engine.conn.commit()

            review = engine.review(user_id="u1", project_id="p1")

            rows = engine.conn.execute(
                "SELECT id, status, change_reason FROM memories WHERE id IN (?, ?, ?, ?) ORDER BY id",
                tuple(created_ids),
            ).fetchall()
            active_ids = [int(row["id"]) for row in rows if row["status"] == "active"]
            archived = [row for row in rows if row["status"] == "archived"]
            self.assertEqual(review["workflow_strategy_candidate_archives"], [created_ids[0]])
            self.assertEqual(set(active_ids), set(created_ids[1:]))
            self.assertEqual(len(archived), 1)
            self.assertIn("branch limit", archived[0]["change_reason"])

    def test_workflow_strategy_governance_rejects_privacy_risk(self) -> None:
        from memory_engine.governance import GovernanceRejected

        with self._engine() as engine:
            write = engine.write(
                event=self._event(content="Candidate contains a redacted secret marker.", source_ref="msg://governance-risk"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow strategy candidate: risky_workflow",
                        summary="Do not promote [api_key:REDACTED] workflow details.",
                        memory_type="procedural",
                        confidence=0.8,
                        importance=0.8,
                        content={
                            "scope": "project",
                            "kind": "workflow_strategy_candidate",
                            "task_type": "risky_workflow",
                            "success_evidence_count": "2",
                            "failure_evidence_count": "0",
                            "recommended_steps": ["run tool", "verify output"],
                            "needs_confirmation": "true",
                        },
                        evidence=[{"source_ref": "msg://governance-risk"}],
                    )
                ],
            )
            candidate_id = write["memory_ids"][0]

            with self.assertRaises(GovernanceRejected):
                engine.confirm_workflow_strategy_candidate(candidate_id, user_id="u1")

            candidate = engine.conn.execute("SELECT status FROM memories WHERE id = ?", (candidate_id,)).fetchone()
            skill_count = engine.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE content_json LIKE '%workflow_skill%'"
            ).fetchone()[0]
            votes = engine.conn.execute(
                "SELECT reviewer_name, vote, reason, assembly_id, ballot_kind FROM memory_votes WHERE candidate_memory_id = ?",
                (candidate_id,),
            ).fetchall()
            audit = engine.conn.execute(
                "SELECT detail FROM audit_log WHERE target_id = ? AND detail LIKE '%rejected by governance%'",
                (candidate_id,),
            ).fetchone()

            self.assertEqual(candidate["status"], "active")
            self.assertEqual(skill_count, 0)
            self.assertEqual(len(votes), 5)
            self.assertTrue(any(row["reviewer_name"] == "PrivacyReviewer" and row["vote"] == "reject" for row in votes))
            self.assertEqual(len({row["assembly_id"] for row in votes}), 1)
            self.assertTrue(all(row["ballot_kind"] == "deterministic_citizen_assembly" for row in votes))
            self.assertIsNotNone(audit)

    def test_workflow_strategy_external_ballot_reject_blocks_confirmation(self) -> None:
        from memory_engine.governance import GovernanceRejected

        contexts = []

        def provider(context):
            contexts.append(context)
            return [
                {
                    "reviewer_name": "AgentRiskReviewer",
                    "reviewer_role": "external_risk_review",
                    "vote": "reject",
                    "score": 0.2,
                    "reason": "external reviewer found unsafe operational shortcut",
                    "evidence_refs": ["agent://risk-review"],
                }
            ]

        with MemoryEngine(self.db_path, governance_ballot_provider=provider) as engine:
            write = engine.write(
                event=self._event(content="Candidate passes deterministic checks.", source_ref="msg://external-ballot"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow strategy candidate: externally_reviewed_workflow",
                        summary="Reusable workflow with sufficient evidence.",
                        memory_type="procedural",
                        confidence=0.8,
                        importance=0.8,
                        content={
                            "scope": "project",
                            "kind": "workflow_strategy_candidate",
                            "task_type": "externally_reviewed_workflow",
                            "success_evidence_count": "2",
                            "failure_evidence_count": "0",
                            "recommended_steps": ["run tool", "verify output"],
                            "needs_confirmation": "true",
                        },
                        evidence=[{"source_ref": "msg://external-ballot"}],
                    )
                ],
            )
            candidate_id = write["memory_ids"][0]

            with self.assertRaises(GovernanceRejected) as raised:
                engine.confirm_workflow_strategy_candidate(candidate_id, user_id="u1")

            votes = engine.conn.execute(
                """
                SELECT reviewer_name, reviewer_role, vote, evidence_refs_json
                FROM memory_votes
                WHERE candidate_memory_id = ?
                """,
                (candidate_id,),
            ).fetchall()
            skill_count = engine.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE content_json LIKE '%workflow_skill%'"
            ).fetchone()[0]

            self.assertEqual(contexts[0]["topic"], "workflow_strategy_promotion")
            self.assertEqual(contexts[0]["candidate_memory_id"], candidate_id)
            self.assertEqual(raised.exception.decision["assembly"]["external_vote_count"], 1)
            self.assertEqual(skill_count, 0)
            self.assertEqual(len(votes), 6)
            self.assertTrue(any(row["reviewer_name"] == "AgentRiskReviewer" and row["vote"] == "reject" for row in votes))
            self.assertTrue(any(row["reviewer_role"] == "external_risk_review" for row in votes))
            self.assertTrue(any("agent://risk-review" in row["evidence_refs_json"] for row in votes))

    def test_cli_governance_ballot_provider_returns_votes(self) -> None:
        from memory_engine.governance import build_cli_ballot_provider

        code = (
            "import json, sys; "
            "ctx=json.loads(sys.stdin.read()); "
            "print(json.dumps({'votes':[{'reviewer_name':'CliAgentReviewer','vote':'reject',"
            "'score':0.2,'reason':'cli rejected '+ctx['topic'],"
            "'evidence_refs':['cli://agent-review']}]}))"
        )
        provider = build_cli_ballot_provider([sys.executable, "-c", code], timeout_s=5)

        votes = provider({"topic": "promotion_to_L2", "candidate_memory_id": 7})

        self.assertEqual(len(votes), 1)
        self.assertEqual(votes[0]["reviewer_name"], "CliAgentReviewer")
        self.assertEqual(votes[0]["vote"], "reject")
        self.assertEqual(votes[0]["reason"], "cli rejected promotion_to_L2")

    def test_env_governance_ballot_provider_uses_json_command(self) -> None:
        from memory_engine.governance import governance_ballot_provider_from_env

        self.assertIsNone(governance_ballot_provider_from_env({}))
        code = (
            "import json, sys; "
            "json.loads(sys.stdin.read()); "
            "print(json.dumps([{'reviewer_name':'EnvAgentReviewer','vote':'approve','score':0.8,"
            "'reason':'env command approved'}]))"
        )
        provider = governance_ballot_provider_from_env(
            {
                "GOVERNANCE_BALLOT_COMMAND_JSON": json.dumps([sys.executable, "-c", code]),
                "GOVERNANCE_BALLOT_TIMEOUT_S": "5",
            }
        )

        self.assertIsNotNone(provider)
        votes = provider({"topic": "workflow_strategy_promotion"})
        self.assertEqual(votes[0]["reviewer_name"], "EnvAgentReviewer")
        self.assertEqual(votes[0]["vote"], "approve")

    def test_llm_governance_ballot_provider_returns_multi_agent_votes(self) -> None:
        from memory_engine.governance import build_llm_ballot_provider

        calls = []

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "vote": "approve",
                                        "score": 0.82,
                                        "reason": "fake llm reviewer approved",
                                        "evidence_refs": ["msg://llm"],
                                    }
                                )
                            }
                        }
                    ]
                }

        class FakeClient:
            def __init__(self, timeout):
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def post(self, url, headers, json):
                calls.append({"url": url, "headers": headers, "json": json})
                return FakeResponse()

        import memory_engine.governance as governance

        original_client = governance.httpx.Client
        governance.httpx.Client = FakeClient
        try:
            provider = build_llm_ballot_provider(
                api_base="https://example.test/v1",
                api_key="secret-key",
                model="test-model",
                agents=[
                    {"reviewer_name": "AgentA", "reviewer_role": "role_a", "focus": "focus a"},
                    {"reviewer_name": "AgentB", "reviewer_role": "role_b", "focus": "focus b"},
                ],
                timeout_s=5,
            )
            votes = provider(
                {
                    "topic": "implicit_preference_confirmation",
                    "candidate_memory_id": 7,
                    "memory_type": "preference",
                    "title": "Possible preference",
                    "summary": "Aggregated observations",
                    "content": {"kind": "preference_candidate"},
                    "evidence": [{"source_ref": "msg://llm"}],
                    "deterministic_votes": [],
                }
            )
        finally:
            governance.httpx.Client = original_client

        self.assertEqual(len(votes), 2)
        self.assertEqual(votes[0]["reviewer_name"], "AgentA")
        self.assertEqual(votes[1]["reviewer_name"], "AgentB")
        self.assertEqual(votes[0]["vote"], "approve")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["url"], "https://example.test/v1/chat/completions")

    def test_env_governance_ballot_provider_uses_llm_agents(self) -> None:
        from memory_engine.governance import governance_ballot_provider_from_env

        calls = []

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '{"vote":"approve","score":0.7,"reason":"env llm approved","evidence_refs":[]}'
                            }
                        }
                    ]
                }

        class FakeClient:
            def __init__(self, timeout):
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def post(self, url, headers, json):
                calls.append(json)
                return FakeResponse()

        import memory_engine.governance as governance

        original_client = governance.httpx.Client
        governance.httpx.Client = FakeClient
        try:
            provider = governance_ballot_provider_from_env(
                {
                    "GOVERNANCE_LLM_BALLOT_ENABLED": "true",
                    "OPENAI_API_BASE": "https://example.test/v1",
                    "OPENAI_API_KEY": "secret-key",
                    "OPENAI_MODEL": "test-model",
                    "GOVERNANCE_LLM_BALLOT_AGENTS_JSON": json.dumps(
                        [{"reviewer_name": "EnvLLMAgent", "reviewer_role": "env_llm", "focus": "review"}]
                    ),
                }
            )
            self.assertIsNotNone(provider)
            votes = provider({"topic": "promotion_to_L2", "candidate_memory_id": 3})
        finally:
            governance.httpx.Client = original_client

        self.assertEqual(votes[0]["reviewer_name"], "EnvLLMAgent")
        self.assertEqual(votes[0]["vote"], "approve")
        self.assertEqual(len(calls), 1)

    def test_sample_governance_ballot_script_rejects_sensitive_marker(self) -> None:
        from memory_engine.governance import build_cli_ballot_provider

        script = Path("scripts") / "sample_governance_ballot.py"
        provider = build_cli_ballot_provider([sys.executable, str(script)], timeout_s=5)

        votes = provider(
            {
                "topic": "promotion_to_L2",
                "candidate_memory_id": 11,
                "title": "Decision with api_key marker",
                "summary": "Do not promote secret-bearing memory.",
                "content": {"scope": "project"},
                "evidence": [{"source_ref": "msg://secret"}],
            }
        )

        self.assertEqual(votes[0]["reviewer_name"], "SamplePrivacyAgent")
        self.assertEqual(votes[0]["vote"], "reject")
        self.assertIn("sensitive marker", votes[0]["reason"])

    def test_workflow_trace_records_step_event_entry(self) -> None:
        from memory_engine.workflows import build_workflow_trace_candidate, derive_workflow_trace_steps

        with self._engine() as engine:
            steps = derive_workflow_trace_steps(
                user_message="run focused tests",
                tool_name="pytest",
                tool_output="3 passed, 0 failed",
                assistant_summary="focused tests passed",
            )
            result = engine.write(
                event=self._event(content="run focused tests", source_ref="msg://workflow-trace"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    build_workflow_trace_candidate(
                        result={
                            "outcome": "success",
                            "task_type": "test_verification_workflow",
                            "trigger": "run focused tests",
                            "summary": "focused tests passed",
                        },
                        steps=steps,
                        evidence=[{"source_ref": "msg://workflow-trace"}],
                        scope="project",
                        source_text="run focused tests\n3 passed, 0 failed",
                    )
                ],
            )

            row = engine.conn.execute(
                "SELECT content_json FROM memories WHERE id = ?",
                (result["memory_ids"][0],),
            ).fetchone()
            content = json.loads(row["content_json"])
            entries = engine.find_related_events(relation="recorded_workflow_trace", project_id="p1")

            self.assertEqual(content["kind"], "workflow_trace")
            self.assertEqual(content["step_count"], "4")
            self.assertEqual(content["steps"][2]["phase"], "tool_result")
            self.assertEqual(content["steps"][2]["status"], "succeeded")
            self.assertEqual(content["steps"][2]["exit_code"], "0")
            self.assertEqual(content["steps"][2]["verification_signal"], "tests")
            self.assertEqual(content["verification_signals"], ["tests"])
            self.assertEqual(content["tool_families"], ["test"])
            self.assertEqual(len(entries), 1)

    def test_workflow_trace_records_failure_diagnostics(self) -> None:
        from memory_engine.workflows import build_workflow_trace_candidate, derive_workflow_trace_steps

        with self._engine() as engine:
            steps = derive_workflow_trace_steps(
                user_message="verify the fixture change",
                tool_name="pytest",
                tool_output="Traceback: fixture path missing\n1 failed, 2 passed",
                assistant_summary="focused tests failed because the fixture path was missing",
            )
            result = engine.write(
                event=self._event(content="verify the fixture change", source_ref="msg://workflow-trace-failure"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    build_workflow_trace_candidate(
                        result={
                            "outcome": "failure",
                            "task_type": "test_verification_workflow",
                            "trigger": "verify the fixture change",
                            "summary": "focused tests failed because the fixture path was missing",
                        },
                        steps=steps,
                        evidence=[{"source_ref": "msg://workflow-trace-failure"}],
                        scope="project",
                        source_text="Traceback: fixture path missing\n1 failed, 2 passed",
                    )
                ],
            )

            row = engine.conn.execute(
                "SELECT content_json FROM memories WHERE id = ?",
                (result["memory_ids"][0],),
            ).fetchone()
            content = json.loads(row["content_json"])

            self.assertEqual(content["steps"][2]["status"], "failed")
            self.assertEqual(content["steps"][2]["exit_code"], "1")
            self.assertEqual(content["steps"][2]["failure_type"], "exception")
            self.assertIn("Traceback", content["steps"][2]["failure_signal"])
            self.assertEqual(content["failed_step_indexes"], ["3"])
            self.assertEqual(content["failure_signals"], ["Traceback: fixture path missing"])

    def test_reject_workflow_strategy_candidate_archives_candidate(self) -> None:
        from memory_engine.workflows import build_workflow_case_candidate

        with self._engine() as engine:
            for i in range(2):
                event = self._event(
                    content=f"pytest strategy rejected case {i}",
                    source_ref=f"msg://workflow-reject-{i}",
                )
                engine.write(
                    event=event,
                    project_id="p1",
                    task_id="t1",
                    user_id="u1",
                    memory_candidates=[
                        build_workflow_case_candidate(
                            kind="workflow_success_case",
                            task_type="test_verification_workflow",
                            trigger="code change requires tests",
                            outcome="focused tests passed",
                            evidence=[{"source_ref": event.source_ref}],
                            scope="project",
                            source_text=event.content,
                        )
                    ],
                )
            candidate_id = engine.review(user_id="u1", project_id="p1")["workflow_strategy_candidates"][0]

            result = engine.reject_workflow_strategy_candidate(candidate_id, user_id="u1")

            row = engine.conn.execute("SELECT status, change_reason FROM memories WHERE id = ?", (candidate_id,)).fetchone()
            skill_count = engine.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE content_json LIKE '%workflow_skill%'"
            ).fetchone()[0]
            recalls = engine.recall(
                RecallRequest(
                    query="workflow strategy test verification",
                    project_id="p1",
                    user_id="u1",
                    intent="workflow",
                )
            )

            self.assertTrue(result["rejected"])
            self.assertEqual(row["status"], "archived")
            self.assertIn("user rejected", row["change_reason"])
            self.assertEqual(skill_count, 0)
            self.assertFalse(any(r["id"] == candidate_id for r in recalls))

    def test_record_workflow_skill_success_updates_effectiveness(self) -> None:
        with self._engine() as engine:
            write = engine.write(
                event=self._event(content="Confirmed test verification workflow.", source_ref="msg://skill-success"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow skill: test_verification_workflow",
                        summary="Reusable workflow skill for test verification.",
                        memory_type="procedural",
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                        },
                        evidence=[{"source_ref": "msg://skill-success"}],
                    )
                ],
            )
            skill_id = write["memory_ids"][0]

            result = engine.record_workflow_skill_outcome(
                skill_id,
                outcome="success",
                summary="Workflow skill reused and focused pytest passed.",
                evidence=[{"source_ref": "msg://skill-success-outcome"}],
                user_id="u1",
                project_id="p1",
                task_id="t1",
            )

            skill = engine.conn.execute(
                "SELECT content_json FROM memories WHERE id = ?",
                (skill_id,),
            ).fetchone()
            content = json.loads(skill["content_json"])
            outcome_row = engine.conn.execute(
                "SELECT content_json FROM memories WHERE id = ?",
                (result["outcome_memory_id"],),
            ).fetchone()
            outcome_content = json.loads(outcome_row["content_json"])
            entries = engine.find_related_events(relation="workflow_skill_succeeded", project_id="p1")

            self.assertEqual(content["usage_count"], "1")
            self.assertEqual(content["adoption_success_count"], "1")
            self.assertEqual(content["effectiveness_score"], "1.0")
            self.assertEqual(outcome_content["kind"], "workflow_skill_outcome")
            self.assertEqual(outcome_content["workflow_skill_id"], str(skill_id))
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["qualifiers"]["action"], "record_outcome")
            self.assertEqual(entries[0]["qualifiers"]["target_memory_id"], skill_id)
            self.assertEqual(entries[0]["qualifiers"]["context_key"], "test_verification_workflow")
            self.assertEqual(entries[0]["qualifiers"]["outcome"], "success")
            self.assertEqual(entries[0]["qualifiers"]["polarity"], "positive")

    def test_record_workflow_skill_failures_mark_skill_for_review(self) -> None:
        with self._engine() as engine:
            write = engine.write(
                event=self._event(content="Confirmed test verification workflow.", source_ref="msg://skill-failure"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow skill: test_verification_workflow",
                        summary="Reusable workflow skill for test verification.",
                        memory_type="procedural",
                        confidence=0.9,
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                        },
                        evidence=[{"source_ref": "msg://skill-failure"}],
                    )
                ],
            )
            skill_id = write["memory_ids"][0]

            for i in range(2):
                engine.record_workflow_skill_outcome(
                    skill_id,
                    outcome="failure",
                    summary=f"Workflow skill reused but pytest failed {i}.",
                    evidence=[{"source_ref": f"msg://skill-failure-outcome-{i}"}],
                    user_id="u1",
                    project_id="p1",
                    task_id="t1",
                )

            skill = engine.conn.execute(
                "SELECT content_json, confidence, change_reason FROM memories WHERE id = ?",
                (skill_id,),
            ).fetchone()
            content = json.loads(skill["content_json"])
            entries = engine.find_related_events(relation="workflow_skill_failed", project_id="p1")

            self.assertEqual(content["usage_count"], "2")
            self.assertEqual(content["adoption_failure_count"], "2")
            self.assertEqual(content["effectiveness_score"], "0.0")
            self.assertEqual(content["needs_review"], "true")
            self.assertEqual(content["review_reason"], "workflow skill negative outcome evidence observed")
            self.assertLessEqual(float(skill["confidence"]), 0.6)
            self.assertEqual(skill["change_reason"], "workflow skill negative outcome evidence observed")
            self.assertEqual(len(entries), 2)

    def test_reconfirm_workflow_skill_clears_review_and_records_event_entry(self) -> None:
        with self._engine() as engine:
            write = engine.write(
                event=self._event(content="Confirmed test verification workflow.", source_ref="msg://skill-reconfirm"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow skill: test_verification_reconfirm",
                        summary="Reusable workflow skill for test verification.",
                        memory_type="procedural",
                        confidence=0.9,
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                        },
                        evidence=[{"source_ref": "msg://skill-reconfirm"}],
                    )
                ],
            )
            skill_id = write["memory_ids"][0]
            for i in range(2):
                engine.record_workflow_skill_outcome(
                    skill_id,
                    outcome="failure",
                    summary=f"Workflow skill failed and requires review {i}.",
                    evidence=[{"source_ref": f"msg://skill-reconfirm-failure-{i}"}],
                    user_id="u1",
                    project_id="p1",
                    task_id="t1",
                )

            result = engine.reconfirm_workflow_skill(skill_id, user_id="u1")

            skill = engine.conn.execute(
                "SELECT content_json, confidence, status, change_reason FROM memories WHERE id = ?",
                (skill_id,),
            ).fetchone()
            content = json.loads(skill["content_json"])
            entries = engine.find_related_events(relation="reconfirmed_workflow_skill", project_id="p1")

            self.assertTrue(result["reconfirmed"])
            self.assertEqual(result["workflow_skill_id"], skill_id)
            self.assertEqual(skill["status"], "active")
            self.assertEqual(content["needs_review"], "false")
            self.assertEqual(content["confirmed"], "true")
            self.assertEqual(content["needs_confirmation"], "false")
            self.assertEqual(content["reconfirmed_by"], "u1")
            self.assertNotIn("review_reason", content)
            self.assertGreaterEqual(float(skill["confidence"]), 0.75)
            self.assertIn("reconfirmed", skill["change_reason"])
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["qualifiers"]["memory_id"], skill_id)
            self.assertEqual(entries[0]["qualifiers"]["content_kind"], "workflow_skill")
            self.assertEqual(entries[0]["qualifiers"]["action"], "reconfirm")
            self.assertEqual(entries[0]["qualifiers"]["target_memory_id"], skill_id)
            self.assertEqual(entries[0]["qualifiers"]["context_key"], "test_verification_workflow")

    def test_reject_workflow_skill_archives_and_records_event_entry(self) -> None:
        with self._engine() as engine:
            write = engine.write(
                event=self._event(content="Confirmed test verification workflow.", source_ref="msg://skill-reject"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow skill: test_verification_reject",
                        summary="Reusable workflow skill for test verification.",
                        memory_type="procedural",
                        confidence=0.9,
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                        },
                        evidence=[{"source_ref": "msg://skill-reject"}],
                    )
                ],
            )
            skill_id = write["memory_ids"][0]
            for i in range(2):
                engine.record_workflow_skill_outcome(
                    skill_id,
                    outcome="failure",
                    summary=f"Workflow skill failed and should be rejected {i}.",
                    evidence=[{"source_ref": f"msg://skill-reject-failure-{i}"}],
                    user_id="u1",
                    project_id="p1",
                    task_id="t1",
                )

            result = engine.reject_workflow_skill(skill_id, user_id="u1")

            skill = engine.conn.execute(
                "SELECT content_json, confidence, status, change_reason FROM memories WHERE id = ?",
                (skill_id,),
            ).fetchone()
            content = json.loads(skill["content_json"])
            entries = engine.find_related_events(relation="rejected_workflow_skill", project_id="p1")
            recalled = engine.recall(
                RecallRequest(query="test verification workflow", project_id="p1", user_id="u1", intent="workflow")
            )

            self.assertTrue(result["rejected"])
            self.assertEqual(result["workflow_skill_id"], skill_id)
            self.assertEqual(skill["status"], "archived")
            self.assertEqual(content["needs_review"], "false")
            self.assertEqual(content["confirmed"], "false")
            self.assertEqual(content["rejected_by"], "u1")
            self.assertEqual(content["rejection_reason"], "user rejected workflow skill during review")
            self.assertNotIn("review_reason", content)
            self.assertLessEqual(float(skill["confidence"]), 0.4)
            self.assertIn("rejected", skill["change_reason"])
            self.assertFalse(any(item["id"] == skill_id for item in recalled))
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["qualifiers"]["memory_id"], skill_id)
            self.assertEqual(entries[0]["qualifiers"]["content_kind"], "workflow_skill")
            self.assertEqual(entries[0]["qualifiers"]["action"], "reject")
            self.assertEqual(entries[0]["qualifiers"]["target_memory_id"], skill_id)
            self.assertEqual(entries[0]["qualifiers"]["context_key"], "test_verification_workflow")
            self.assertEqual(entries[0]["qualifiers"]["outcome"], "archived")

    def test_record_workflow_skill_repeated_failures_archive_skill(self) -> None:
        with self._engine() as engine:
            write = engine.write(
                event=self._event(content="Confirmed fragile test workflow.", source_ref="msg://skill-archive"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow skill: fragile_test_verification_workflow",
                        summary="Reusable workflow skill for fragile test verification.",
                        memory_type="procedural",
                        confidence=0.9,
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "fragile_test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                        },
                        evidence=[{"source_ref": "msg://skill-archive"}],
                    )
                ],
            )
            skill_id = write["memory_ids"][0]

            for i in range(3):
                engine.record_workflow_skill_outcome(
                    skill_id,
                    outcome="failure",
                    summary=f"Workflow skill failed verification run {i}.",
                    evidence=[{"source_ref": f"msg://skill-archive-outcome-{i}"}],
                    user_id="u1",
                    project_id="p1",
                    task_id="t1",
                )

            skill = engine.conn.execute(
                "SELECT content_json, confidence, status, change_reason FROM memories WHERE id = ?",
                (skill_id,),
            ).fetchone()
            content = json.loads(skill["content_json"])
            recalled = engine.recall(RecallRequest(query="fragile test verification", project_id="p1"))

            self.assertEqual(skill["status"], "archived")
            self.assertEqual(content["usage_count"], "3")
            self.assertEqual(content["archived_by_policy"], "true")
            self.assertEqual(content["archive_reason"], "workflow skill repeated negative outcomes")
            self.assertLessEqual(float(skill["confidence"]), 0.4)
            self.assertEqual(skill["change_reason"], "workflow skill archived after repeated negative outcomes")
            self.assertFalse(any(item["id"] == skill_id for item in recalled))

    def test_review_marks_stale_workflow_skill_for_review_without_archiving(self) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        with self._engine() as engine:
            skill_id = engine.write(
                event=self._event(content="Confirmed old workflow skill.", source_ref="msg://old-workflow-skill"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow skill: old_test_workflow",
                        summary="Old workflow skill.",
                        memory_type="procedural",
                        confidence=0.9,
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                        },
                        evidence=[{"source_ref": "msg://old-workflow-skill"}],
                    )
                ],
            )["memory_ids"][0]
            engine.conn.execute(
                "UPDATE memories SET created_at = ?, updated_at = ? WHERE id = ?",
                (old_ts, old_ts, skill_id),
            )
            engine.conn.commit()

            review = engine.review(user_id="u1", project_id="p1")

            row = engine.conn.execute(
                "SELECT content_json, confidence, status, change_reason FROM memories WHERE id = ?",
                (skill_id,),
            ).fetchone()
            content = json.loads(row["content_json"])
            entries = engine.find_related_events(relation="workflow_skill_marked_stale_for_review", project_id="p1")

            self.assertEqual(review["workflow_skill_reviews"], [skill_id])
            self.assertEqual(row["status"], "active")
            self.assertEqual(content["needs_review"], "true")
            self.assertEqual(content["review_reason"], "workflow skill stale or long unused")
            self.assertEqual(content["decay_stale_days"], "90")
            self.assertLessEqual(float(row["confidence"]), 0.6)
            self.assertEqual(row["change_reason"], "workflow skill stale or long unused")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["qualifiers"]["action"], "mark_review")
            self.assertEqual(entries[0]["qualifiers"]["target_memory_id"], skill_id)

    def test_review_keeps_recently_reconfirmed_workflow_skill_active(self) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        recent_ts = datetime.now(timezone.utc).isoformat()
        with self._engine() as engine:
            skill_id = engine.write(
                event=self._event(content="Confirmed recently reconfirmed workflow skill.", source_ref="msg://recent-workflow-skill"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow skill: recent_test_workflow",
                        summary="Recently reconfirmed workflow skill.",
                        memory_type="procedural",
                        confidence=0.9,
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                            "reconfirmed_at": recent_ts,
                        },
                        evidence=[{"source_ref": "msg://recent-workflow-skill"}],
                    )
                ],
            )["memory_ids"][0]
            engine.conn.execute(
                "UPDATE memories SET created_at = ?, updated_at = ? WHERE id = ?",
                (old_ts, old_ts, skill_id),
            )
            engine.conn.commit()

            review = engine.review(user_id="u1", project_id="p1")

            row = engine.conn.execute(
                "SELECT content_json, status FROM memories WHERE id = ?",
                (skill_id,),
            ).fetchone()
            content = json.loads(row["content_json"])
            self.assertEqual(review["workflow_skill_reviews"], [])
            self.assertEqual(row["status"], "active")
            self.assertNotEqual(content.get("needs_review"), "true")

    def test_evaluate_workflow_self_improvement_detects_replacement_gain(self) -> None:
        with self._engine() as engine:
            brittle = engine.write(
                event=self._event(content="Confirmed brittle workflow.", source_ref="msg://brittle-skill"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow skill: brittle_test_workflow",
                        summary="Brittle workflow skill.",
                        memory_type="procedural",
                        confidence=0.9,
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                        },
                        evidence=[{"source_ref": "msg://brittle-skill"}],
                    )
                ],
            )["memory_ids"][0]
            improved = engine.write(
                event=self._event(content="Confirmed improved workflow.", source_ref="msg://improved-skill"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow skill: improved_test_workflow",
                        summary="Improved workflow skill.",
                        memory_type="procedural",
                        confidence=0.9,
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                        },
                        evidence=[{"source_ref": "msg://improved-skill"}],
                    )
                ],
            )["memory_ids"][0]

            for i in range(3):
                engine.record_workflow_skill_outcome(
                    brittle,
                    outcome="failure",
                    summary=f"Brittle workflow failed cycle {i}.",
                    evidence=[{"source_ref": f"msg://brittle-failure-{i}"}],
                    user_id="u1",
                    project_id="p1",
                    task_id="t1",
                )
            for i in range(2):
                engine.record_workflow_skill_outcome(
                    improved,
                    outcome="success",
                    summary=f"Improved workflow succeeded cycle {i}.",
                    evidence=[{"source_ref": f"msg://improved-success-{i}"}],
                    user_id="u1",
                    project_id="p1",
                    task_id="t1",
                )

            evaluation = engine.evaluate_workflow_self_improvement("test_verification_workflow")

            self.assertEqual(evaluation["status"], "improved")
            self.assertEqual(evaluation["active_skill_count"], 1)
            self.assertEqual(evaluation["retired_or_review_skill_count"], 1)
            self.assertEqual(evaluation["best_active_skill"]["memory_id"], improved)
            self.assertEqual(evaluation["weakest_retired_or_review_skill"]["memory_id"], brittle)
            self.assertGreater(evaluation["improvement_delta"], 0.0)

    def test_evaluate_workflow_self_improvement_reports_not_improved(self) -> None:
        with self._engine() as engine:
            retired = engine.write(
                event=self._event(content="Confirmed old weak workflow.", source_ref="msg://old-weak-skill"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow skill: old_weak_test_workflow",
                        summary="Old weak workflow skill.",
                        memory_type="procedural",
                        confidence=0.9,
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                        },
                        evidence=[{"source_ref": "msg://old-weak-skill"}],
                    )
                ],
            )["memory_ids"][0]
            replacement = engine.write(
                event=self._event(content="Confirmed weak replacement workflow.", source_ref="msg://weak-replacement-skill"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow skill: weak_replacement_test_workflow",
                        summary="Weak replacement workflow skill.",
                        memory_type="procedural",
                        confidence=0.9,
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                        },
                        evidence=[{"source_ref": "msg://weak-replacement-skill"}],
                    )
                ],
            )["memory_ids"][0]

            for i in range(3):
                engine.record_workflow_skill_outcome(
                    retired,
                    outcome="failure",
                    summary=f"Old weak workflow failed cycle {i}.",
                    evidence=[{"source_ref": f"msg://old-weak-failure-{i}"}],
                    user_id="u1",
                    project_id="p1",
                    task_id="t1",
                )
            engine.record_workflow_skill_outcome(
                replacement,
                outcome="failure",
                summary="Replacement workflow failed first cycle.",
                evidence=[{"source_ref": "msg://weak-replacement-failure"}],
                user_id="u1",
                project_id="p1",
                task_id="t1",
            )

            evaluation = engine.evaluate_workflow_self_improvement("test_verification_workflow")

            self.assertEqual(evaluation["status"], "not_improved")
            self.assertEqual(evaluation["best_active_skill"]["memory_id"], replacement)
            self.assertEqual(evaluation["weakest_retired_or_review_skill"]["memory_id"], retired)
            self.assertEqual(evaluation["improvement_delta"], 0.0)

    def test_evaluate_workflow_self_improvement_reports_insufficient_evidence(self) -> None:
        with self._engine() as engine:
            skill_id = engine.write(
                event=self._event(content="Confirmed solo workflow.", source_ref="msg://solo-skill"),
                project_id="p1",
                task_id="t1",
                user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Workflow skill: solo_test_workflow",
                        summary="Solo workflow skill.",
                        memory_type="procedural",
                        confidence=0.9,
                        content={
                            "scope": "project",
                            "kind": "workflow_skill",
                            "task_type": "test_verification_workflow",
                            "confirmed": "true",
                            "needs_confirmation": "false",
                        },
                        evidence=[{"source_ref": "msg://solo-skill"}],
                    )
                ],
            )["memory_ids"][0]
            engine.record_workflow_skill_outcome(
                skill_id,
                outcome="success",
                summary="Solo workflow succeeded once.",
                evidence=[{"source_ref": "msg://solo-success"}],
                user_id="u1",
                project_id="p1",
                task_id="t1",
            )

            evaluation = engine.evaluate_workflow_self_improvement("test_verification_workflow")

            self.assertEqual(evaluation["status"], "insufficient_evidence")
            self.assertEqual(evaluation["active_skill_count"], 1)
            self.assertEqual(evaluation["retired_or_review_skill_count"], 0)
            self.assertIsNone(evaluation["improvement_delta"])

    def test_update_supersedes_old_version(self) -> None:
        with self._engine() as engine:
            result = engine.write(
                event=self._event(), project_id="p1", task_id="t1", user_id="u1",
                memory_candidates=[self._candidate(title="Deadline May 5", summary="Current deadline is May 5.")],
            )
            old_id = result["memory_ids"][0]
            engine.update(
                memory_id=old_id,
                candidate=self._candidate(title="Deadline May 7", summary="Current deadline is May 7.",
                                          change_reason="延期"),
                event=self._event(content="Deadline is now May 7.", source_ref="msg://2"),
                project_id="p1", task_id="t1", user_id="u1",
            )
            results = engine.recall(RecallRequest(query="deadline", project_id="p1"))
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["version"], 2)

    def test_update_nonexistent_raises(self) -> None:
        with self._engine() as engine:
            with self.assertRaises(ValueError):
                engine.update(memory_id=9999, candidate=self._candidate(), event=self._event())

    def test_archive_removes_from_recall(self) -> None:
        with self._engine() as engine:
            result = engine.write(event=self._event(), project_id="p1", user_id="u1",
                                  memory_candidates=[self._candidate(title="Archive test", summary="To be archived.")])
            engine.archive(result["memory_ids"][0], reason="Out of scope")
            self.assertEqual(len(engine.recall(RecallRequest(query="archive", project_id="p1"))), 0)

    def test_archive_rolls_back_status_when_audit_fails(self) -> None:
        with self._engine() as engine:
            result = engine.write(event=self._event(), project_id="p1", user_id="u1",
                                  memory_candidates=[self._candidate()])
            memory_id = result["memory_ids"][0]
            with patch.object(engine, "_log_audit", side_effect=RuntimeError("audit failed")):
                with self.assertRaises(RuntimeError):
                    engine.archive(memory_id, reason="Out of scope")
            row = engine.conn.execute("SELECT status, change_reason FROM memories WHERE id = ?", (memory_id,)).fetchone()
            self.assertEqual(row["status"], "active")
            self.assertIsNone(row["change_reason"])

    def test_invalidate_rolls_back_status_when_audit_fails(self) -> None:
        with self._engine() as engine:
            result = engine.write(event=self._event(), project_id="p1", user_id="u1",
                                  memory_candidates=[self._candidate()])
            memory_id = result["memory_ids"][0]
            with patch.object(engine, "_log_audit", side_effect=RuntimeError("audit failed")):
                with self.assertRaises(RuntimeError):
                    engine.invalidate(memory_id, reason="Bad source")
            row = engine.conn.execute("SELECT status, change_reason FROM memories WHERE id = ?", (memory_id,)).fetchone()
            self.assertEqual(row["status"], "active")
            self.assertIsNone(row["change_reason"])

    def test_context_manager(self) -> None:
        with self._engine() as engine:
            engine.write(event=self._event(), project_id="p1", user_id="u1",
                         memory_candidates=[self._candidate()])

    # ---- #7 source hash ----

    def test_event_stores_content_hash(self) -> None:
        with self._engine() as engine:
            engine.write(event=self._event(content="hello world"), project_id="p1",
                         memory_candidates=[self._candidate()])
            row = engine.conn.execute("SELECT content_hash FROM events WHERE id = 1").fetchone()
            self.assertIsNotNone(row["content_hash"])
            self.assertEqual(len(row["content_hash"]), 64)  # SHA-256 hex

    def test_event_prefers_external_content_hash_and_source_version(self) -> None:
        with self._engine() as engine:
            event = self._event(payload={"content_hash": "external_hash", "source_version": "v2"})
            engine.write(event=event, project_id="p1", memory_candidates=[self._candidate()])
            row = engine.conn.execute("SELECT content_hash, source_version FROM events WHERE id = 1").fetchone()
            self.assertEqual(row["content_hash"], "external_hash")
            self.assertEqual(row["source_version"], "v2")

    def test_validate_sources_without_resolver_returns_unknown(self) -> None:
        with self._engine() as engine:
            engine.write(event=self._event(payload={"content_hash": "h1"}), project_id="p1",
                         memory_candidates=[self._candidate()])
            result = engine.validate_sources()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["status"], "unknown")
            self.assertEqual(result[0]["reason"], "no resolver")

    def test_validate_sources_reports_ok_changed_and_missing(self) -> None:
        with self._engine() as engine:
            engine.write(event=self._event(source_ref="msg://ok", payload={"content_hash": "h1", "source_version": "v1"}),
                         project_id="p1", memory_candidates=[self._candidate(title="OK")])
            engine.write(event=self._event(source_ref="msg://changed", payload={"content_hash": "h2", "source_version": "v1"}),
                         project_id="p1", memory_candidates=[self._candidate(title="Changed")])
            engine.write(event=self._event(source_ref="msg://missing", payload={"content_hash": "h3"}),
                         project_id="p1", memory_candidates=[self._candidate(title="Missing")])

            def resolver(source_type: str, source_ref: str) -> dict:
                if source_ref == "msg://ok":
                    return {"exists": True, "content_hash": "h1", "source_version": "v1"}
                if source_ref == "msg://changed":
                    return {"exists": True, "content_hash": "new_h2", "source_version": "v2"}
                return {"exists": False}

            by_ref = {item["source_ref"]: item for item in engine.validate_sources(resolver)}
            self.assertEqual(by_ref["msg://ok"]["status"], "ok")
            self.assertEqual(by_ref["msg://changed"]["status"], "changed")
            self.assertEqual(by_ref["msg://missing"]["status"], "missing")

    # ---- tags ----

    def test_tags_stored_and_returned(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(tags=["tech", "launch"])],
            )
            results = engine.recall(RecallRequest(query="test", project_id="p1"))
            self.assertEqual(set(results[0]["tags"]), {"tech", "launch"})


# --------------------------------------------------------------------- #
# P1: Memory Layer Tests
# --------------------------------------------------------------------- #

class MemoryLayerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path("tests_runtime") / "p1" / str(uuid.uuid4())
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

    def _candidate(self, title: str = "Test", summary: str = "Test summary.",
                   memory_type: str = "decision", importance: float = 0.7,
                   confidence: float = 0.9, **kwargs) -> MemoryCandidate:
        defaults = dict(memory_type=memory_type, title=title, summary=summary,
                        content={"scope": "project"}, importance=importance, confidence=confidence,
                        evidence=[{"source_ref": "msg://1"}])
        defaults.update(kwargs)
        return MemoryCandidate(**defaults)

    def test_memory_layer_default_is_factual(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Use option B")],
            )
            row = engine.conn.execute("SELECT memory_layer FROM memories LIMIT 1").fetchone()
            self.assertEqual(row["memory_layer"], "factual")

    def test_write_working_memory(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Temp task state")],
                memory_layer="working",
            )
            row = engine.conn.execute("SELECT memory_layer FROM memories LIMIT 1").fetchone()
            self.assertEqual(row["memory_layer"], "working")

    def test_write_rejects_invalid_memory_layer(self) -> None:
        with self._engine() as engine:
            with self.assertRaises(ValueError) as ctx:
                engine.write(
                    event=self._event(), project_id="p1", user_id="u1",
                    memory_candidates=[self._candidate(title="Invalid layer")],
                    memory_layer="longterm",
                )
            self.assertIn("invalid memory_layer", str(ctx.exception))

    def test_recall_rejects_invalid_memory_layer(self) -> None:
        with self._engine() as engine:
            with self.assertRaises(ValueError) as ctx:
                engine.recall(RecallRequest(query="x", memory_layer="longterm"))
            self.assertIn("invalid memory_layer", str(ctx.exception))

    def test_recall_filters_by_memory_layer(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Working Memory")],
                memory_layer="working",
            )
            engine.write(
                event=self._event(source_ref="msg://2"), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Factual Memory")],
                memory_layer="factual",
            )
            w_results = engine.recall(
                RecallRequest(query="Memory", project_id="p1", memory_layer="working"),
            )
            self.assertEqual(len(w_results), 1)
            self.assertEqual(w_results[0]["title"], "Working Memory")
            f_results = engine.recall(
                RecallRequest(query="Memory", project_id="p1", memory_layer="factual"),
            )
            self.assertEqual(len(f_results), 1)
            self.assertEqual(f_results[0]["title"], "Factual Memory")

    def test_recall_all_layers_by_default(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Working")],
                memory_layer="working",
            )
            engine.write(
                event=self._event(source_ref="msg://2"), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Factual")],
                memory_layer="factual",
            )
            results = engine.recall(RecallRequest(query="Working Factual", project_id="p1"))
            self.assertGreaterEqual(len(results), 2)

    def test_confidence_tier_annotation(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[
                    self._candidate(title="High", confidence=0.9),
                    self._candidate(title="Med", confidence=0.55),
                    self._candidate(title="Low", confidence=0.3),
                ],
            )
            results = engine.recall(
                RecallRequest(query="High Med Low", project_id="p1"), limit=3,
            )
            tiers = {r["title"]: r["confidence_tier"] for r in results}
            self.assertEqual(tiers["High"], 1)
            self.assertEqual(tiers["Med"], 2)
            self.assertEqual(tiers["Low"], 3)
            self.assertEqual(results[0]["confidence_tier_label"], "direct_injection")

    def test_promote_working_to_factual(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Temp state", memory_type="task_status")],
                memory_layer="working",
            )
            memory_id = engine.conn.execute("SELECT id FROM memories LIMIT 1").fetchone()["id"]
            result = engine.promote(memory_id, user_id="u1")
            new_row = engine.conn.execute(
                "SELECT memory_layer, status FROM memories WHERE id = ?",
                (result["new_memory_id"],),
            ).fetchone()
            self.assertEqual(new_row["memory_layer"], "factual")
            self.assertEqual(new_row["status"], "active")
            old_row = engine.conn.execute(
                "SELECT status FROM memories WHERE id = ?", (memory_id,),
            ).fetchone()
            self.assertEqual(old_row["status"], "promoted")

    def test_update_preserves_memory_layer(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Working draft")],
                memory_layer="working",
            )
            memory_id = engine.conn.execute("SELECT id FROM memories LIMIT 1").fetchone()["id"]
            result = engine.update(
                memory_id,
                self._candidate(title="Working draft updated"),
                self._event(source_ref="msg://update"),
                project_id="p1",
                user_id="u1",
            )
            row = engine.conn.execute(
                "SELECT memory_layer FROM memories WHERE id = ?",
                (result["new_memory_id"],),
            ).fetchone()
            self.assertEqual(row["memory_layer"], "working")

    def test_promote_requires_working_layer(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate()],
                memory_layer="factual",
            )
            memory_id = engine.conn.execute("SELECT id FROM memories LIMIT 1").fetchone()["id"]
            with self.assertRaises(ValueError) as ctx:
                engine.promote(memory_id)
            self.assertIn("not working", str(ctx.exception))

    def test_flush_marks_promotion_candidates(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Temp")],
                memory_layer="working",
            )
            result = engine.flush(project_id="p1", reason="context_80pct")
            self.assertGreaterEqual(result["marked_count"], 1)
            flagged = engine.conn.execute(
                "SELECT promotion_candidate FROM memories WHERE id = 1",
            ).fetchone()
            self.assertEqual(flagged["promotion_candidate"], 1)

    def test_flush_returns_correct_reason(self) -> None:
        with self._engine() as engine:
            result = engine.flush(project_id="p1", reason="task_stage_transition")
            self.assertEqual(result["flush_reason"], "task_stage_transition")

    def test_compact_expired_working_memory_creates_audit(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Old working")],
                memory_layer="working",
            )
            memory_id = engine.conn.execute("SELECT id FROM memories LIMIT 1").fetchone()["id"]
            engine.conn.execute(
                "UPDATE memories SET created_at=?, updated_at=? WHERE id=?",
                ("2026-04-20T00:00:00+00:00", "2026-04-20T00:00:00+00:00", memory_id),
            )
            engine.conn.commit()
            result = engine.compact()
            self.assertEqual(result["expired_working"], 1)
            audit = engine.conn.execute(
                "SELECT action FROM audit_log WHERE target_id=? AND action=?",
                (memory_id, "compact_archive"),
            ).fetchone()
            self.assertIsNotNone(audit)

    def test_query_rewrite_is_identity_phase1(self) -> None:
        with self._engine() as engine:
            ctx = RecallContext(user_id="u1", project_id="p1", task_id=None,
                                intent="general", last_queries=[])
            result = engine._rewrite_query("原始查询内容", ctx)
            self.assertEqual(result, "原始查询内容")

    def test_conflict_fact_override_supersedes(self) -> None:
        """fact_override: new memory supersedes old via version chain."""
        with self._engine() as engine:
            engine.write(
                event=self._event(source_ref="msg://1"),
                project_id="proj-alpha", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Project architecture decision",
                    summary="Decided to use microservices for the backend system.",
                    memory_type="decision",
                    content={"scope": "project", "project_id": "proj-alpha"},
                )],
            )
            result = engine.write(
                event=self._event(source_ref="msg://2"),
                project_id="proj-alpha", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Project architecture decision",
                    summary="Decided to use monolith architecture for the backend system.",
                    memory_type="decision",
                    content={"scope": "project", "project_id": "proj-alpha"},
                )],
            )
            # A conflict should be detected
            self.assertGreaterEqual(len(result.get("conflicts", [])), 1)
            conflict_types = {c["conflict_type"] for c in result["conflicts"]}
            self.assertIn("fact_override", conflict_types)
            old_row = engine.conn.execute(
                "SELECT status, superseded_by FROM memories WHERE id = 1",
            ).fetchone()
            new_row = engine.conn.execute(
                "SELECT uuid FROM memories WHERE id = ?",
                (result["memory_ids"][0],),
            ).fetchone()
            self.assertEqual(old_row["status"], "superseded")
            self.assertEqual(old_row["superseded_by"], new_row["uuid"])

    def test_conflict_evidence_conflict_keeps_both(self) -> None:
        """evidence_conflict: both memories remain active, confidence lowered."""
        with self._engine() as engine:
            engine.write(
                event=self._event(source_ref="msg://1"),
                project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Sprint velocity report",
                    summary="Team velocity is 30 story points per sprint.",
                    memory_type="semantic",
                )],
            )
            result = engine.write(
                event=self._event(source_ref="msg://2"),
                project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Sprint velocity report",
                    summary="Team velocity updated to 60 story points per sprint.",
                    memory_type="semantic",
                )],
            )
            # evidence_conflict is detected (overlap > 0.6 but < 0.85)
            conflict_types = [c["conflict_type"] for c in result.get("conflicts", [])]
            self.assertIn("evidence_conflict", conflict_types)

    def test_conflict_evidence_conflict_precedes_fact_override(self) -> None:
        """evidence_conflict is more specific than same-project fact_override."""
        with self._engine() as engine:
            engine.write(
                event=self._event(source_ref="msg://1"),
                project_id="proj-alpha", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Latency target decision",
                    summary="Decided API latency target is 200 ms for checkout.",
                    memory_type="decision",
                    content={"scope": "project", "project_id": "proj-alpha"},
                )],
            )
            result = engine.write(
                event=self._event(source_ref="msg://2"),
                project_id="proj-alpha", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Latency target decision",
                    summary="Decided API latency target is 500 ms for checkout.",
                    memory_type="decision",
                    content={"scope": "project", "project_id": "proj-alpha"},
                )],
            )
            self.assertGreaterEqual(len(result.get("conflicts", [])), 1)
            self.assertEqual(result["conflicts"][0]["conflict_type"], "evidence_conflict")
            old_row = engine.conn.execute("SELECT status FROM memories WHERE id = 1").fetchone()
            self.assertEqual(old_row["status"], "active")

    def test_conflict_role_change_supersedes(self) -> None:
        """role_change: decision about a role triggers supersede + notify.

        Bypasses fact_override by omitting project_id from content dict,
        so _same_factual_topic returns False.
        """
        with self._engine() as engine:
            engine.write(
                event=self._event(source_ref="msg://1"),
                project_id="proj-alpha", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Project owner assignment",
                    summary="Alice is assigned as the project owner and lead.",
                    memory_type="decision",
                    content={"scope": "project"},
                )],
            )
            result = engine.write(
                event=self._event(source_ref="msg://2"),
                project_id="proj-alpha", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Project owner reassignment",
                    summary="Bob is the new project owner and lead, Alice is relieved.",
                    memory_type="decision",
                    content={"scope": "project"},
                )],
            )
            conflict_types = {c["conflict_type"] for c in result.get("conflicts", [])}
            self.assertIn("role_change", conflict_types)
            rc = next(c for c in result["conflicts"] if c["conflict_type"] == "role_change")
            self.assertEqual(rc["resolution_action"], "supersede")
            old_row = engine.conn.execute(
                "SELECT status FROM memories WHERE id = 1",
            ).fetchone()
            self.assertEqual(old_row["status"], "superseded")

    def test_conflict_goal_drift_keeps_both(self) -> None:
        """goal_drift: conflicting goals are preserved as decision chain.

        Bypasses fact_override by omitting project_id from content dict.
        Title contains 'goal' and 'plan' keywords to trigger goal_drift.
        """
        with self._engine() as engine:
            engine.write(
                event=self._event(source_ref="msg://1"),
                project_id="proj-alpha", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Project goal plan overview",
                    summary="We plan to launch the product in Q3.",
                    memory_type="decision",
                    content={"scope": "project"},
                )],
            )
            result = engine.write(
                event=self._event(source_ref="msg://2"),
                project_id="proj-alpha", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Project goal plan adjustment",
                    summary="We plan to launch the product in Q4 instead.",
                    memory_type="decision",
                    content={"scope": "project"},
                )],
            )
            conflict_types = {c["conflict_type"] for c in result.get("conflicts", [])}
            self.assertIn("goal_drift", conflict_types)
            gd = next(c for c in result["conflicts"] if c["conflict_type"] == "goal_drift")
            self.assertEqual(gd["resolution_action"], "keep_both")
            old_row = engine.conn.execute(
                "SELECT status FROM memories WHERE id = 1",
            ).fetchone()
            self.assertEqual(old_row["status"], "active")

    def test_conflict_constraint_supplement_keeps_both(self) -> None:
        """constraint_supplement: semantic memory with additive keywords keeps both.

        The classifier checks the existing memory's summary for additive keywords.
        Avoid different numbers to prevent evidence_conflict from matching first.
        """
        with self._engine() as engine:
            engine.write(
                event=self._event(source_ref="msg://1"),
                project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Production health check policy",
                    summary="Also requires all services to have health check endpoints.",
                    memory_type="semantic",
                )],
            )
            result = engine.write(
                event=self._event(source_ref="msg://2"),
                project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(
                    title="Production health check policy",
                    summary="Requires all services to have health check endpoints enabled.",
                    memory_type="semantic",
                )],
            )
            conflict_types = {c["conflict_type"] for c in result.get("conflicts", [])}
            self.assertIn("constraint_supplement", conflict_types)
            cs = next(c for c in result["conflicts"] if c["conflict_type"] == "constraint_supplement")
            self.assertEqual(cs["resolution_action"], "keep_both")
            # Both memories remain active
            old_row = engine.conn.execute(
                "SELECT status FROM memories WHERE id = 1",
            ).fetchone()
            self.assertEqual(old_row["status"], "active")

    def test_memory_layer_in_recall_result(self) -> None:
        with self._engine() as engine:
            engine.write(
                event=self._event(), project_id="p1", user_id="u1",
                memory_candidates=[self._candidate(title="Working item")],
                memory_layer="working",
            )
            results = engine.recall(RecallRequest(query="Working item", project_id="p1"))
            self.assertEqual(results[0]["memory_layer"], "working")


# =============================================================================
# P2: Usage-Based Promotion And Demotion
# =============================================================================

class PromotionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path("tests_runtime") / str(uuid.uuid4())
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp_dir / "memory.sqlite3"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _engine(self) -> MemoryEngine:
        return MemoryEngine(self.db_path)

    def _event(self, content: str = "Test event.", source_ref: str = "msg://1", **kwargs) -> SourceEvent:
        defaults = dict(source_type="message", source_ref=source_ref, actors=["u1"],
                        timestamp="2026-04-24T09:00:00+00:00", content=content, scope="project")
        defaults.update(kwargs)
        return SourceEvent(**defaults)

    def _candidate(self, title: str = "Test", summary: str = "Test summary.",
                  memory_type: str = "decision", importance: float = 0.7,
                  confidence: float = 0.9, **kwargs) -> MemoryCandidate:
        defaults = dict(memory_type=memory_type, title=title, summary=summary,
                       content={"scope": "project"}, importance=importance, confidence=confidence,
                       evidence=[{"source_ref": "msg://1"}])
        defaults.update(kwargs)
        return MemoryCandidate(**defaults)

    def _write(self, engine: MemoryEngine, **kwargs) -> int:
        result = engine.write(
            event=self._event(),
            project_id="p1", user_id="u1",
            memory_candidates=[self._candidate(**kwargs)],
        )
        return result["memory_ids"][0]

    def _log_recall(self, engine: MemoryEngine, memory_id: int, task_id: str, was_returned: int = 1) -> None:
        """Directly insert a recall_log entry for testing promotion signals."""
        now = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        engine.conn.execute(
            """INSERT INTO recall_log
               (memory_id, query, raw_score, confidence, rank_index, was_returned,
                project_id, task_id, user_id, recalled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (memory_id, "test query", 0.5, 0.9, 0, was_returned,
             "p1", task_id, "u1", now),
        )
        engine.conn.commit()

    # ---- L1 → L2 Direction B: same-theme decisions ≥ 3 ----

    def test_l1_to_l2_b_same_theme_3(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Decision A", memory_type="decision", confidence=0.8)
            # Log 3 returns from same task (same-theme signal)
            for _ in range(3):
                self._log_recall(engine, mid, task_id="t1")
            # Review should promote
            result = engine.review()
            self.assertEqual(len(result["promotions"]), 1)
            self.assertEqual(result["promotions"][0]["from_layer"], "L1")
            self.assertEqual(result["promotions"][0]["to_layer"], "L2")
            self.assertEqual(result["promotions"][0]["direction"], "B")
            self.assertEqual(result["promotions"][0]["trigger"], "same_theme_decisions_3")

    # ---- L1 → L2 Direction B: cross-task recalls ≥ 2 ----

    def test_l1_to_l2_governance_votes_are_recorded(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Governed decision", memory_type="decision", confidence=0.8)
            for _ in range(3):
                self._log_recall(engine, mid, task_id="t1")

            result = engine.review()

            vote_count = engine.conn.execute(
                "SELECT COUNT(*) FROM memory_votes WHERE candidate_memory_id = ?",
                (mid,),
            ).fetchone()[0]
            ballot = engine.conn.execute(
                """
                SELECT COUNT(DISTINCT assembly_id) AS assembly_count,
                       MIN(ballot_kind) AS ballot_kind
                FROM memory_votes
                WHERE candidate_memory_id = ?
                """,
                (mid,),
            ).fetchone()
            evidence_vote = engine.conn.execute(
                """
                SELECT evidence_refs_json
                FROM memory_votes
                WHERE candidate_memory_id = ?
                  AND reviewer_name = 'EvidenceReviewer'
                LIMIT 1
                """,
                (mid,),
            ).fetchone()
            self.assertEqual(len(result["promotions"]), 1)
            self.assertTrue(result["promotions"][0]["governance_assembly_id"])
            self.assertEqual(vote_count, 3)
            self.assertEqual(ballot["assembly_count"], 1)
            self.assertEqual(ballot["ballot_kind"], "deterministic_citizen_assembly")
            self.assertIn("msg://", evidence_vote["evidence_refs_json"])

    def test_l1_to_l2_governance_rejects_privacy_risk(self) -> None:
        with self._engine() as engine:
            mid = self._write(
                engine,
                title="Risky decision",
                summary="Do not promote [api_key:REDACTED] details.",
                memory_type="decision",
                confidence=0.8,
            )
            for _ in range(3):
                self._log_recall(engine, mid, task_id="t1")

            result = engine.review()

            row = engine.conn.execute(
                "SELECT logical_layer, change_reason FROM memories WHERE id = ?",
                (mid,),
            ).fetchone()
            votes = engine.conn.execute(
                "SELECT reviewer_name, vote, assembly_id, ballot_kind FROM memory_votes WHERE candidate_memory_id = ?",
                (mid,),
            ).fetchall()
            self.assertEqual(len(result["promotions"]), 0)
            self.assertEqual(len(result["governance_rejections"]), 1)
            self.assertTrue(result["governance_rejections"][0]["governance_assembly_id"])
            self.assertEqual(row["logical_layer"], "L1")
            self.assertIn("governance rejected promotion", row["change_reason"])
            self.assertTrue(any(v["reviewer_name"] == "PrivacyReviewer" and v["vote"] == "reject" for v in votes))
            self.assertEqual(len({v["assembly_id"] for v in votes}), 1)
            self.assertTrue(all(v["ballot_kind"] == "deterministic_citizen_assembly" for v in votes))

    def test_l1_to_l2_external_ballot_rejects_promotion(self) -> None:
        def provider(context):
            return [
                {
                    "reviewer_name": "AgentScopeReviewer",
                    "vote": "reject",
                    "score": 0.3,
                    "reason": f"external reviewer rejected {context['topic']}",
                }
            ]

        with MemoryEngine(self.db_path, governance_ballot_provider=provider) as engine:
            mid = self._write(engine, title="Externally governed decision", memory_type="decision", confidence=0.8)
            for _ in range(3):
                self._log_recall(engine, mid, task_id="t1")

            result = engine.review()

            row = engine.conn.execute(
                "SELECT logical_layer, change_reason FROM memories WHERE id = ?",
                (mid,),
            ).fetchone()
            votes = engine.conn.execute(
                """
                SELECT reviewer_name, vote, assembly_id, ballot_kind
                FROM memory_votes
                WHERE candidate_memory_id = ?
                """,
                (mid,),
            ).fetchall()

            self.assertEqual(len(result["promotions"]), 0)
            self.assertEqual(len(result["governance_rejections"]), 1)
            self.assertIn("AgentScopeReviewer", result["governance_rejections"][0]["governance_reason"])
            self.assertEqual(row["logical_layer"], "L1")
            self.assertIn("governance rejected promotion", row["change_reason"])
            self.assertEqual(len(votes), 4)
            self.assertTrue(any(v["reviewer_name"] == "AgentScopeReviewer" and v["vote"] == "reject" for v in votes))
            self.assertEqual(len({v["assembly_id"] for v in votes}), 1)
            self.assertTrue(all(v["ballot_kind"] == "deterministic_citizen_assembly" for v in votes))

    def test_l1_to_l2_b_cross_task_2(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Decision B", memory_type="decision", confidence=0.8)
            # Log returns from 2 different tasks
            self._log_recall(engine, mid, task_id="t1")
            self._log_recall(engine, mid, task_id="t2")
            result = engine.review()
            self.assertEqual(len(result["promotions"]), 1)
            self.assertEqual(result["promotions"][0]["trigger"], "cross_task_2")

    # ---- L1 → L2 Direction B: confidence < 0.6 blocks promotion ----

    def test_l1_to_l2_b_blocked_by_low_confidence(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Low confidence decision",
                              memory_type="decision", confidence=0.5)
            for _ in range(5):
                self._log_recall(engine, mid, task_id="t1")
            result = engine.review()
            self.assertEqual(len(result["promotions"]), 0)

    # ---- L1 → L2 Direction C: cross-scene consistency ≥ 2 projects ----

    def test_l1_to_l2_c_cross_scene_2(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="User preference",
                              memory_type="preference", confidence=0.8)
            # Log returns from 2 different projects
            for proj in ("p1", "p2"):
                self._log_recall(engine, mid, task_id=f"task_{proj}")
                # Update only the most recent recall_log entry to this project
                last_id = engine.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                engine.conn.execute(
                    "UPDATE recall_log SET project_id = ? WHERE id = ?",
                    (proj, last_id),
                )
                engine.conn.commit()
            result = engine.review()
            self.assertEqual(len(result["promotions"]), 1)
            self.assertEqual(result["promotions"][0]["direction"], "C")
            self.assertEqual(result["promotions"][0]["trigger"], "cross_scene_2")

    # ---- L1 → L2 Direction C: persistence ≥ 7 days ----

    def test_l1_to_l2_c_persistent_7d(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Old preference",
                              memory_type="preference", confidence=0.8)
            # Set created_at to 8 days ago
            old_ts = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
            engine.conn.execute(
                "UPDATE memories SET created_at = ? WHERE id = ?",
                (old_ts, mid),
            )
            engine.conn.commit()
            # No recall logs — persistence alone should trigger
            result = engine.review()
            self.assertEqual(len(result["promotions"]), 1)
            self.assertEqual(result["promotions"][0]["trigger"], "persistent_7d")

    # ---- L2 → L3 Direction B: multi-role reference ----

    def test_l2_to_l3_b_multi_role(self) -> None:
        with self._engine() as engine:
            # Write an event with multiple actors
            event = self._event(actors=["alice", "bob", "charlie"])
            engine.write(event=event, project_id="p1", user_id="alice",
                         memory_candidates=[
                             self._candidate(title="Multi-party decision",
                                             memory_type="decision", confidence=0.8)
                         ])
            mid = engine.conn.execute(
                "SELECT id FROM memories WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]
            # Set it to L2 first
            engine.conn.execute(
                "UPDATE memories SET logical_layer = 'L2' WHERE id = ?", (mid,)
            )
            engine.conn.commit()
            result = engine.review()
            self.assertEqual(len(result["promotions"]), 1)
            self.assertEqual(result["promotions"][0]["to_layer"], "L3")
            self.assertEqual(result["promotions"][0]["direction"], "B")
            self.assertEqual(result["promotions"][0]["trigger"], "multi_role_reference")

    def test_l2_to_l3_b_workflow_chinese_keyword(self) -> None:
        with self._engine() as engine:
            mid = self._write(
                engine,
                title="Workflow task status",
                summary="该任务需要按照流程执行发布步骤。",
                memory_type="task_status",
                confidence=0.8,
            )
            engine.conn.execute(
                "UPDATE memories SET logical_layer = 'L2' WHERE id = ?", (mid,)
            )
            engine.conn.commit()
            result = engine.review()
            self.assertEqual(len(result["promotions"]), 1)
            self.assertEqual(result["promotions"][0]["to_layer"], "L3")
            self.assertEqual(result["promotions"][0]["trigger"], "workflow_keyword")

    # ---- L2 → L3 Direction C: pending feedback (no conflict) ----

    def test_l2_to_l3_c_pending_feedback_no_conflict(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Habit rule",
                              memory_type="habit_rule", confidence=0.8)
            # Set to L2
            engine.conn.execute(
                "UPDATE memories SET logical_layer = 'L2' WHERE id = ?", (mid,)
            )
            engine.conn.commit()
            result = engine.review()
            self.assertEqual(len(result["promotions"]), 0)
            self.assertEqual(len(result["pending_promotions"]), 1)
            self.assertEqual(result["pending_promotions"][0]["to_layer"], "L3")
            self.assertEqual(result["pending_promotions"][0]["trigger"], "pending_feedback")
            row = engine.conn.execute(
                "SELECT logical_layer FROM memories WHERE id = ?", (mid,)
            ).fetchone()
            self.assertEqual(row["logical_layer"], "L2")

    # ---- Demotion: long inactive > 30 days ----

    def test_demote_long_inactive(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Stale memory", confidence=0.8)
            # Set last_recalled_at to 31 days ago (no recall at all)
            old_ts = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
            engine.conn.execute(
                "UPDATE memories SET created_at = ? WHERE id = ?", (old_ts, mid)
            )
            engine.conn.commit()
            result = engine.review()
            self.assertEqual(len(result["demotions"]), 1)
            self.assertEqual(result["demotions"][0]["reason"], "long_inactive")

    # ---- Demotion: low importance + zero usage ----

    def test_demote_low_value_zero_usage(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Low value no usage",
                              importance=0.1, confidence=0.5)
            old_ts = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
            engine.conn.execute(
                "UPDATE memories SET created_at = ? WHERE id = ?", (old_ts, mid)
            )
            engine.conn.commit()
            result = engine.review()
            self.assertEqual(len(result["demotions"]), 1)
            self.assertEqual(result["demotions"][0]["reason"], "low_value_no_usage")

    def test_review_does_not_demote_fresh_unrecalled_memory(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Fresh important memory",
                              importance=0.9, confidence=0.9)
            result = engine.review()
            self.assertEqual(len(result["demotions"]), 0)
            row = engine.conn.execute(
                "SELECT status FROM memories WHERE id = ?", (mid,)
            ).fetchone()
            self.assertEqual(row["status"], "active")

    def test_review_scope_filters_project(self) -> None:
        with self._engine() as engine:
            mid_p1 = self._write(engine, title="Project one decision", confidence=0.8)
            result = engine.write(
                event=self._event(source_ref="msg://p2"),
                project_id="p2", user_id="u1",
                memory_candidates=[
                    self._candidate(title="Project two decision", confidence=0.8)
                ],
            )
            mid_p2 = result["memory_ids"][0]
            for _ in range(3):
                self._log_recall(engine, mid_p1, task_id="t1")
            for _ in range(3):
                engine.conn.execute(
                    """INSERT INTO recall_log
                       (memory_id, query, raw_score, confidence, rank_index, was_returned,
                        project_id, task_id, user_id, recalled_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        mid_p2, "test query", 0.5, 0.9, 0, 1,
                        "p2", "t1", "u1", datetime.now(timezone.utc).isoformat(),
                    ),
                )
            engine.conn.commit()
            result = engine.review(project_id="p1")
            self.assertEqual(len(result["promotions"]), 1)
            self.assertEqual(result["promotions"][0]["memory_id"], mid_p1)
            row_p2 = engine.conn.execute(
                "SELECT logical_layer FROM memories WHERE id = ?", (mid_p2,)
            ).fetchone()
            self.assertEqual(row_p2["logical_layer"], "L1")

    # ---- review() runs atomically ----

    def test_review_atomic_no_partial_state(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Atomic test", confidence=0.8)
            for _ in range(3):
                self._log_recall(engine, mid, task_id="t1")
            result = engine.review()
            self.assertEqual(len(result["promotions"]), 1)
            # Verify no in-progress state: all updates should be committed
            row = engine.conn.execute(
                "SELECT logical_layer, status FROM memories WHERE id = ?", (mid,)
            ).fetchone()
            self.assertEqual(row["logical_layer"], "L2")
            self.assertEqual(row["status"], "active")

    def test_review_rolls_back_on_mid_run_failure(self) -> None:
        with self._engine() as engine:
            promoted_mid = self._write(engine, title="Rollback promote", confidence=0.8)
            failing_mid = self._write(engine, title="Rollback failure", confidence=0.8)
            for _ in range(3):
                self._log_recall(engine, promoted_mid, task_id="t1")
            engine.conn.execute(
                "UPDATE memories SET logical_layer = 'L2' WHERE id = ?", (failing_mid,)
            )
            engine.conn.commit()

            with patch("memory_engine.engine.promote_l2_to_l3", side_effect=RuntimeError("forced failure")):
                with self.assertRaises(RuntimeError):
                    engine.review()

            row = engine.conn.execute(
                "SELECT logical_layer FROM memories WHERE id = ?", (promoted_mid,)
            ).fetchone()
            self.assertEqual(row["logical_layer"], "L1")

    # ---- recall() filters by logical_layer ----

    def test_recall_filters_by_logical_layer(self) -> None:
        with self._engine() as engine:
            mid_l1 = self._write(engine, title="L1 memory", confidence=0.8)
            mid_l2 = self._write(engine, title="L2 memory", confidence=0.8)
            # Promote one to L2
            engine.conn.execute(
                "UPDATE memories SET logical_layer = 'L2' WHERE id = ?", (mid_l2,)
            )
            engine.conn.commit()
            # Recall only L1
            results_l1 = engine.recall(
                RecallRequest(query="memory", logical_layer="L1")
            )
            self.assertTrue(all(r["logical_layer"] == "L1" for r in results_l1))
            # Recall only L2
            results_l2 = engine.recall(
                RecallRequest(query="memory", logical_layer="L2")
            )
            self.assertTrue(all(r["logical_layer"] == "L2" for r in results_l2))
            # Recall all (default)
            results_all = engine.recall(RecallRequest(query="memory"))
            self.assertEqual(len(results_all), 2)

    def test_recall_rejects_invalid_logical_layer(self) -> None:
        with self._engine() as engine:
            with self.assertRaises(ValueError) as ctx:
                engine.recall(RecallRequest(query="memory", logical_layer="L9"))
            self.assertIn("invalid logical_layer", str(ctx.exception))

    # ---- promote_l2() manual promotion ----

    def test_promote_l2_manual_success(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Manual promote", memory_type="decision", confidence=0.8)
            for _ in range(3):
                self._log_recall(engine, mid, task_id="t1")
            result = engine.promote_l2(mid)
            self.assertEqual(result["from_layer"], "L1")
            self.assertEqual(result["to_layer"], "L2")
            self.assertEqual(result["direction"], "B")

    def test_promote_l2_manual_not_eligible_raises(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Not eligible", confidence=0.5)
            with self.assertRaises(ValueError) as ctx:
                engine.promote_l2(mid)
            self.assertIn("does not meet", str(ctx.exception))

    # ---- demote() manual demotion ----

    def test_demote_manual(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Manual demote")
            engine.demote(mid, "user requested")
            row = engine.conn.execute(
                "SELECT status, change_reason FROM memories WHERE id = ?", (mid,)
            ).fetchone()
            self.assertEqual(row["status"], "archived")
            self.assertIn("user requested", row["change_reason"])

    # ---- logical_layer defaults to L1 ----

    def test_logical_layer_defaults_to_l1(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Default layer test")
            row = engine.conn.execute(
                "SELECT logical_layer FROM memories WHERE id = ?", (mid,)
            ).fetchone()
            self.assertEqual(row["logical_layer"], "L1")

    # ---- last_reviewed_at prevents duplicate review ----

    def test_review_skips_recently_reviewed(self) -> None:
        with self._engine() as engine:
            mid = self._write(engine, title="Recently reviewed", confidence=0.8)
            for _ in range(3):
                self._log_recall(engine, mid, task_id="t1")
            # Mark as recently reviewed
            recent = datetime.now(timezone.utc).isoformat()
            engine.conn.execute(
                "UPDATE memories SET last_reviewed_at = ? WHERE id = ?", (recent, mid)
            )
            engine.conn.commit()
            result = engine.review()
            self.assertEqual(len(result["promotions"]), 0)
            self.assertEqual(result["scanned"], 0)

    # ---- preference_decay_30d reason is used for preference memories ----

    def test_demote_preference_decay_30d(self) -> None:
        with self._engine() as engine:
            mid = self._write(
                engine, title="Old preference", summary="User prefers dark mode.",
                memory_type="preference", confidence=0.8,
            )
            old_ts = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
            engine.conn.execute(
                "UPDATE memories SET created_at = ?, logical_layer = 'L2' WHERE id = ?",
                (old_ts, mid),
            )
            # Log one recall also 31 days ago
            engine.conn.execute(
                """INSERT INTO recall_log
                   (memory_id, query, raw_score, confidence, rank_index, was_returned,
                    project_id, task_id, user_id, recalled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (mid, "dark mode", 0.5, 0.8, 0, 1, "p1", "t1", "u1", old_ts),
            )
            engine.conn.commit()
            result = engine.review()
            self.assertEqual(len(result["demotions"]), 1)
            self.assertEqual(result["demotions"][0]["reason"], "preference_decay_30d")

    # ---- L2→L3 blocked by evidence conflict ----

    def test_l2_to_l3_c_blocked_by_evidence_conflict(self) -> None:
        with self._engine() as engine:
            self._write(
                engine,
                title="Review habit rule",
                summary="Team release review requires 2 approvals before shipping.",
                memory_type="habit_rule",
                confidence=0.8,
            )
            result = engine.write(
                event=self._event(source_ref="msg://conflict"),
                project_id="p1", user_id="u1",
                memory_candidates=[
                    self._candidate(
                        title="Review habit rule",
                        summary="Team release review updated to 5 approvals before shipping.",
                        memory_type="habit_rule",
                        confidence=0.8,
                    )
                ],
            )
            self.assertIn("evidence_conflict", {c["conflict_type"] for c in result["conflicts"]})
            mid = result["memory_ids"][0]
            engine.conn.execute(
                "UPDATE memories SET logical_layer = 'L2' WHERE id = ?", (mid,)
            )
            engine.conn.commit()
            result = engine.review()
            self.assertEqual(len(result["promotions"]), 0)
            self.assertEqual(len(result["pending_promotions"]), 0)

    # ---- L2→L3 B direction with missing source event ----

    def test_l2_to_l3_b_missing_event_no_crash(self) -> None:
        with self._engine() as engine:
            mid = self._write(
                engine, title="Decision with deleted event",
                memory_type="decision", confidence=0.8,
            )
            # Point to a non-existent event_id to simulate orphaned reference
            engine.conn.execute(
                "UPDATE memories SET logical_layer = 'L2', source_event_id = 99999 WHERE id = ?",
                (mid,),
            )
            engine.conn.commit()
            result = engine.review()
            # Should not crash; event lookup returns no row, falls through
            self.assertEqual(len(result["promotions"]), 0)


if __name__ == "__main__":
    unittest.main()
