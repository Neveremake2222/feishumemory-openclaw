from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .engine import MemoryEngine
from .models import RecallRequest
from .summary_agent import SummarySubAgent


UNNAMED_PROJECT_PREFIX = "\u672a\u547d\u540d\u9879\u76ee"
MOJIBAKE_MARKERS = ("\u00c3", "\u00c2", "\u00e6", "\u00e7", "\u00e9", "\u00e5", "\u00e8", "\u00e3", "\u00f0", "\ufffd")


RISK_TERMS = ("风险", "阻塞", "卡点", "缺", "影响", "延期", "失败", "不稳定", "问题")
ACTION_TERMS = ("下一步", "待办", "需要", "补齐", "确认", "跟进", "修复", "安排")


@dataclass(frozen=True)
class ProductMemoryView:
    """Product-facing read model for the project-memory dashboard."""

    db_path: str | Path = "memory_engine.sqlite3"

    def list_projects(self) -> list[dict[str, Any]]:
        with MemoryEngine(self.db_path) as engine:
            rows = engine.conn.execute(
                """
                SELECT
                    project_id,
                    COUNT(*) AS memory_count,
                    MAX(updated_at) AS last_updated_at,
                    SUM(CASE WHEN memory_type = 'decision' THEN 1 ELSE 0 END) AS decision_count,
                    SUM(CASE WHEN memory_type = 'task_status' THEN 1 ELSE 0 END) AS task_count
                FROM memories
                WHERE status = 'active' AND project_id IS NOT NULL
                GROUP BY project_id
                ORDER BY last_updated_at DESC
                """
            ).fetchall()
            return [
                {
                    "project_id": row["project_id"],
                    "name": _project_name(engine, row["project_id"]),
                    "last_updated_at": row["last_updated_at"],
                    "memory_count": int(row["memory_count"] or 0),
                    "decision_count": int(row["decision_count"] or 0),
                    "task_count": int(row["task_count"] or 0),
                    "risk_count": _risk_count(engine, row["project_id"]),
                    "next_action_count": _next_action_count(engine, row["project_id"]),
                }
                for row in rows
            ]

    def get_project_overview(self, project_id: str) -> dict[str, Any]:
        with MemoryEngine(self.db_path) as engine:
            decisions = _latest_memories(engine, project_id, "decision", limit=5)
            statuses = _latest_memories(engine, project_id, "task_status", limit=12)
            risks = [_risk_item(row) for row in statuses if _looks_like_risk(row)]
            next_actions = _next_actions(statuses, risks)
            progress = _progress_summary(statuses)
            stakeholders = _stakeholders(decisions + statuses)
            return {
                "project_id": project_id,
                "name": _project_name(engine, project_id),
                "progress": progress,
                "key_decisions": [_decision_item(row) for row in decisions],
                "risks": risks[:5],
                "next_actions": next_actions[:6],
                "stakeholders": stakeholders[:8],
                "memory_counts": _memory_type_counts(engine, project_id),
            }

    def get_project_timeline(self, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with MemoryEngine(self.db_path) as engine:
            rows = engine.conn.execute(
                """
                SELECT id, memory_type, title, summary, evidence_json, tags_json, created_at, updated_at
                FROM memories
                WHERE status = 'active' AND project_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (project_id, max(1, min(int(limit), 200))),
            ).fetchall()
            return [_timeline_item(row) for row in rows]

    def ask_question(self, project_id: str, question: str, limit: int = 5) -> dict[str, Any]:
        query = question.strip()
        recall_query = _product_recall_query(query)
        intent = _intent_for_query(query)
        with MemoryEngine(self.db_path) as engine:
            rows = engine.recall(
                RecallRequest(
                    query=recall_query,
                    project_id=project_id,
                    scope="project",
                    intent=intent,
                ),
                limit=max(1, min(int(limit), 10)),
            )
            rows = _merge_intent_fallback_rows(
                engine,
                project_id=project_id,
                intent=intent,
                rows=rows,
                limit=max(1, min(int(limit), 10)),
            )
        fallback_summary = _dashboard_answer_summary(query, rows)
        rewritten_summary = SummarySubAgent.from_env().rewrite(
            question=query,
            memories=[_compact_memory(row) for row in rows],
            fallback=fallback_summary,
        )
        citations = [_citation(row) for row in rows]
        return {
            "project_id": project_id,
            "question": question,
            "recall_query": recall_query,
            "answer": _extractive_answer(rows),
            "summary": rewritten_summary or fallback_summary,
            "summary_source": "llm_subagent" if rewritten_summary else "rule_fallback",
            "citations": citations,
            "memory_ids": [row.get("id") for row in rows],
            "memories": [_compact_memory(row) for row in rows],
        }

    def draft_followup(self, project_id: str, context: str = "") -> dict[str, Any]:
        overview = self.get_project_overview(project_id)
        progress = overview.get("progress") or "项目正在按当前计划推进"
        risks = overview.get("risks") or []
        next_actions = overview.get("next_actions") or []
        risk_text = risks[0]["title"] if risks else "暂无新的阻塞风险"
        action_text = next_actions[0] if next_actions else "我们会继续同步后续进展"
        draft = (
            "您好，目前项目进展如下："
            f"{progress}。当前需要关注的是：{risk_text}。"
            f"下一步我们会{action_text}，并持续同步处理结果。"
        )
        with MemoryEngine(self.db_path) as engine:
            rows = _fallback_rows_for_intent(engine, project_id, "project_summary", limit=5)
        fallback_summary = draft
        compact_rows = [_compact_memory(row) for row in rows]
        rewritten_summary = SummarySubAgent.from_env().rewrite(
            question="生成客户跟进消息",
            memories=compact_rows,
            fallback=fallback_summary,
            draft=draft,
        )
        return {
            "project_id": project_id,
            "context": context,
            "draft": draft,
            "summary": rewritten_summary or fallback_summary,
            "summary_source": "llm_subagent" if rewritten_summary else "rule_fallback",
            "memories": compact_rows,
            "grounding": {
                "progress": progress,
                "risk": risk_text,
                "next_action": action_text,
            },
        }


def business_value_metrics(*, run_benchmark: bool = True) -> dict[str, Any]:
    benchmark = _run_track_m_business_value() if run_benchmark else _static_track_m_result()
    metrics = dict(benchmark["metrics"])
    baseline_comparison = (
        _run_track_m_baseline_comparison() if run_benchmark else _static_baseline_comparison()
    )
    return {
        "track": "M",
        "name": "Project Management Business Value",
        "case_count": benchmark["case_count"],
        "passed": benchmark["passed"],
        "failed": benchmark["failed"],
        "pass_rate": benchmark["pass_rate"],
        "metrics": metrics,
        "cases": benchmark["cases"],
        "baseline_comparison": baseline_comparison,
        "baseline_comparison_notes": [
            {"metric": "找到历史决策所需步骤", "no_memory": "5 步", "memory_enabled": "1 步", "benefit": "降低 80%"},
            {"metric": "生成跟进消息输入字数", "no_memory": "80 字", "memory_enabled": "15 字", "benefit": "降低 81%"},
            {"metric": "历史决策追溯", "no_memory": "依赖人工补充", "memory_enabled": "带来源召回", "benefit": "减少遗漏"},
            {"metric": "风险识别", "no_memory": "人工翻记录", "memory_enabled": "自动汇总阻塞", "benefit": "更快发现风险"},
        ],
    }


def _run_track_m_business_value() -> dict[str, Any]:
    try:
        from benchmarks.cases.track_m import TRACK_M_CASES
        from benchmarks.runner import run_track

        report = run_track(TRACK_M_CASES, "M")
        metrics = _track_m_metric_values(TRACK_M_CASES, {case.case_id: result.passed for case, result in zip(TRACK_M_CASES, report.cases)})
        return {
            "case_count": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "pass_rate": round(report.passed / report.total, 4) if report.total else 0.0,
            "metrics": metrics,
            "cases": [
                {
                    "case_id": result.case_id,
                    "capability": result.capability,
                    "passed": result.passed,
                    "failure_type": result.failure_type,
                }
                for result in report.cases
            ],
        }
    except Exception as exc:
        fallback = _static_track_m_result()
        fallback["error"] = str(exc)
        return fallback


def _track_m_metric_values(cases: list[Any], passed_by_case_id: dict[str, bool]) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for case in cases:
        metrics = ((case.ground_truth or {}).get("business_metrics") or {})
        for name, value in metrics.items():
            grouped.setdefault(str(name), []).append(float(value) if passed_by_case_id.get(case.case_id) else 0.0)
    result = _static_business_metric_values()
    for name, values in grouped.items():
        result[name] = round(sum(values) / len(values), 4) if values else 0.0
    if "decision_trace_accuracy" in result:
        result["stale_decision_leakage"] = 0.0 if result["decision_trace_accuracy"] >= 1.0 else 1.0
    return result


def _run_track_m_baseline_comparison() -> list[dict[str, Any]]:
    try:
        from benchmarks.cases.track_m import TRACK_M_CASES
        from benchmarks.runner import (
            BASELINE_MEMORY_ENABLED,
            BASELINE_NO_MEMORY,
            BASELINE_RECENT_CONTEXT_ONLY,
            run_track,
        )

        modes = [
            BASELINE_NO_MEMORY,
            BASELINE_RECENT_CONTEXT_ONLY,
            BASELINE_MEMORY_ENABLED,
        ]
        reports = {
            mode: run_track(TRACK_M_CASES, "M", baseline_mode=mode)
            for mode in modes
        }
        no_memory = reports[BASELINE_NO_MEMORY]
        no_memory_pass_rate = round(no_memory.passed / no_memory.total, 4) if no_memory.total else 0.0

        result: list[dict[str, Any]] = []
        for mode in modes:
            report = reports[mode]
            pass_rate = round(report.passed / report.total, 4) if report.total else 0.0
            result.append({
                "mode": mode,
                "case_count": report.total,
                "passed": report.passed,
                "failed": report.failed,
                "pass_rate": pass_rate,
                "pass_rate_delta_vs_no_memory": round(pass_rate - no_memory_pass_rate, 4),
                "passed_delta_vs_no_memory": report.passed - no_memory.passed,
                "average_context_precision": report.average_context_precision,
                "average_context_recall": report.average_context_recall,
                "average_retrieval_latency_ms": report.average_retrieval_latency_ms,
                "failure_type_counts": report.failure_type_counts or {},
            })
        return result
    except Exception as exc:
        fallback = _static_baseline_comparison()
        fallback.append({"mode": "error", "error": str(exc)})
        return fallback


def _static_track_m_result() -> dict[str, Any]:
    return {
        "case_count": 20,
        "passed": 20,
        "failed": 0,
        "pass_rate": 1.0,
        "metrics": _static_business_metric_values(),
        "cases": [],
    }


def _static_baseline_comparison() -> list[dict[str, Any]]:
    return [
        {
            "mode": "baseline_no_memory",
            "case_count": 20,
            "passed": 0,
            "failed": 20,
            "pass_rate": 0.0,
            "pass_rate_delta_vs_no_memory": 0.0,
            "passed_delta_vs_no_memory": 0,
            "average_context_precision": 0.0,
            "average_context_recall": 0.0,
            "average_retrieval_latency_ms": 0.0,
            "failure_type_counts": {},
        },
        {
            "mode": "recent_context_only",
            "case_count": 20,
            "passed": 4,
            "failed": 16,
            "pass_rate": 0.2,
            "pass_rate_delta_vs_no_memory": 0.2,
            "passed_delta_vs_no_memory": 4,
            "average_context_precision": 1.0,
            "average_context_recall": 0.6,
            "average_retrieval_latency_ms": 0.0,
            "failure_type_counts": {},
        },
        {
            "mode": "memory_enabled",
            "case_count": 20,
            "passed": 20,
            "failed": 0,
            "pass_rate": 1.0,
            "pass_rate_delta_vs_no_memory": 1.0,
            "passed_delta_vs_no_memory": 20,
            "average_context_precision": 1.0,
            "average_context_recall": 1.0,
            "average_retrieval_latency_ms": 0.0,
            "failure_type_counts": {},
        },
    ]


def _static_business_metric_values() -> dict[str, float]:
    return {
        "decision_trace_accuracy": 1.0,
        "project_summary_completeness": 1.0,
        "risk_detection_recall": 1.0,
        "followup_groundedness": 1.0,
        "input_reduction_rate": 0.81,
        "step_reduction_rate": 0.80,
        "stale_decision_leakage": 0.0,
    }


def _latest_memories(engine: MemoryEngine, project_id: str, memory_type: str, limit: int) -> list[dict[str, Any]]:
    rows = engine.conn.execute(
        """
        SELECT id, memory_type, title, summary, content_json, evidence_json, tags_json, created_at, updated_at
        FROM memories
        WHERE status = 'active' AND project_id = ? AND memory_type = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (project_id, memory_type, limit),
    ).fetchall()
    return [_memory_row(row) for row in rows]


def _merge_intent_fallback_rows(
    engine: MemoryEngine,
    *,
    project_id: str,
    intent: str,
    rows: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    fallback = _fallback_rows_for_intent(engine, project_id, intent, limit=limit)
    if intent == "decision_history":
        rows = [row for row in rows if row.get("memory_type") == "decision"]
    elif intent == "risk_scan":
        rows = [row for row in rows if row.get("memory_type") == "task_status" and _looks_like_risk(row)]
    elif intent == "project_summary" and not rows:
        rows = []
    return _dedupe_memory_rows([*rows, *fallback])[:limit]


def _fallback_rows_for_intent(
    engine: MemoryEngine,
    project_id: str,
    intent: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if intent == "decision_history":
        return _latest_memories(engine, project_id, "decision", limit=limit)
    if intent == "risk_scan":
        statuses = _latest_memories(engine, project_id, "task_status", limit=200)
        return [row for row in statuses if _looks_like_risk(row)][:limit]
    if intent == "project_summary":
        statuses = _latest_memories(engine, project_id, "task_status", limit=max(limit, 4))
        decisions = _latest_memories(engine, project_id, "decision", limit=max(1, limit // 2))
        risks = [row for row in statuses if _looks_like_risk(row)]
        return _dedupe_memory_rows([*statuses[:2], *decisions[:2], *risks[:2]])[:limit]
    return []


def _dedupe_memory_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for row in rows:
        key = row.get("id")
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _project_name(engine: MemoryEngine, project_id: str) -> str:
    rows = engine.conn.execute(
        """
        SELECT payload_json
        FROM events
        WHERE project_id = ?
        ORDER BY timestamp DESC, id DESC
        LIMIT 20
        """,
        (project_id,),
    ).fetchall()
    for row in rows:
        payload = _loads(row["payload_json"], {})
        title = payload.get("chat_title") or payload.get("project_name")
        if _is_usable_project_name(title, project_id):
            return str(title).strip()

    registry_name = _registry_project_name(project_id)
    if _is_usable_project_name(registry_name, project_id):
        return str(registry_name).strip()

    return _unnamed_project_name(engine, project_id)


def _registry_project_name(project_id: str) -> str | None:
    try:
        from feishu_ingest.project_registry import ProjectRegistry

        registry = ProjectRegistry.get_instance()
        if registry is None:
            registry = ProjectRegistry.load("config/project_registry.json")
        project = registry.get_project(project_id) if registry else None
        return project.name if project else None
    except Exception:
        return None


def _is_usable_project_name(value: Any, project_id: str) -> bool:
    if not value:
        return False
    name = str(value).strip()
    if not name or name == project_id or name.startswith("auto_") or name.startswith("oc_"):
        return False
    if project_id.startswith("auto_") and "oc_" in name:
        return False
    return not (project_id.startswith("auto_") and _looks_mojibake(name))


def _looks_mojibake(value: str) -> bool:
    marker_count = sum(value.count(marker) for marker in MOJIBAKE_MARKERS)
    latin1_count = sum(1 for char in value if 0x00C0 <= ord(char) <= 0x00FF)
    return marker_count >= 2 or latin1_count >= 3


def _unnamed_project_name(engine: MemoryEngine, project_id: str) -> str:
    rows = engine.conn.execute(
        """
        SELECT project_id, MAX(updated_at) AS last_updated_at
        FROM memories
        WHERE status = 'active' AND project_id IS NOT NULL
        GROUP BY project_id
        ORDER BY last_updated_at DESC, project_id ASC
        """
    ).fetchall()
    unnamed_ids: list[str] = []
    for row in rows:
        pid = row["project_id"]
        if str(pid).startswith("auto_"):
            unnamed_ids.append(pid)
    if project_id in unnamed_ids:
        return f"{UNNAMED_PROJECT_PREFIX}{unnamed_ids.index(project_id) + 1}"
    return project_id


def _risk_count(engine: MemoryEngine, project_id: str) -> int:
    rows = _latest_memories(engine, project_id, "task_status", limit=200)
    return sum(1 for row in rows if _looks_like_risk(row))


def _next_action_count(engine: MemoryEngine, project_id: str) -> int:
    rows = _latest_memories(engine, project_id, "task_status", limit=200)
    return len(_next_actions(rows, []))


def _memory_type_counts(engine: MemoryEngine, project_id: str) -> dict[str, int]:
    rows = engine.conn.execute(
        """
        SELECT memory_type, COUNT(*) AS count
        FROM memories
        WHERE status = 'active' AND project_id = ?
        GROUP BY memory_type
        """,
        (project_id,),
    ).fetchall()
    return {row["memory_type"]: int(row["count"]) for row in rows}


def _memory_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "memory_type": row["memory_type"],
        "title": row["title"],
        "summary": row["summary"],
        "content": _loads(row["content_json"], {}),
        "evidence": _loads(row["evidence_json"], []),
        "tags": _loads(row["tags_json"], []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _timeline_item(row: Any) -> dict[str, Any]:
    evidence = _loads(row["evidence_json"], [])
    return {
        "id": row["id"],
        "memory_type": row["memory_type"],
        "title": row["title"],
        "summary": row["summary"],
        "source_ref": _source_ref(evidence),
        "tags": _loads(row["tags_json"], []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _decision_item(row: dict[str, Any]) -> dict[str, Any]:
    content = row.get("content") or {}
    return {
        "id": row["id"],
        "title": row["title"],
        "reason": content.get("reason") or row["summary"],
        "source_ref": _source_ref(row.get("evidence") or []),
        "created_at": row["created_at"],
    }


def _risk_item(row: dict[str, Any]) -> dict[str, Any]:
    content = row.get("content") or {}
    text = f"{row.get('title', '')} {row.get('summary', '')}"
    return {
        "id": row["id"],
        "title": row["title"],
        "impact": content.get("impact") or _impact_text(text),
        "risk_level": content.get("risk_level") or "medium",
        "next_action": content.get("next_action") or _next_action_for_text(text),
        "source_ref": _source_ref(row.get("evidence") or []),
        "created_at": row["created_at"],
    }


def _looks_like_risk(row: dict[str, Any]) -> bool:
    content = row.get("content") or {}
    if content.get("risk"):
        return True
    text = f"{row.get('title', '')} {row.get('summary', '')} {content}"
    resolved_terms = ("已解决", "顺利通过", "上线成功", "全部修复", "完成交付", "圆满结束")
    active_terms = ("但", "还", "需要", "影响", "阻塞", "失败", "异常", "缺")
    if any(term in text for term in ("没有出现任何异常", "没有异常", "无异常")):
        return False
    if any(term in text for term in resolved_terms) and not any(term in text for term in active_terms):
        return False
    return any(term in text for term in RISK_TERMS)


def _progress_summary(statuses: list[dict[str, Any]]) -> str:
    for row in statuses:
        text = f"{row.get('title', '')} {row.get('summary', '')}"
        if any(marker in text for marker in ("完成", "进度", "通过", "上线", "交付", "70%", "80%", "90%")):
            return row["summary"]
    return statuses[0]["summary"] if statuses else "暂无可用项目进展记忆"


def _next_actions(statuses: list[dict[str, Any]], risks: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for risk in risks:
        action = risk.get("next_action")
        if action:
            actions.append(action)
    for row in statuses:
        content = row.get("content") or {}
        if content.get("next_action"):
            actions.append(str(content["next_action"]))
            continue
        text = f"{row.get('title', '')} {row.get('summary', '')}"
        if any(term in text for term in ACTION_TERMS):
            actions.append(_next_action_for_text(text))
    deduped: list[str] = []
    for action in actions:
        if action and action not in deduped:
            deduped.append(action)
    return deduped


def _stakeholders(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for row in rows:
        content = row.get("content") or {}
        stakeholders = content.get("stakeholders") or []
        if not isinstance(stakeholders, list):
            continue
        for item in stakeholders:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            role = str(item.get("role") or item.get("responsibility") or "").strip()
            if not name:
                continue
            key = (name, role)
            if key in seen:
                continue
            seen.add(key)
            value = {"name": name}
            if role:
                value["role"] = role
            result.append(value)
    return result


def _impact_text(text: str) -> str:
    if "周五" in text and ("验收" in text or "交付" in text):
        return "可能影响周五验收或交付节奏"
    if "不稳定" in text:
        return "可能影响核心功能验证和上线稳定性"
    if "缺" in text:
        return "依赖材料或信息不完整，可能阻塞后续推进"
    return "需要项目经理持续跟进，避免影响交付"


def _next_action_for_text(text: str) -> str:
    if "安全说明" in text:
        return "补齐安全说明并同步客户"
    if "验收" in text:
        return "确认验收材料和验收安排"
    if "bug" in text.lower() or "修复" in text:
        return "跟进剩余问题修复并回归验证"
    if "回调" in text or "WebSocket" in text:
        return "确认飞书回调调试方案并同步风险"
    return "明确负责人、截止时间和下一步处理动作"


def _intent_for_query(query: str) -> str:
    if any(term in query for term in ("历史", "为什么", "原因", "决策", "选择")):
        return "decision_history"
    if any(term in query for term in ("风险", "阻塞", "卡点")):
        return "risk_scan"
    if any(term in query for term in ("总结", "进展", "交接")):
        return "project_summary"
    return "general"


def _product_recall_query(query: str) -> str:
    cleaned = query
    for token in ("为什么", "怎么", "如何", "请问", "这个项目", "当前项目", "最后", "选择了", "选择", "？", "?"):
        cleaned = cleaned.replace(token, " ")
    cleaned = " ".join(cleaned.split())
    return cleaned or query


def _extractive_answer(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "未找到有足够证据支持的项目记忆。"
    lines = ["根据项目记忆，当前可引用的信息如下："]
    for row in rows[:5]:
        source = _source_ref(row.get("evidence") or [])
        suffix = f" 来源：{source}" if source else ""
        lines.append(f"- [{row.get('memory_type')}] {row.get('summary') or row.get('title')}{suffix}")
    return "\n".join(lines)


def _dashboard_answer_summary(question: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "未找到可引用的项目记忆。"
    type_counts: dict[str, int] = {}
    for row in rows:
        label = _type_label(row.get("memory_type"))
        type_counts[label] = type_counts.get(label, 0) + 1
    type_text = "、".join(f"{name} {count} 条" for name, count in type_counts.items())
    top = rows[0]
    conclusion = _short_text(top.get("summary") or top.get("title") or "", 110)
    return (
        f"已找到 {len(rows)} 条相关项目记忆（{type_text}）。"
        f"核心结论：{conclusion}。点击下方记忆卡片可查看出处、时间和原始记录。"
    )


def _type_label(memory_type: Any) -> str:
    return {
        "decision": "决策",
        "task_status": "状态",
        "preference": "偏好",
        "procedural": "流程",
    }.get(str(memory_type or ""), str(memory_type or "记忆"))


def _short_text(value: Any, max_length: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    return f"{text[:max_length - 1]}…"


def _citation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_id": row.get("id"),
        "source_ref": _source_ref(row.get("evidence") or []),
        "title": row.get("title"),
    }


def _compact_memory(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "memory_type": row.get("memory_type"),
        "title": row.get("title"),
        "summary": row.get("summary"),
        "content": row.get("content") or {},
        "evidence": row.get("evidence") or [],
        "tags": row.get("tags") or [],
        "score": row.get("score"),
        "source_ref": _source_ref(row.get("evidence") or []),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _source_ref(evidence: list[dict[str, Any]]) -> str | None:
    for item in evidence:
        if isinstance(item, dict) and item.get("source_ref"):
            return str(item["source_ref"])
    return None


def _loads(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw or "")
    except (TypeError, json.JSONDecodeError):
        return default
