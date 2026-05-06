"""Benchmark runner: executes all test cases and reports results."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory_engine import MemoryCandidate, MemoryEngine, RecallRequest, SourceEvent
from memory_engine.governance import GovernanceRejected
from benchmarks.structures import (
    BenchmarkCase,
    FailureCase,
    InterferenceSetup,
    RecallSpec,
    ResultAssertion,
    SetupEvent,
    SetupMemory,
    SetupWorkflowOutcome,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    case_id: str
    track: str
    capability: str
    passed: bool
    duration_ms: float
    details: list[str]
    errors: list[str]
    baseline_mode: str = "memory_enabled"
    score: float | None = None
    rubric_score: float | None = None
    rubric_scores: dict[str, float | None] | None = None
    failure_type: str | None = None
    missing_memory: list[str] | None = None
    wrong_memory_used: list[str] | None = None
    notes: list[str] | None = None
    write_latency_ms: float | None = None
    retrieval_latency_ms: float | None = None
    relevant_selected_count: int | None = None
    irrelevant_selected_count: int | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    trace_completeness: float | None = None
    trace_checks_passed: int | None = None
    trace_checks_total: int | None = None
    answer_text: str | None = None
    answer_scores: dict[str, float | None] | None = None
    answer_faithfulness: float | None = None
    answer_relevancy: float | None = None
    memory_improvement: float | None = None
    memory_event_rate: float | None = None
    transcript: dict[str, Any] | None = None


@dataclass
class BenchmarkReport:
    track: str
    baseline_mode: str
    total: int
    passed: int
    failed: int
    skip: int
    cases: list[CaseResult]
    by_capability: dict[str, dict[str, int]]
    average_duration_ms: float = 0.0
    average_rubric_score: float = 0.0
    average_write_latency_ms: float = 0.0
    average_retrieval_latency_ms: float = 0.0
    average_context_precision: float = 0.0
    average_context_recall: float = 0.0
    context_evaluated_cases: int = 0
    average_trace_completeness: float = 0.0
    trace_evaluated_cases: int = 0
    average_answer_faithfulness: float = 0.0
    average_answer_relevancy: float = 0.0
    average_memory_improvement: float = 0.0
    answer_evaluated_cases: int = 0
    memory_improvement_evaluated_cases: int = 0
    average_memory_event_rate: float = 0.0
    failure_type_counts: dict[str, int] | None = None
    relevant_selected_count: int = 0
    irrelevant_selected_count: int = 0


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

BASELINE_MEMORY_ENABLED = "memory_enabled"
BASELINE_NO_MEMORY = "baseline_no_memory"
BASELINE_RECENT_CONTEXT_ONLY = "recent_context_only"
BASELINE_MODES = {
    BASELINE_MEMORY_ENABLED,
    BASELINE_NO_MEMORY,
    BASELINE_RECENT_CONTEXT_ONLY,
}
ABLATION_FLAT_KEYWORD_ONLY = "flat_keyword_only"
ABLATION_TYPED_MEMORY_NO_EVENT = "typed_memory_no_event"
ABLATION_TYPED_MEMORY_WITH_EVENT = "typed_memory_with_event"
ABLATION_TYPED_MEMORY_WITH_GOVERNANCE = "typed_memory_with_governance"
ABLATION_FULL_SYSTEM = "full_system"
ABLATION_MODES = (
    ABLATION_FLAT_KEYWORD_ONLY,
    ABLATION_TYPED_MEMORY_NO_EVENT,
    ABLATION_TYPED_MEMORY_WITH_EVENT,
    ABLATION_TYPED_MEMORY_WITH_GOVERNANCE,
    ABLATION_FULL_SYSTEM,
)
BENCHMARK_RUN_MODES = BASELINE_MODES | set(ABLATION_MODES)

def hours_ago(hours: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Case executor
# ---------------------------------------------------------------------------

def _make_event(setup: SetupEvent, project_id: str | None, task_id: str | None, user_id: str | None) -> SourceEvent:
    return SourceEvent(
        source_type=setup.source_type,
        source_ref=setup.source_ref or f"bench://{uuid.uuid4().hex[:8]}",
        actors=setup.actors,
        timestamp=hours_ago(setup.created_hours_ago),
        content=setup.content,
        scope=setup.scope,
        payload=setup.payload or {},
    )


def _make_memory(setup: SetupMemory, event_id: int, mem_scope: str = "project") -> MemoryCandidate:
    content = dict(setup.content)  # shallow copy so we don't mutate the original
    content.setdefault("scope", mem_scope)
    return MemoryCandidate(
        memory_type=setup.memory_type,
        title=setup.title,
        summary=setup.summary,
        content=content,
        importance=setup.importance,
        confidence=setup.confidence,
        evidence=setup.evidence or [{"source_ref": "bench://setup"}],
        tags=setup.tags or [],
    )


def _run_recall(engine: MemoryEngine, spec: RecallSpec) -> list[dict[str, Any]]:
    request = RecallRequest(
        query=spec.query,
        user_id=spec.user_id,
        project_id=spec.project_id,
        task_id=spec.task_id,
        scope=spec.scope,
        intent=spec.intent,
    )
    return engine.recall(request, limit=spec.limit)


def _summarize_recall_result(row: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, JSON-stable recall result for transcripts."""
    evidence = row.get("evidence") or []
    content = row.get("content") if isinstance(row.get("content"), dict) else {}
    scope = row.get("scope") or content.get("scope")
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "memory_type": row.get("memory_type"),
        "status": row.get("status"),
        "scope": scope,
        "score": row.get("score"),
        "tags": row.get("tags") or [],
        "evidence_source_refs": [
            item.get("source_ref")
            for item in evidence
            if isinstance(item, dict) and item.get("source_ref")
        ],
    }


def _build_interference(n: int) -> list[dict[str, Any]]:
    """Generate n noise memories for interference tests."""
    noise_content = [
        "今天天气很好，适合加班。",
        "午餐吃什么？楼下新开的店不错。",
        "周会推迟到下午 3 点。",
        "快递到了，帮忙取一下。",
        "会议室 A 已预订，可以用了。",
        "团建投票开始了，请大家参与。",
        "项目进度报告已上传到共享盘。",
        "明天出差，邮件回复可能延迟。",
        "空调坏了，已报修，等待维修。",
        "生日礼物已下单，预计明天送达。",
        "下周二有客户来访，请准备演示环境。",
        "代码审查意见已回复，请查看合并请求。",
        "测试环境数据库今晚会做备份维护。",
        "新同事下周入职，需要分配工位和设备。",
        "季度 OKR 提交截止日期是本周五。",
        "打印机卡纸了，行政已安排维修。",
        "产品需求文档已更新至 V2.3 版本。",
        "本月服务器费用账单已出，请确认。",
        "设计稿评审会议改到线上进行。",
        "消防演习定于本周四下午两点。",
        "合作伙伴发来了最新的接口对接文档。",
        "年会节目报名通道已关闭，共 32 人参加。",
        "监控告警规则已调整，降低了误报阈值。",
        "客户反馈邮箱最近收到了几封投诉信。",
        "新版本发布后收到了三个功能建议。",
        "财务部提醒本月报销单需在 25 号前提交。",
        "技术分享会本周主题：微服务架构实践。",
        "门禁系统升级，旧卡需要重新激活。",
        "市场部活动方案已定稿，下周开始执行。",
        "内部培训课程报名链接已发到群里。",
    ]
    result = []
    for i in range(n):
        item = noise_content[i % len(noise_content)]
        result.append({
            "memory_type": "task_status",
            "title": f"日常事项 {i + 1}",
            "summary": item,
            "content": {"scope": "project", "note": item},
            "importance": 0.3,
            "confidence": 0.5,
            "evidence": [{"source_ref": f"chat://noise/{i}"}],
        })
    return result


def _check_assertions(
    results: list[dict[str, Any]],
    assertions: list[ResultAssertion],
    errors: list[str],
    details: list[str],
) -> bool:
    """Evaluate a list of ResultAssertions against a recall result set. Returns overall passed."""
    passed = True
    for assertion in assertions:
        if assertion.type == "contains_title":
            found = any(str(assertion.value) in r.get("title", "") for r in results)
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: title '{assertion.value}' {'found' if found else 'correctly absent'}")
            else:
                passed = False
                errors.append(f"title assertion failed: {assertion.value} {'missing' if not found else 'unexpectedly present'}")

        elif assertion.type == "contains_memory_type":
            found = any(r.get("memory_type") == assertion.value for r in results)
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: memory_type '{assertion.value}' {'found' if found else 'correctly absent'}")
            else:
                passed = False
                errors.append(f"memory_type assertion failed: {assertion.value} {'missing' if not found else 'unexpectedly present'}")

        elif assertion.type == "contains_tag":
            found = any(assertion.value in r.get("tags", []) for r in results)
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: tag '{assertion.value}' {'found' if found else 'correctly absent'}")
            else:
                passed = False
                errors.append(f"tag assertion failed: {assertion.value} {'missing' if not found else 'unexpectedly present'}")

        elif assertion.type == "contains_evidence_source_ref":
            found = any(
                any(ev.get("source_ref") == assertion.value for ev in r.get("evidence", []))
                for r in results
            )
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: evidence source_ref '{assertion.value}' {'found' if found else 'correctly absent'}")
            else:
                passed = False
                errors.append(f"evidence source_ref assertion failed: {assertion.value} {'missing' if not found else 'unexpectedly present'}")

        elif assertion.type == "evidence_has_fields":
            # value must be a list of required field names
            if not isinstance(assertion.value, list):
                passed = False
                errors.append(f"evidence_has_fields value must be a list, got {type(assertion.value).__name__}")
                continue
            required_fields = assertion.value
            found_valid = False
            for r in results:
                for ev in r.get("evidence", []):
                    if all(field in ev for field in required_fields):
                        found_valid = True
                        break
                if found_valid:
                    break
            check = found_valid if not assertion.negates else not found_valid
            if check:
                details.append(f"PASS: evidence has fields {required_fields}")
            else:
                passed = False
                errors.append(f"evidence_has_fields failed: evidence missing one of {required_fields}")

        else:
            passed = False
            errors.append(f"Unknown assertion type: {assertion.type}")

    return passed


def _check_event_assertions(
    engine: MemoryEngine,
    assertions: list[ResultAssertion],
    errors: list[str],
    details: list[str],
) -> bool:
    """Evaluate event-centric assertions against event_entries and event bundles."""
    passed = True
    for assertion in assertions:
        if assertion.type == "event_entry_relation_exists":
            row = engine.conn.execute(
                "SELECT id FROM event_entries WHERE relation = ? LIMIT 1",
                (assertion.value,),
            ).fetchone()
            found = row is not None
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: event relation '{assertion.value}' {'found' if found else 'correctly absent'}")
            else:
                passed = False
                errors.append(f"event relation assertion failed: {assertion.value}")

        elif assertion.type == "event_entry_relation_count":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 2:
                passed = False
                errors.append("event_entry_relation_count value must be [relation, count]")
                continue
            relation, expected_count = assertion.value
            count = engine.conn.execute(
                "SELECT COUNT(*) FROM event_entries WHERE relation = ?",
                (relation,),
            ).fetchone()[0]
            found = int(count) == int(expected_count)
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: event relation '{relation}' count={count}")
            else:
                passed = False
                errors.append(f"event relation count failed: {relation} expected {expected_count}, got {count}")

        elif assertion.type == "event_bundle_has_relation":
            row = engine.conn.execute(
                "SELECT source_event_id FROM event_entries WHERE relation = ? LIMIT 1",
                (assertion.value,),
            ).fetchone()
            found = False
            if row is not None:
                bundle = engine.get_event_bundle(int(row["source_event_id"]))
                found = any(entry["relation"] == assertion.value for entry in bundle["event_entries"])
                found = found and len(bundle["memories"]) >= 1
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: event bundle contains relation '{assertion.value}'")
            else:
                passed = False
                errors.append(f"event bundle relation assertion failed: {assertion.value}")

        elif assertion.type == "cross_event_synthesis":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 4:
                passed = False
                errors.append("cross_event_synthesis value must be [question, status, kind, min_source_count]")
                continue
            question, expected_status, expected_kind, min_source_count = assertion.value
            event_ids = [
                int(row["id"])
                for row in engine.conn.execute("SELECT id FROM events ORDER BY id").fetchall()
            ]
            result = engine.synthesize_events(event_ids, str(question))
            found = result.get("status") == expected_status
            if expected_kind:
                found = found and any(
                    item.get("kind") == expected_kind
                    and len(item.get("source_event_ids", [])) >= int(min_source_count)
                    for item in result.get("conclusions", [])
                )
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: cross-event synthesis status={result.get('status')} kind={expected_kind}")
            else:
                passed = False
                errors.append(
                    f"cross-event synthesis failed: expected status={expected_status}, kind={expected_kind}, got {result}"
                )

        elif assertion.type == "workflow_strategy_governance_approves":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 2:
                passed = False
                errors.append("workflow_strategy_governance_approves value must be [candidate_title, vote_count]")
                continue
            candidate_title, expected_vote_count = assertion.value
            row = engine.conn.execute(
                """
                SELECT id
                FROM memories
                WHERE status = 'active'
                  AND memory_type = 'procedural'
                  AND title = ?
                  AND content_json LIKE '%workflow_strategy_candidate%'
                ORDER BY id DESC
                LIMIT 1
                """,
                (candidate_title,),
            ).fetchone()
            if row is None:
                passed = False
                errors.append(f"workflow strategy candidate not found: {candidate_title}")
                continue
            candidate_id = int(row["id"])
            try:
                result = engine.confirm_workflow_strategy_candidate(candidate_id, user_id="benchmark")
            except Exception as exc:
                passed = False
                errors.append(f"workflow strategy governance approval failed: {exc}")
                continue
            vote_count = engine.conn.execute(
                "SELECT COUNT(*) FROM memory_votes WHERE candidate_memory_id = ?",
                (candidate_id,),
            ).fetchone()[0]
            skill = engine.conn.execute(
                "SELECT id FROM memories WHERE id = ? AND status = 'active' AND content_json LIKE '%workflow_skill%'",
                (result["workflow_skill_id"],),
            ).fetchone()
            found = int(vote_count) == int(expected_vote_count) and skill is not None
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: workflow strategy governance approved '{candidate_title}'")
            else:
                passed = False
                errors.append(
                    f"workflow strategy governance approval assertion failed: votes={vote_count}, skill={skill is not None}"
                )

        elif assertion.type == "workflow_strategy_governance_rejects":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 3:
                passed = False
                errors.append("workflow_strategy_governance_rejects value must be [candidate_title, reviewer_name, vote_count]")
                continue
            candidate_title, reviewer_name, expected_vote_count = assertion.value
            row = engine.conn.execute(
                """
                SELECT id
                FROM memories
                WHERE status = 'active'
                  AND memory_type = 'procedural'
                  AND title = ?
                  AND content_json LIKE '%workflow_strategy_candidate%'
                ORDER BY id DESC
                LIMIT 1
                """,
                (candidate_title,),
            ).fetchone()
            if row is None:
                passed = False
                errors.append(f"workflow strategy candidate not found: {candidate_title}")
                continue
            candidate_id = int(row["id"])
            rejected = False
            try:
                engine.confirm_workflow_strategy_candidate(candidate_id, user_id="benchmark")
            except GovernanceRejected:
                rejected = True
            except Exception as exc:
                passed = False
                errors.append(f"workflow strategy governance rejection raised unexpected error: {exc}")
                continue
            vote_count = engine.conn.execute(
                "SELECT COUNT(*) FROM memory_votes WHERE candidate_memory_id = ?",
                (candidate_id,),
            ).fetchone()[0]
            reviewer = engine.conn.execute(
                """
                SELECT vote
                FROM memory_votes
                WHERE candidate_memory_id = ?
                  AND reviewer_name = ?
                  AND vote = 'reject'
                LIMIT 1
                """,
                (candidate_id, reviewer_name),
            ).fetchone()
            skill_count = engine.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE content_json LIKE '%workflow_skill%' AND replaces_memory_id = ?",
                (candidate_id,),
            ).fetchone()[0]
            found = rejected and int(vote_count) == int(expected_vote_count) and reviewer is not None and int(skill_count) == 0
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: workflow strategy governance rejected '{candidate_title}' via {reviewer_name}")
            else:
                passed = False
                errors.append(
                    "workflow strategy governance rejection assertion failed: "
                    f"rejected={rejected}, votes={vote_count}, reviewer_reject={reviewer is not None}, skill_count={skill_count}"
                )

        elif assertion.type == "sample_governance_ballot_reviewer_vote":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 3:
                passed = False
                errors.append("sample_governance_ballot_reviewer_vote value must be [title, reviewer_name, vote]")
                continue
            title, reviewer_name, expected_vote = assertion.value
            row = engine.conn.execute(
                """
                SELECT id, title, summary, content_json, evidence_json
                FROM memories
                WHERE title = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (title,),
            ).fetchone()
            if row is None:
                passed = False
                errors.append(f"sample governance ballot memory not found: {title}")
                continue

            context = {
                "topic": "benchmark_cli_governance",
                "candidate_memory_id": int(row["id"]),
                "title": row["title"],
                "summary": row["summary"],
                "content": json.loads(row["content_json"] or "{}"),
                "evidence": json.loads(row["evidence_json"] or "[]"),
            }
            script_path = Path(__file__).parent.parent / "scripts" / "sample_governance_ballot.py"
            try:
                completed = subprocess.run(
                    [sys.executable, str(script_path)],
                    input=json.dumps(context),
                    text=True,
                    encoding="utf-8",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    check=False,
                )
                payload = json.loads(completed.stdout or "{}")
            except Exception as exc:
                passed = False
                errors.append(f"sample governance ballot reviewer failed: {exc}")
                continue

            if completed.returncode != 0:
                passed = False
                errors.append(
                    "sample governance ballot reviewer exited "
                    f"{completed.returncode}: {completed.stderr.strip()}"
                )
                continue

            votes = payload.get("votes") or []
            found = any(
                vote.get("reviewer_name") == reviewer_name
                and vote.get("vote") == expected_vote
                and vote.get("reviewer_role") == "sample_cli_governance_reviewer"
                for vote in votes
                if isinstance(vote, dict)
            )
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: sample governance reviewer {reviewer_name} voted {expected_vote}")
            else:
                passed = False
                errors.append(
                    "sample governance ballot reviewer vote assertion failed: "
                    f"{reviewer_name} expected {expected_vote}, got {votes}"
                )

        elif assertion.type == "preference_candidate_confirm_creates_event_entry":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 2:
                passed = False
                errors.append("preference_candidate_confirm_creates_event_entry value must be [candidate_title, relation]")
                continue
            candidate_title, expected_relation = assertion.value
            row = engine.conn.execute(
                """
                SELECT id
                FROM memories
                WHERE status = 'active'
                  AND memory_type = 'preference'
                  AND title = ?
                  AND content_json LIKE '%preference_candidate%'
                ORDER BY id DESC
                LIMIT 1
                """,
                (candidate_title,),
            ).fetchone()
            if row is None:
                passed = False
                errors.append(f"preference candidate not found: {candidate_title}")
                continue
            candidate_id = int(row["id"])
            try:
                result = engine.confirm_preference_candidate(candidate_id, user_id="benchmark")
            except Exception as exc:
                passed = False
                errors.append(f"preference candidate confirmation failed: {exc}")
                continue
            entry = engine.conn.execute(
                """
                SELECT qualifiers_json
                FROM event_entries
                WHERE relation = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (expected_relation,),
            ).fetchone()
            found = False
            if entry is not None:
                try:
                    qualifiers = json.loads(entry["qualifiers_json"])
                    found = int(qualifiers.get("memory_id")) == int(result["stable_preference_id"])
                except Exception:
                    found = False
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: preference confirmation created event relation '{expected_relation}'")
            else:
                passed = False
                errors.append(
                    f"preference confirmation event assertion failed: relation={expected_relation}, found={found}"
                )

        elif assertion.type == "stable_preference_review_action_creates_event_entry":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 3:
                passed = False
                errors.append(
                    "stable_preference_review_action_creates_event_entry value must be [stable_title, action, relation]"
                )
                continue
            stable_title, action, expected_relation = assertion.value
            row = engine.conn.execute(
                """
                SELECT id
                FROM memories
                WHERE status = 'active'
                  AND memory_type = 'preference'
                  AND title = ?
                  AND content_json LIKE '%stable_preference%'
                  AND content_json LIKE '%needs_review%'
                ORDER BY id DESC
                LIMIT 1
                """,
                (stable_title,),
            ).fetchone()
            if row is None:
                passed = False
                errors.append(f"stable preference under review not found: {stable_title}")
                continue
            stable_id = int(row["id"])
            try:
                if action == "reconfirm":
                    result = engine.reconfirm_stable_preference(stable_id, user_id="benchmark")
                elif action == "reject":
                    result = engine.reject_stable_preference(stable_id, user_id="benchmark")
                else:
                    passed = False
                    errors.append(f"unknown stable preference review action: {action}")
                    continue
            except Exception as exc:
                passed = False
                errors.append(f"stable preference review action failed: {exc}")
                continue
            entry = engine.conn.execute(
                """
                SELECT qualifiers_json
                FROM event_entries
                WHERE relation = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (expected_relation,),
            ).fetchone()
            updated = engine.conn.execute(
                "SELECT status, content_json FROM memories WHERE id = ?",
                (stable_id,),
            ).fetchone()
            found = False
            if entry is not None and updated is not None:
                try:
                    qualifiers = json.loads(entry["qualifiers_json"])
                    content = json.loads(updated["content_json"])
                    expected_status = "archived" if action == "reject" else "active"
                    found = (
                        int(qualifiers.get("memory_id")) == int(result["stable_preference_id"])
                        and updated["status"] == expected_status
                        and content.get("needs_review") == "false"
                    )
                except Exception:
                    found = False
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: stable preference {action} created event relation '{expected_relation}'")
            else:
                passed = False
                errors.append(
                    f"stable preference review event assertion failed: action={action}, relation={expected_relation}"
                )

        elif assertion.type == "memory_content_kind_count":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 2:
                passed = False
                errors.append("memory_content_kind_count value must be [kind, count]")
                continue
            kind, expected_count = assertion.value
            rows = engine.conn.execute(
                """
                SELECT content_json
                FROM memories
                WHERE status = 'active'
                  AND content_json LIKE ?
                """,
                (f"%{kind}%",),
            ).fetchall()
            count = 0
            for row in rows:
                try:
                    content = json.loads(row["content_json"])
                except Exception:
                    continue
                if content.get("kind") == kind:
                    count += 1
            found = count == int(expected_count)
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: memory content kind '{kind}' count={count}")
            else:
                passed = False
                errors.append(f"memory content kind count failed: {kind} expected {expected_count}, got {count}")

        elif assertion.type == "memory_content_field_equals":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 3:
                passed = False
                errors.append("memory_content_field_equals value must be [kind, field, expected]")
                continue
            kind, field, expected = assertion.value
            rows = engine.conn.execute(
                """
                SELECT content_json
                FROM memories
                WHERE status = 'active'
                  AND content_json LIKE ?
                """,
                (f"%{kind}%",),
            ).fetchall()
            found = False
            for row in rows:
                try:
                    content = json.loads(row["content_json"])
                except Exception:
                    continue
                if content.get("kind") == kind and str(content.get(field)) == str(expected):
                    found = True
                    break
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: memory content kind '{kind}' has {field}={expected}")
            else:
                passed = False
                errors.append(f"memory content field assertion failed: {kind}.{field} expected {expected}")

        elif assertion.type == "memory_content_field_equals_any_status":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 3:
                passed = False
                errors.append("memory_content_field_equals_any_status value must be [kind, field, expected]")
                continue
            kind, field, expected = assertion.value
            rows = engine.conn.execute(
                """
                SELECT content_json
                FROM memories
                WHERE content_json LIKE ?
                """,
                (f"%{kind}%",),
            ).fetchall()
            found = False
            for row in rows:
                try:
                    content = json.loads(row["content_json"])
                except Exception:
                    continue
                if content.get("kind") == kind and str(content.get(field)) == str(expected):
                    found = True
                    break
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: memory content kind '{kind}' has {field}={expected} in any status")
            else:
                passed = False
                errors.append(f"memory content field any-status assertion failed: {kind}.{field} expected {expected}")

        elif assertion.type == "memory_content_list_contains":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 3:
                passed = False
                errors.append("memory_content_list_contains value must be [kind, field, expected_item]")
                continue
            kind, field, expected_item = assertion.value
            rows = engine.conn.execute(
                """
                SELECT content_json
                FROM memories
                WHERE status = 'active'
                  AND content_json LIKE ?
                """,
                (f"%{kind}%",),
            ).fetchall()
            found = False
            for row in rows:
                try:
                    content = json.loads(row["content_json"])
                except Exception:
                    continue
                values = content.get(field)
                if content.get("kind") == kind and isinstance(values, list) and str(expected_item) in {str(v) for v in values}:
                    found = True
                    break
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: memory content kind '{kind}' list {field} contains {expected_item}")
            else:
                passed = False
                errors.append(f"memory content list assertion failed: {kind}.{field} missing {expected_item}")

        elif assertion.type == "workflow_trace_step_field_equals":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 4:
                passed = False
                errors.append("workflow_trace_step_field_equals value must be [task_type, step_index, field, expected]")
                continue
            task_type, step_index, field, expected = assertion.value
            rows = engine.conn.execute(
                """
                SELECT content_json
                FROM memories
                WHERE status = 'active'
                  AND content_json LIKE '%workflow_trace%'
                  AND content_json LIKE ?
                """,
                (f"%{task_type}%",),
            ).fetchall()
            found = False
            for row in rows:
                try:
                    content = json.loads(row["content_json"])
                except Exception:
                    continue
                if content.get("kind") != "workflow_trace" or content.get("task_type") != task_type:
                    continue
                for step in content.get("steps") or []:
                    if str(step.get("index")) == str(step_index) and str(step.get(field)) == str(expected):
                        found = True
                        break
                if found:
                    break
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: workflow trace {task_type} step {step_index} has {field}={expected}")
            else:
                passed = False
                errors.append(
                    f"workflow trace step assertion failed: {task_type} step {step_index} {field} expected {expected}"
                )

        elif assertion.type == "memory_content_kind_status_count":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 3:
                passed = False
                errors.append("memory_content_kind_status_count value must be [kind, status, count]")
                continue
            kind, status, expected_count = assertion.value
            rows = engine.conn.execute(
                """
                SELECT content_json
                FROM memories
                WHERE status = ?
                  AND content_json LIKE ?
                """,
                (status, f"%{kind}%"),
            ).fetchall()
            count = 0
            for row in rows:
                try:
                    content = json.loads(row["content_json"])
                except Exception:
                    continue
                if content.get("kind") == kind:
                    count += 1
            found = count == int(expected_count)
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: memory content kind '{kind}' status={status} count={count}")
            else:
                passed = False
                errors.append(
                    f"memory content kind status count failed: {kind} status={status} expected {expected_count}, got {count}"
                )

        elif assertion.type == "memory_title_logical_layer_equals":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 2:
                passed = False
                errors.append("memory_title_logical_layer_equals value must be [title, logical_layer]")
                continue
            title, expected_layer = assertion.value
            row = engine.conn.execute(
                """
                SELECT logical_layer
                FROM memories
                WHERE title = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (title,),
            ).fetchone()
            found = row is not None and str(row["logical_layer"]) == str(expected_layer)
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: memory '{title}' logical_layer={expected_layer}")
            else:
                actual = row["logical_layer"] if row is not None else None
                passed = False
                errors.append(f"memory logical layer assertion failed: {title} expected {expected_layer}, got {actual}")

        elif assertion.type == "memory_title_change_reason_contains":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 2:
                passed = False
                errors.append("memory_title_change_reason_contains value must be [title, substring]")
                continue
            title, expected_text = assertion.value
            row = engine.conn.execute(
                """
                SELECT change_reason
                FROM memories
                WHERE title = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (title,),
            ).fetchone()
            actual = str(row["change_reason"] or "") if row is not None else ""
            found = str(expected_text) in actual
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: memory '{title}' change_reason contains '{expected_text}'")
            else:
                passed = False
                errors.append(f"memory change_reason assertion failed: {title} missing {expected_text}, got {actual}")

        elif assertion.type == "memory_vote_count":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 2:
                passed = False
                errors.append("memory_vote_count value must be [title, expected_count]")
                continue
            title, expected_count = assertion.value
            row = engine.conn.execute(
                """
                SELECT id
                FROM memories
                WHERE title = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (title,),
            ).fetchone()
            count = 0
            if row is not None:
                count = engine.conn.execute(
                    "SELECT COUNT(*) FROM memory_votes WHERE candidate_memory_id = ?",
                    (int(row["id"]),),
                ).fetchone()[0]
            found = int(count) == int(expected_count)
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: memory '{title}' vote_count={count}")
            else:
                passed = False
                errors.append(f"memory vote count assertion failed: {title} expected {expected_count}, got {count}")

        elif assertion.type == "memory_reviewer_vote":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 3:
                passed = False
                errors.append("memory_reviewer_vote value must be [title, reviewer_name, vote]")
                continue
            title, reviewer_name, expected_vote = assertion.value
            row = engine.conn.execute(
                """
                SELECT id
                FROM memories
                WHERE title = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (title,),
            ).fetchone()
            vote_row = None
            if row is not None:
                vote_row = engine.conn.execute(
                    """
                    SELECT vote
                    FROM memory_votes
                    WHERE candidate_memory_id = ?
                      AND reviewer_name = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (int(row["id"]), reviewer_name),
                ).fetchone()
            found = vote_row is not None and str(vote_row["vote"]) == str(expected_vote)
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: memory '{title}' reviewer {reviewer_name} voted {expected_vote}")
            else:
                actual = vote_row["vote"] if vote_row is not None else None
                passed = False
                errors.append(
                    f"memory reviewer vote assertion failed: {title} {reviewer_name} expected {expected_vote}, got {actual}"
                )

        elif assertion.type == "memory_vote_assembly":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 3:
                passed = False
                errors.append("memory_vote_assembly value must be [title, ballot_kind, expected_assembly_count]")
                continue
            title, ballot_kind, expected_assembly_count = assertion.value
            row = engine.conn.execute(
                """
                SELECT id
                FROM memories
                WHERE title = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (title,),
            ).fetchone()
            assembly_count = 0
            missing_metadata = 0
            if row is not None:
                summary = engine.conn.execute(
                    """
                    SELECT COUNT(DISTINCT assembly_id) AS assembly_count,
                           SUM(CASE WHEN ballot_kind = ? AND reviewer_role IS NOT NULL THEN 0 ELSE 1 END) AS missing_metadata
                    FROM memory_votes
                    WHERE candidate_memory_id = ?
                    """,
                    (ballot_kind, int(row["id"])),
                ).fetchone()
                assembly_count = int(summary["assembly_count"] or 0)
                missing_metadata = int(summary["missing_metadata"] or 0)
            found = assembly_count == int(expected_assembly_count) and missing_metadata == 0
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: memory '{title}' vote assembly count={assembly_count}")
            else:
                passed = False
                errors.append(
                    f"memory vote assembly assertion failed: {title} expected {expected_assembly_count}, "
                    f"got {assembly_count}, missing_metadata={missing_metadata}"
                )

        elif assertion.type == "workflow_self_improvement_status":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 4:
                passed = False
                errors.append(
                    "workflow_self_improvement_status value must be "
                    "[task_type, expected_status, min_active_count, min_retired_or_review_count]"
                )
                continue
            task_type, expected_status, min_active_count, min_retired_count = assertion.value
            evaluation = engine.evaluate_workflow_self_improvement(str(task_type))
            found = (
                evaluation["status"] == expected_status
                and int(evaluation["active_skill_count"]) >= int(min_active_count)
                and int(evaluation["retired_or_review_skill_count"]) >= int(min_retired_count)
            )
            check = found if not assertion.negates else not found
            if check:
                details.append(
                    f"PASS: workflow self-improvement {task_type} status={evaluation['status']} "
                    f"delta={evaluation['improvement_delta']}"
                )
            else:
                passed = False
                errors.append(f"workflow self-improvement assertion failed for {task_type}: {evaluation}")

        elif assertion.type == "workflow_skill_review_action_creates_event_entry":
            if not isinstance(assertion.value, (list, tuple)) or len(assertion.value) != 3:
                passed = False
                errors.append("workflow_skill_review_action_creates_event_entry value must be [skill_title, action, relation]")
                continue
            skill_title, action, expected_relation = assertion.value
            row = engine.conn.execute(
                """
                SELECT id
                FROM memories
                WHERE status = 'active'
                  AND memory_type = 'procedural'
                  AND title = ?
                  AND content_json LIKE '%workflow_skill%'
                ORDER BY id DESC
                LIMIT 1
                """,
                (skill_title,),
            ).fetchone()
            if row is None:
                passed = False
                errors.append(f"workflow skill not found for review action: {skill_title}")
                continue
            skill_id = int(row["id"])
            try:
                if action == "reconfirm":
                    result = engine.reconfirm_workflow_skill(skill_id, user_id="benchmark")
                elif action == "reject":
                    result = engine.reject_workflow_skill(skill_id, user_id="benchmark")
                else:
                    passed = False
                    errors.append(f"unknown workflow skill review action: {action}")
                    continue
            except Exception as exc:
                passed = False
                errors.append(f"workflow skill review action failed: {exc}")
                continue
            entry = engine.conn.execute(
                """
                SELECT qualifiers_json
                FROM event_entries
                WHERE relation = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (expected_relation,),
            ).fetchone()
            updated = engine.conn.execute(
                "SELECT status, content_json FROM memories WHERE id = ?",
                (skill_id,),
            ).fetchone()
            found = False
            if entry is not None and updated is not None:
                try:
                    qualifiers = json.loads(entry["qualifiers_json"])
                    content = json.loads(updated["content_json"])
                    expected_status = "archived" if action == "reject" else "active"
                    found = (
                        int(qualifiers.get("memory_id")) == int(result["workflow_skill_id"])
                        and updated["status"] == expected_status
                        and content.get("needs_review") == "false"
                    )
                except Exception:
                    found = False
            check = found if not assertion.negates else not found
            if check:
                details.append(f"PASS: workflow skill {action} created event relation '{expected_relation}'")
            else:
                passed = False
                errors.append(f"workflow skill review event assertion failed: action={action}, relation={expected_relation}")

        else:
            passed = False
            errors.append(f"Unknown event assertion type: {assertion.type}")

    return passed


def run_case(case: BenchmarkCase, baseline_mode: str | None = None) -> CaseResult:
    """Execute a single benchmark case and return the result."""
    import time
    import traceback as _traceback
    active_baseline_mode = baseline_mode or case.baseline_mode or BASELINE_MEMORY_ENABLED
    if active_baseline_mode not in BENCHMARK_RUN_MODES:
        raise ValueError(f"unknown baseline_mode: {active_baseline_mode}")
    start = time.perf_counter()
    errors: list[str] = []
    details: list[str] = []
    notes: list[str] = []
    write_latency_ms = 0.0
    retrieval_latency_ms = 0.0
    transcript: dict[str, Any] = {
        "case_id": case.case_id,
        "track": case.track.value if hasattr(case.track, "value") else case.track,
        "capability": case.capability,
        "baseline_mode": active_baseline_mode,
        "setup": {
            "events": [],
            "memories": [],
            "interference": [],
            "workflow_outcomes": [],
            "review": None,
        },
        "recalls": [],
        "assertions": {
            "expected_titles": list(case.expected_titles),
            "forbidden_titles": list(case.forbidden_titles),
            "expected_count_range": case.expected_count_range,
            "expect_zero_results": case.expect_zero_results,
            "details": [],
            "errors": [],
        },
        "outcome": {},
        "notes": notes,
    }

    # Create a fresh engine for this case
    tmp_dir = Path("tests_runtime") / "benchmarks" / str(uuid.uuid4())
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_dir / "bench.db"

    try:
        engine = MemoryEngine(db_path)

        project_id = "proj-alpha"
        task_id = "task-alpha"
        user_id = "pm_zhang"

        should_write_setup = active_baseline_mode != BASELINE_NO_MEMORY

        if should_write_setup:
            setup_events = _baseline_setup_events(case.setup_events, active_baseline_mode)
            setup_memories = _baseline_setup_memories(case.setup_memories, active_baseline_mode)
            if active_baseline_mode == ABLATION_FLAT_KEYWORD_ONLY:
                setup_events = []
                setup_memories = [_flatten_setup_memory(memory) for memory in setup_memories]
                notes.append("flat_keyword_only: wrote setup memories as untyped semantic keyword records")
            elif active_baseline_mode == ABLATION_TYPED_MEMORY_NO_EVENT:
                setup_events = []
                notes.append("typed_memory_no_event: skipped standalone setup events and clears event entries before assertions")
            elif active_baseline_mode == ABLATION_TYPED_MEMORY_WITH_EVENT:
                notes.append("typed_memory_with_event: uses typed writes and event entries but skips governance review and workflow outcomes")
            elif active_baseline_mode == ABLATION_TYPED_MEMORY_WITH_GOVERNANCE:
                notes.append("typed_memory_with_governance: uses typed writes, event entries, and governance review; skips workflow outcomes")
            elif active_baseline_mode == ABLATION_FULL_SYSTEM:
                notes.append("full_system: equivalent to memory_enabled for ablation comparison")
            interference = case.interference if _mode_uses_interference(active_baseline_mode) else None
            workflow_outcomes = case.workflow_outcomes if _mode_uses_workflow_outcomes(active_baseline_mode) else []
        else:
            setup_events = []
            setup_memories = []
            interference = None
            workflow_outcomes = []
            notes.append("baseline_no_memory: skipped setup memories, events, interference, review, and workflow outcomes")

        # Write setup events and memories
        for ev_setup in setup_events:
            ev = _make_event(ev_setup, project_id, task_id, user_id)
            write_start = time.perf_counter()
            engine.write(event=ev, memory_candidates=[], project_id=project_id, task_id=task_id, user_id=user_id)
            write_latency_ms += (time.perf_counter() - write_start) * 1000
            transcript["setup"]["events"].append({
                "source_type": ev.source_type,
                "source_ref": ev.source_ref,
                "scope": ev.scope,
                "actors": ev.actors,
                "timestamp": ev.timestamp,
            })

        # P2: Track (memory_id, created_hours_ago) for timestamp backwrite
        timestamp_fixups: list[tuple[int, str]] = []

        for mem_setup in setup_memories:
            mem_scope = mem_setup.scope
            mem_pid = mem_setup.project_id or project_id
            mem_tid = mem_setup.task_id or task_id
            mem_uid = mem_setup.user_id or user_id
            ev = SourceEvent(
                source_type="event",
                source_ref=f"bench://{uuid.uuid4().hex[:8]}",
                actors=[mem_uid],
                timestamp=hours_ago(mem_setup.created_hours_ago),
                content=f"[Setup] {mem_setup.title}",
                scope=mem_scope,
            )
            mem = _make_memory(mem_setup, event_id=0, mem_scope=mem_scope)
            write_start = time.perf_counter()
            result = engine.write(event=ev, memory_candidates=[mem], project_id=mem_pid, task_id=mem_tid, user_id=mem_uid)
            write_latency_ms += (time.perf_counter() - write_start) * 1000
            transcript["setup"]["memories"].append({
                "title": mem_setup.title,
                "memory_type": mem_setup.memory_type,
                "scope": mem_scope,
                "project_id": mem_pid,
                "task_id": mem_tid,
                "user_id": mem_uid,
                "memory_ids": list(result.get("memory_ids", [])),
                "source_ref": ev.source_ref,
            })
            if mem_setup.logical_layer is not None:
                for mid in result.get("memory_ids", []):
                    engine.conn.execute(
                        "UPDATE memories SET logical_layer = ? WHERE id = ?",
                        (mem_setup.logical_layer, mid),
                    )
                engine.conn.commit()
            # P2: Schedule timestamp backwrite
            if mem_setup.created_hours_ago > 0:
                for mid in result.get("memory_ids", []):
                    timestamp_fixups.append((mid, hours_ago(mem_setup.created_hours_ago)))

        # Inject interference if specified
        if interference is not None:
            for i, im in enumerate(interference.memories):
                im_scope = im.scope
                im_uid = im.user_id or "interference_user"
                im_pid = im.project_id or project_id
                im_tid = im.task_id or task_id
                ev = SourceEvent(
                    source_type="message",
                    source_ref=f"chat://interference/{i}",
                    actors=[im_uid],
                    timestamp=hours_ago(im.created_hours_ago),
                    content=f"[干扰] {im.title}",
                    scope=im_scope,
                )
                mem = _make_memory(im, event_id=0, mem_scope=im_scope)
                write_start = time.perf_counter()
                result = engine.write(event=ev, memory_candidates=[mem], project_id=im_pid, task_id=im_tid, user_id=im_uid)
                write_latency_ms += (time.perf_counter() - write_start) * 1000
                transcript["setup"]["interference"].append({
                    "title": im.title,
                    "memory_type": im.memory_type,
                    "scope": im_scope,
                    "project_id": im_pid,
                    "task_id": im_tid,
                    "user_id": im_uid,
                    "memory_ids": list(result.get("memory_ids", [])),
                    "source_ref": ev.source_ref,
                })

            # P5: Use exact count from InterferenceSetup, no forced minimum
            extra_needed = interference.count - len(interference.memories)
            if extra_needed > 0:
                noise_memories = _build_interference(extra_needed)
                for i, nm in enumerate(noise_memories):
                    ev = SourceEvent(
                        source_type="message",
                        source_ref=f"chat://noise/{i}",
                        actors=["noise_user"],
                        timestamp=hours_ago(0.1),
                        content=f"噪声事件 {i}: {nm['summary']}",
                        scope="project",
                    )
                    mem = MemoryCandidate(
                        memory_type=nm["memory_type"],
                        title=nm["title"],
                        summary=nm["summary"],
                        content=nm["content"],
                        importance=nm["importance"],
                        confidence=nm["confidence"],
                        evidence=nm["evidence"],
                    )
                    write_start = time.perf_counter()
                    result = engine.write(event=ev, memory_candidates=[mem], project_id=project_id, task_id=task_id, user_id="noise_user")
                    write_latency_ms += (time.perf_counter() - write_start) * 1000
                    transcript["setup"]["interference"].append({
                        "title": nm["title"],
                        "memory_type": nm["memory_type"],
                        "scope": "project",
                        "project_id": project_id,
                        "task_id": task_id,
                        "user_id": "noise_user",
                        "memory_ids": list(result.get("memory_ids", [])),
                        "source_ref": ev.source_ref,
                    })

        # P2: Backwrite timestamps so freshness scoring reflects created_hours_ago
        for mid, ts in timestamp_fixups:
            engine.conn.execute("UPDATE memories SET created_at=?, updated_at=? WHERE id=?", (ts, ts, mid))
        if timestamp_fixups:
            engine.conn.commit()

        for outcome in workflow_outcomes:
            skill_row = engine.conn.execute(
                """
                SELECT id
                FROM memories
                WHERE status = 'active'
                  AND memory_type = 'procedural'
                  AND title = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (outcome.skill_title,),
            ).fetchone()
            if skill_row is None:
                raise ValueError(f"workflow_outcome skill not found: {outcome.skill_title}")
            result = engine.record_workflow_skill_outcome(
                int(skill_row["id"]),
                outcome=outcome.outcome,
                summary=outcome.summary,
                evidence=outcome.evidence or [{"source_ref": "bench://workflow-outcome"}],
                project_id=outcome.project_id or project_id,
                task_id=outcome.task_id or task_id,
                user_id=outcome.user_id or user_id,
            )
            details.append(
                f"Recorded workflow outcome {outcome.outcome} for '{outcome.skill_title}' "
                f"as memory {result['outcome_memory_id']}"
            )
            transcript["setup"]["workflow_outcomes"].append({
                "skill_title": outcome.skill_title,
                "outcome": outcome.outcome,
                "outcome_memory_id": result.get("outcome_memory_id"),
                "project_id": outcome.project_id or project_id,
                "task_id": outcome.task_id or task_id,
                "user_id": outcome.user_id or user_id,
            })

        if active_baseline_mode == ABLATION_TYPED_MEMORY_NO_EVENT:
            engine.conn.execute("DELETE FROM event_entries")
            engine.conn.commit()

        if case.run_review and _mode_uses_review(active_baseline_mode):
            review_result = engine.review(user_id=user_id, project_id=project_id)
            details.append(
                "Review generated "
                f"{len(review_result.get('preference_candidates', []))} preference candidates and "
                f"{len(review_result.get('workflow_strategy_candidates', []))} workflow strategy candidates"
            )
            transcript["setup"]["review"] = {
                "preference_candidate_count": len(review_result.get("preference_candidates", [])),
                "workflow_strategy_candidate_count": len(review_result.get("workflow_strategy_candidates", [])),
            }
        elif case.run_review and not _mode_uses_review(active_baseline_mode):
            notes.append(f"{active_baseline_mode}: skipped review")

        # --- Execute recalls (P1: keep per-recall results) ---
        all_results: list[dict[str, Any]] = []
        results_by_recall: list[list[dict[str, Any]]] = []

        for recall_spec in case.recalls:
            recall_start = time.perf_counter()
            results = _run_recall(engine, recall_spec)
            recall_latency = (time.perf_counter() - recall_start) * 1000
            retrieval_latency_ms += recall_latency
            results_by_recall.append(results)
            all_results.extend(results)
            transcript["recalls"].append({
                "query": recall_spec.query,
                "user_id": recall_spec.user_id,
                "project_id": recall_spec.project_id,
                "task_id": recall_spec.task_id,
                "scope": recall_spec.scope,
                "intent": recall_spec.intent,
                "limit": recall_spec.limit,
                "latency_ms": round(recall_latency, 2),
                "result_count": len(results),
                "results": [_summarize_recall_result(row) for row in results],
            })

        # --- Assertions ---
        passed = True

        # P1: Per-recall assertions
        for ri, recall_spec in enumerate(case.recalls):
            if recall_spec.assertions:
                ri_results = results_by_recall[ri]
                if not _check_assertions(ri_results, recall_spec.assertions, errors, details):
                    passed = False

        # Case-level assertions on all_results combined
        if not _check_assertions(all_results, case.assertions, errors, details):
            passed = False

        if case.event_assertions:
            if not _check_event_assertions(engine, case.event_assertions, errors, details):
                passed = False

        # Zero-result check
        if case.expect_zero_results:
            if len(all_results) > 0:
                passed = False
                errors.append(f"Expected zero results but got {len(all_results)}")
            else:
                details.append("PASS: correctly returned zero results")

        # Count range check (P6: only expected_count_range, no count_range assertion type)
        if case.expected_count_range is not None:
            min_c, max_c = case.expected_count_range
            if not (min_c <= len(all_results) <= max_c):
                passed = False
                errors.append(f"Result count {len(all_results)} outside expected range [{min_c}, {max_c}]")
            else:
                details.append(f"PASS: result count {len(all_results)} in range [{min_c}, {max_c}]")

        # Expected titles check
        for expected_title in case.expected_titles:
            found = any(expected_title in r.get("title", "") for r in all_results)
            if found:
                details.append(f"PASS: found expected title '{expected_title}'")
            else:
                passed = False
                errors.append(f"Missing expected title: '{expected_title}'")

        # Forbidden titles check
        for forbidden_title in case.forbidden_titles:
            found = any(forbidden_title in r.get("title", "") for r in all_results)
            if found:
                passed = False
                errors.append(f"Found forbidden title: '{forbidden_title}'")
            else:
                details.append(f"PASS: correctly excluded forbidden title '{forbidden_title}'")

        if passed and not errors:
            details.append(f"PASS: all assertions satisfied")
            score = 1.0
        else:
            score = 0.0

        memory_event_rate = _memory_event_rate(engine)
        failure_type = _classify_case_failure(errors)
        missing_memory = _missing_expected_titles(case, all_results)
        wrong_memory_used = _unexpected_forbidden_titles(case, all_results)
        relevant_selected_count = _matched_expected_title_count(case, all_results)
        irrelevant_selected_count = len(wrong_memory_used)
        context_precision = _context_precision(relevant_selected_count, irrelevant_selected_count)
        context_recall = _context_recall(case, relevant_selected_count)
        trace_completeness, trace_checks_passed, trace_checks_total = _trace_completeness(case, errors)
        answer_text = _generate_grounded_answer(case, all_results)
        answer_scores = _answer_scores(case, all_results, passed, active_baseline_mode)
        answer_faithfulness = answer_scores.get("faithfulness")
        answer_relevancy = answer_scores.get("relevancy")
        memory_improvement = answer_scores.get("memory_improvement")
        rubric_scores = _rubric_scores(case, all_results, passed)
        rubric_score = _average_optional_scores(rubric_scores)

    except Exception as e:
        passed = False
        errors.append(f"Exception: {e}")
        errors.append(_traceback.format_exc())
        score = 0.0
        failure_type = _classify_case_failure(errors)
        missing_memory = []
        wrong_memory_used = []
        relevant_selected_count = None
        irrelevant_selected_count = None
        context_precision = None
        context_recall = None
        trace_completeness = None
        trace_checks_passed = None
        trace_checks_total = None
        answer_text = None
        answer_scores = {}
        answer_faithfulness = None
        answer_relevancy = None
        memory_improvement = None
        rubric_scores = {}
        rubric_score = 0.0
        memory_event_rate = None

    finally:
        # P7: Close engine before removing temp directory (Windows file lock safety)
        try:
            if 'engine' in dir():
                engine.conn.close()
        except Exception:
            pass
        shutil.rmtree(tmp_dir, ignore_errors=True)

    duration_ms = (time.perf_counter() - start) * 1000
    transcript["assertions"]["details"] = list(details)
    transcript["assertions"]["errors"] = list(errors)
    transcript["outcome"] = {
        "passed": passed,
        "score": score,
        "rubric_score": rubric_score,
        "rubric_scores": rubric_scores,
        "failure_type": failure_type,
        "missing_memory": missing_memory,
        "wrong_memory_used": wrong_memory_used,
        "write_latency_ms": round(write_latency_ms, 2),
        "retrieval_latency_ms": round(retrieval_latency_ms, 2),
        "duration_ms": round(duration_ms, 2),
        "relevant_selected_count": relevant_selected_count,
        "irrelevant_selected_count": irrelevant_selected_count,
        "context_precision": context_precision,
        "context_recall": context_recall,
        "trace_completeness": trace_completeness,
        "trace_checks_passed": trace_checks_passed,
        "trace_checks_total": trace_checks_total,
        "answer_text": answer_text,
        "answer_scores": answer_scores,
        "answer_faithfulness": answer_faithfulness,
        "answer_relevancy": answer_relevancy,
        "memory_improvement": memory_improvement,
        "memory_event_rate": memory_event_rate,
    }
    return CaseResult(
        case_id=case.case_id,
        track=case.track,
        capability=case.capability,
        passed=passed,
        duration_ms=round(duration_ms, 2),
        details=details,
        errors=errors,
        baseline_mode=active_baseline_mode,
        score=score,
        rubric_score=rubric_score,
        rubric_scores=rubric_scores,
        failure_type=failure_type,
        missing_memory=missing_memory,
        wrong_memory_used=wrong_memory_used,
        notes=notes,
        write_latency_ms=round(write_latency_ms, 2),
        retrieval_latency_ms=round(retrieval_latency_ms, 2),
        relevant_selected_count=relevant_selected_count,
        irrelevant_selected_count=irrelevant_selected_count,
        context_precision=context_precision,
        context_recall=context_recall,
        trace_completeness=trace_completeness,
        trace_checks_passed=trace_checks_passed,
        trace_checks_total=trace_checks_total,
        answer_text=answer_text,
        answer_scores=answer_scores,
        answer_faithfulness=answer_faithfulness,
        answer_relevancy=answer_relevancy,
        memory_improvement=memory_improvement,
        memory_event_rate=memory_event_rate,
        transcript=transcript,
    )


def _classify_case_failure(errors: list[str]) -> str | None:
    if not errors:
        return None
    joined = "\n".join(errors).lower()

    if any(token in joined for token in ("governance", "reviewer vote", "vote count", "ballot")):
        return "governance_failed"
    if any(token in joined for token in ("workflow self-improvement", "workflow skill review", "recovery")):
        return "recovery_failed"
    if any(token in joined for token in ("event relation", "event bundle", "cross-event", "event assertion")):
        return "event_trace_missing"
    if any(token in joined for token in ("workflow trace", "workflow_trace")):
        return "event_trace_missing"
    if "conflict" in joined:
        return "conflict_not_detected"
    if any(token in joined for token in ("stale", "superseded", "obsolete", "outdated")):
        return "stale_memory_used"
    if "expected zero results" in joined:
        return "hallucinated_memory"
    if "result count" in joined and "outside expected range" in joined:
        return "over_retrieval_noise"
    if any(token in joined for token in ("found forbidden title", "unexpectedly present")):
        return "wrong_recall"
    if any(
        token in joined
        for token in (
            "missing expected title",
            "title assertion failed",
            "memory_type assertion failed",
            "tag assertion failed",
            "evidence source_ref assertion failed",
            "evidence_has_fields failed",
        )
    ):
        return "missed_recall"
    return "assertion_failed"


def _trace_completeness(case: BenchmarkCase, errors: list[str]) -> tuple[float | None, int | None, int | None]:
    trace_assertions = _trace_assertions(case)
    total = len(trace_assertions)
    if total == 0:
        return None, None, None

    failed = min(total, sum(1 for error in errors if _is_trace_error(error)))
    passed = max(0, total - failed)
    return round(passed / total, 2), passed, total


def _trace_assertions(case: BenchmarkCase) -> list[ResultAssertion]:
    assertions: list[ResultAssertion] = []
    assertions.extend(assertion for assertion in case.event_assertions if _is_trace_assertion(assertion))
    assertions.extend(assertion for assertion in case.assertions if _is_trace_assertion(assertion))
    for recall in case.recalls:
        assertions.extend(assertion for assertion in recall.assertions if _is_trace_assertion(assertion))
    return assertions


def _is_trace_assertion(assertion: ResultAssertion) -> bool:
    assertion_type = assertion.type
    return (
        assertion_type.startswith("event_")
        or assertion_type.startswith("cross_event_")
        or assertion_type.startswith("workflow_trace_")
        or assertion_type.startswith("workflow_skill_review_")
        or assertion_type.startswith("workflow_strategy_governance_")
        or assertion_type.startswith("preference_candidate_")
    )


def _is_trace_error(error: str) -> bool:
    lower = error.lower()
    return any(
        token in lower
        for token in (
            "event relation",
            "event bundle",
            "cross-event",
            "workflow trace",
            "workflow skill review",
            "workflow strategy governance",
            "preference candidate",
        )
    )


def _missing_expected_titles(case: BenchmarkCase, results: list[dict[str, Any]]) -> list[str]:
    return [
        title
        for title in case.expected_titles
        if not any(title in row.get("title", "") for row in results)
    ]


def _unexpected_forbidden_titles(case: BenchmarkCase, results: list[dict[str, Any]]) -> list[str]:
    return [
        title
        for title in case.forbidden_titles
        if any(title in row.get("title", "") for row in results)
    ]


def _generate_grounded_answer(case: BenchmarkCase, results: list[dict[str, Any]]) -> str:
    task = case.evaluation_task or case.description or case.capability
    if not results:
        return f"NO_GROUNDED_MEMORY: {task}"

    parts = [f"TASK: {task}", "GROUNDED_MEMORY:"]
    for row in results[:5]:
        evidence_refs = _result_evidence_refs(row)
        citation = f" evidence={','.join(evidence_refs)}" if evidence_refs else ""
        parts.append(f"- {row.get('title', '')}: {row.get('summary', '')}{citation}")
    return "\n".join(parts)


def _answer_scores(
    case: BenchmarkCase,
    results: list[dict[str, Any]],
    passed: bool,
    baseline_mode: str,
) -> dict[str, float | None]:
    return {
        "faithfulness": _answer_faithfulness(results),
        "relevancy": _answer_relevancy(case, results, passed),
        "memory_improvement": _answer_memory_improvement(case, results, passed, baseline_mode),
    }


def _answer_faithfulness(results: list[dict[str, Any]]) -> float:
    # The generated answer is extractive: it only emits titles, summaries, and evidence refs from recall results.
    cited_refs = {
        ref
        for row in results
        for ref in _result_evidence_refs(row)
    }
    available_refs = set(cited_refs)
    return 1.0 if cited_refs <= available_refs else 0.0


def _answer_relevancy(case: BenchmarkCase, results: list[dict[str, Any]], passed: bool) -> float:
    if case.expect_zero_results:
        return 1.0 if not results else 0.0
    if case.expected_titles:
        matched = _matched_expected_title_count(case, results) or 0
        excluded = 1.0 if not _unexpected_forbidden_titles(case, results) else 0.0
        return round(((matched / len(case.expected_titles)) + excluded) / 2.0, 4)
    if case.event_assertions or case.assertions:
        return 1.0 if passed else 0.0
    return 1.0 if passed else 0.0


def _answer_memory_improvement(
    case: BenchmarkCase,
    results: list[dict[str, Any]],
    passed: bool,
    baseline_mode: str,
) -> float | None:
    if case.expect_zero_results and not case.expected_titles and not case.event_assertions:
        return None
    if baseline_mode == BASELINE_NO_MEMORY:
        return 0.0
    if not (case.expected_titles or case.event_assertions or case.assertions):
        return None
    return 1.0 if passed and results_or_trace_available(case, results) else 0.0


def results_or_trace_available(case: BenchmarkCase, results: list[dict[str, Any]]) -> bool:
    return bool(results) or bool(case.event_assertions)


def _result_evidence_refs(row: dict[str, Any]) -> list[str]:
    evidence = row.get("evidence") or []
    return [
        str(item.get("source_ref"))
        for item in evidence
        if isinstance(item, dict) and item.get("source_ref")
    ]


def _matched_expected_title_count(case: BenchmarkCase, results: list[dict[str, Any]]) -> int | None:
    if not case.expected_titles:
        return None
    return len(case.expected_titles) - len(_missing_expected_titles(case, results))


def _context_precision(relevant_selected_count: int | None, irrelevant_selected_count: int | None) -> float | None:
    if relevant_selected_count is None:
        return None
    irrelevant = irrelevant_selected_count or 0
    selected = relevant_selected_count + irrelevant
    if selected == 0:
        return 0.0
    return round(float(relevant_selected_count) / float(selected), 4)


def _context_recall(case: BenchmarkCase, relevant_selected_count: int | None) -> float | None:
    if not case.expected_titles or relevant_selected_count is None:
        return None
    return round(float(relevant_selected_count) / float(len(case.expected_titles)), 4)


def _rubric_scores(case: BenchmarkCase, results: list[dict[str, Any]], passed: bool) -> dict[str, float | None]:
    scores: dict[str, float | None] = {
        "case_pass": 1.0 if passed else 0.0,
        "expected_title_recall": None,
        "forbidden_title_exclusion": None,
        "zero_result_correctness": None,
        "count_range_correctness": None,
    }
    if case.expected_titles:
        matched = _matched_expected_title_count(case, results) or 0
        scores["expected_title_recall"] = round(float(matched) / float(len(case.expected_titles)), 4)
    if case.forbidden_titles:
        unexpected = len(_unexpected_forbidden_titles(case, results))
        scores["forbidden_title_exclusion"] = 1.0 if unexpected == 0 else 0.0
    if case.expect_zero_results:
        scores["zero_result_correctness"] = 1.0 if len(results) == 0 else 0.0
    if case.expected_count_range is not None:
        min_c, max_c = case.expected_count_range
        scores["count_range_correctness"] = 1.0 if min_c <= len(results) <= max_c else 0.0
    return scores


def _average_optional_scores(scores: dict[str, float | None]) -> float:
    values = [value for value in scores.values() if value is not None]
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _memory_event_rate(engine: MemoryEngine) -> float | None:
    memory_count = engine.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    if int(memory_count) == 0:
        return None
    event_entry_count = engine.conn.execute("SELECT COUNT(*) FROM event_entries").fetchone()[0]
    return round(float(event_entry_count) / float(memory_count), 4)


def _baseline_setup_events(events: list[SetupEvent], baseline_mode: str) -> list[SetupEvent]:
    if baseline_mode == BASELINE_RECENT_CONTEXT_ONLY:
        return events[-1:]
    return events


def _baseline_setup_memories(memories: list[SetupMemory], baseline_mode: str) -> list[SetupMemory]:
    if baseline_mode == BASELINE_RECENT_CONTEXT_ONLY:
        return memories[-1:]
    return memories


def _flatten_setup_memory(memory: SetupMemory) -> SetupMemory:
    content = {
        "text": " ".join(
            str(part)
            for part in (
                memory.title,
                memory.summary,
                json.dumps(memory.content, ensure_ascii=False, sort_keys=True),
                " ".join(memory.tags or []),
            )
            if part
        )
    }
    return replace(memory, memory_type="semantic", content=content, tags=[])


def _mode_uses_interference(mode: str) -> bool:
    return mode in {
        BASELINE_MEMORY_ENABLED,
        ABLATION_FLAT_KEYWORD_ONLY,
        ABLATION_TYPED_MEMORY_NO_EVENT,
        ABLATION_TYPED_MEMORY_WITH_EVENT,
        ABLATION_TYPED_MEMORY_WITH_GOVERNANCE,
        ABLATION_FULL_SYSTEM,
    }


def _mode_uses_review(mode: str) -> bool:
    return mode in {
        BASELINE_MEMORY_ENABLED,
        ABLATION_TYPED_MEMORY_WITH_GOVERNANCE,
        ABLATION_FULL_SYSTEM,
    }


def _mode_uses_workflow_outcomes(mode: str) -> bool:
    return mode in {
        BASELINE_MEMORY_ENABLED,
        ABLATION_FULL_SYSTEM,
    }


def run_track(
    cases: list[BenchmarkCase],
    track_label: str,
    baseline_mode: str = BASELINE_MEMORY_ENABLED,
) -> BenchmarkReport:
    """Run all cases in a track and return the report."""
    if baseline_mode not in BENCHMARK_RUN_MODES:
        raise ValueError(f"unknown baseline_mode: {baseline_mode}")
    results: list[CaseResult] = []
    by_capability: dict[str, dict[str, int]] = {}

    for case in cases:
        result = run_case(case, baseline_mode=baseline_mode)
        results.append(result)

        cap = case.capability
        if cap not in by_capability:
            by_capability[cap] = {"passed": 0, "failed": 0}
        if result.passed:
            by_capability[cap]["passed"] += 1
        else:
            by_capability[cap]["failed"] += 1

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    failure_type_counts: dict[str, int] = {}
    for result in results:
        if result.failure_type:
            failure_type_counts[result.failure_type] = failure_type_counts.get(result.failure_type, 0) + 1

    return BenchmarkReport(
        track=track_label,
        baseline_mode=baseline_mode,
        total=len(results),
        passed=passed,
        failed=failed,
        skip=0,
        cases=results,
        by_capability=by_capability,
        average_duration_ms=_average([r.duration_ms for r in results]),
        average_rubric_score=_average([r.rubric_score for r in results if r.rubric_score is not None]),
        average_write_latency_ms=_average([r.write_latency_ms for r in results if r.write_latency_ms is not None]),
        average_retrieval_latency_ms=_average(
            [r.retrieval_latency_ms for r in results if r.retrieval_latency_ms is not None]
        ),
        average_context_precision=_average([r.context_precision for r in results if r.context_precision is not None]),
        average_context_recall=_average([r.context_recall for r in results if r.context_recall is not None]),
        context_evaluated_cases=sum(1 for r in results if r.context_precision is not None or r.context_recall is not None),
        average_trace_completeness=_average(
            [r.trace_completeness for r in results if r.trace_completeness is not None]
        ),
        trace_evaluated_cases=sum(1 for r in results if r.trace_completeness is not None),
        average_answer_faithfulness=_average(
            [r.answer_faithfulness for r in results if r.answer_faithfulness is not None]
        ),
        average_answer_relevancy=_average(
            [r.answer_relevancy for r in results if r.answer_relevancy is not None]
        ),
        average_memory_improvement=_average(
            [r.memory_improvement for r in results if r.memory_improvement is not None]
        ),
        answer_evaluated_cases=sum(1 for r in results if r.answer_relevancy is not None),
        memory_improvement_evaluated_cases=sum(1 for r in results if r.memory_improvement is not None),
        average_memory_event_rate=_average([r.memory_event_rate for r in results if r.memory_event_rate is not None]),
        failure_type_counts=failure_type_counts,
        relevant_selected_count=sum(r.relevant_selected_count or 0 for r in results),
        irrelevant_selected_count=sum(r.irrelevant_selected_count or 0 for r in results),
    )


def print_report(report: BenchmarkReport) -> None:
    print(f"\n{'='*60}")
    mode_suffix = "" if report.baseline_mode == BASELINE_MEMORY_ENABLED else f" [{report.baseline_mode}]"
    print(f"Track {report.track}{mode_suffix}: {report.passed}/{report.total} passed", end="")
    if report.failed > 0:
        print(f", {report.failed} FAILED", end="")
    print()

    # Group by capability
    for cap, counts in sorted(report.by_capability.items()):
        badge = "OK" if counts["failed"] == 0 else "FAIL"
        print(f"  {badge} {cap}: {counts['passed']} passed, {counts['failed']} failed")

    print(
        "  Metrics: "
        f"avg_duration={report.average_duration_ms:.2f}ms, "
        f"avg_rubric={report.average_rubric_score:.2f}, "
        f"avg_write={report.average_write_latency_ms:.2f}ms, "
        f"avg_recall={report.average_retrieval_latency_ms:.2f}ms, "
        f"context_precision={_format_metric(report.average_context_precision, report.context_evaluated_cases)}, "
        f"context_recall={_format_metric(report.average_context_recall, report.context_evaluated_cases)}, "
        f"trace_completeness={_format_metric(report.average_trace_completeness, report.trace_evaluated_cases)}, "
        f"answer_faithfulness={_format_metric(report.average_answer_faithfulness, report.answer_evaluated_cases)}, "
        f"answer_relevancy={_format_metric(report.average_answer_relevancy, report.answer_evaluated_cases)}, "
        f"memory_improvement={_format_metric(report.average_memory_improvement, report.memory_improvement_evaluated_cases)}, "
        f"memory_event_rate={report.average_memory_event_rate:.2f}"
    )
    if report.relevant_selected_count or report.irrelevant_selected_count:
        print(
            "  Selection: "
            f"relevant={report.relevant_selected_count}, "
            f"irrelevant={report.irrelevant_selected_count}"
        )
    if report.failure_type_counts:
        counts = ", ".join(f"{name}={count}" for name, count in sorted(report.failure_type_counts.items()))
        print(f"  Failure types: {counts}")

    # Failed cases
    failed_cases = [r for r in report.cases if not r.passed]
    if failed_cases:
        print(f"\n  Failed cases ({len(failed_cases)}):")
        for r in failed_cases:
            print(f"    [{r.case_id}] {r.capability}")
            for e in r.errors:
                print(f"        ERROR: {e}")

    print("=" * 60)


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def _format_metric(value: float, evaluated_count: int) -> str:
    if evaluated_count == 0:
        return "n/a"
    return f"{value:.2f}"


def _benchmark_tracks() -> list[tuple[str, str, list[BenchmarkCase]]]:
    """Load benchmark tracks lazily so imports stay local to runner entrypoints."""
    from benchmarks.cases.track_a import TRACK_A_CASES
    from benchmarks.cases.track_b import TRACK_B_CASES
    from benchmarks.cases.track_c import TRACK_C_CASES
    from benchmarks.cases.track_d import TRACK_D_CASES
    from benchmarks.cases.track_e import TRACK_E_CASES
    from benchmarks.cases.track_f import TRACK_F_CASES
    from benchmarks.cases.track_g import TRACK_G_CASES
    from benchmarks.cases.track_h import TRACK_H_CASES
    from benchmarks.cases.track_i import TRACK_I_CASES
    from benchmarks.cases.track_j import TRACK_J_CASES
    from benchmarks.cases.track_j_gen import TRACK_J_GEN_CASES
    from benchmarks.cases.track_k import TRACK_K_CASES
    from benchmarks.cases.track_l import TRACK_L_CASES
    from benchmarks.cases.track_m import TRACK_M_CASES

    return [
        ("A", "Dialogue Memory", TRACK_A_CASES),
        ("B", "Task Decision", TRACK_B_CASES),
        ("C", "Preference Learning", TRACK_C_CASES),
        ("D", "Structured Memory Advantage", TRACK_D_CASES),
        ("E", "Event-Centric Temporal Reasoning", TRACK_E_CASES),
        ("F", "Workflow Reflection And Reuse", TRACK_F_CASES),
        ("G", "Memory Governance", TRACK_G_CASES),
        ("H", "Long-Horizon Self Improvement", TRACK_H_CASES),
        ("I", "Agent Memory Eval Dataset MVP", TRACK_I_CASES),
        ("J", "Retrieval Quality", TRACK_J_CASES),
        ("J-gen", "Generated Retrieval Quality", TRACK_J_GEN_CASES),
        ("K", "Scale Benchmark", TRACK_K_CASES),
        ("L", "Agent Task Benchmark", TRACK_L_CASES),
        ("M", "Project Management Business Value", TRACK_M_CASES),
    ]


def _selected_benchmark_tracks(track_ids: list[str] | None = None) -> list[tuple[str, str, list[BenchmarkCase]]]:
    """Return benchmark tracks, optionally filtered by exact track id."""
    tracks = _benchmark_tracks()
    if not track_ids:
        return tracks
    wanted = {track_id.strip().lower() for track_id in track_ids if track_id.strip()}
    selected = [track for track in tracks if track[0].lower() in wanted]
    found = {track[0].lower() for track in selected}
    missing = sorted(wanted - found)
    if missing:
        known = ", ".join(track[0] for track in tracks)
        raise ValueError(f"unknown benchmark track(s): {', '.join(missing)}; known tracks: {known}")
    return selected


def run_all_benchmarks(
    baseline_mode: str = BASELINE_MEMORY_ENABLED,
    *,
    print_reports: bool = True,
    track_ids: list[str] | None = None,
) -> dict[str, BenchmarkReport]:
    """Run all benchmark tracks and return all reports."""
    if baseline_mode not in BENCHMARK_RUN_MODES:
        raise ValueError(f"unknown baseline_mode: {baseline_mode}")

    reports: dict[str, BenchmarkReport] = {}

    for track_id, track_name, cases in _selected_benchmark_tracks(track_ids):
        if print_reports:
            print(f"Running Track {track_id} ({track_name})...")
        reports[track_id] = run_track(cases, track_id, baseline_mode=baseline_mode)
        if print_reports:
            print_report(reports[track_id])

    total = sum(r.total for r in reports.values())
    passed = sum(r.passed for r in reports.values())
    failed = sum(r.failed for r in reports.values())
    if print_reports:
        mode_suffix = "" if baseline_mode == BASELINE_MEMORY_ENABLED else f" [{baseline_mode}]"
        print(f"\n{'='*60}")
        print(f"OVERALL{mode_suffix}: {passed}/{total} passed", end="")
        if failed > 0:
            print(f", {failed} FAILED")
        else:
            print(" ALL PASSED")
        print("=" * 60)

    return reports


def run_baseline_comparison(
    modes: list[str] | None = None,
    *,
    print_reports: bool = False,
    track_ids: list[str] | None = None,
) -> dict[str, dict[str, BenchmarkReport]]:
    """Run the benchmark suite once for each baseline mode."""
    selected_modes = modes or [
        BASELINE_MEMORY_ENABLED,
        BASELINE_NO_MEMORY,
        BASELINE_RECENT_CONTEXT_ONLY,
    ]
    unknown = [mode for mode in selected_modes if mode not in BASELINE_MODES]
    if unknown:
        raise ValueError(f"unknown baseline_mode: {unknown[0]}")
    return {
        mode: run_all_benchmarks(baseline_mode=mode, print_reports=print_reports, track_ids=track_ids)
        for mode in selected_modes
    }


def run_ablation_comparison(
    modes: list[str] | None = None,
    *,
    print_reports: bool = False,
) -> dict[str, dict[str, BenchmarkReport]]:
    """Run the benchmark suite once for each internal ablation mode."""
    selected_modes = modes or list(ABLATION_MODES)
    unknown = [mode for mode in selected_modes if mode not in ABLATION_MODES]
    if unknown:
        raise ValueError(f"unknown ablation_mode: {unknown[0]}")
    return {
        mode: run_all_benchmarks(baseline_mode=mode, print_reports=print_reports)
        for mode in selected_modes
    }


def export_transcripts_jsonl(
    path: str | Path,
    reports: dict[str, BenchmarkReport] | None = None,
    *,
    baseline_mode: str = BASELINE_MEMORY_ENABLED,
) -> int:
    """Write per-case benchmark transcripts to JSONL and return the record count."""
    selected_reports = reports if reports is not None else run_all_benchmarks(
        baseline_mode=baseline_mode,
        print_reports=False,
    )
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for track in sorted(selected_reports):
            for result in selected_reports[track].cases:
                record = result.transcript or _minimal_transcript(result)
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str))
                handle.write("\n")
                count += 1
    return count


def _minimal_transcript(result: CaseResult) -> dict[str, Any]:
    return {
        "case_id": result.case_id,
        "track": result.track,
        "capability": result.capability,
        "baseline_mode": result.baseline_mode,
        "setup": {},
        "recalls": [],
        "assertions": {
            "details": result.details,
            "errors": result.errors,
        },
        "outcome": {
            "passed": result.passed,
            "score": result.score,
            "rubric_score": result.rubric_score,
            "rubric_scores": result.rubric_scores,
            "failure_type": result.failure_type,
            "missing_memory": result.missing_memory,
            "wrong_memory_used": result.wrong_memory_used,
            "write_latency_ms": result.write_latency_ms,
            "retrieval_latency_ms": result.retrieval_latency_ms,
            "duration_ms": result.duration_ms,
            "context_precision": result.context_precision,
            "context_recall": result.context_recall,
            "trace_completeness": result.trace_completeness,
            "trace_checks_passed": result.trace_checks_passed,
            "trace_checks_total": result.trace_checks_total,
            "answer_text": result.answer_text,
            "answer_scores": result.answer_scores,
            "answer_faithfulness": result.answer_faithfulness,
            "answer_relevancy": result.answer_relevancy,
            "memory_improvement": result.memory_improvement,
            "memory_event_rate": result.memory_event_rate,
        },
        "notes": result.notes or [],
    }


if __name__ == "__main__":
    reports = run_all_benchmarks()
    sys.exit(1 if any(r.failed > 0 for r in reports.values()) else 0)
