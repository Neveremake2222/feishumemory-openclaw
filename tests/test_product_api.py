from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from benchmarks.cases.track_m import TRACK_M_CASES
from memory_engine import MemoryCandidate, MemoryEngine, SourceEvent
from memory_engine.product_api import ProductMemoryView, business_value_metrics


@pytest.fixture(autouse=True)
def _disable_summary_subagent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMMARY_SUBAGENT_ENABLED", "false")


def _seed_project(db_path: Path) -> None:
    with MemoryEngine(db_path) as engine:
        event = SourceEvent(
            source_type="message",
            source_ref="test://product/project",
            actors=["pm_lixiang"],
            timestamp="2026-05-06T10:00:00+08:00",
            content="seed product project",
            scope="project",
            payload={"chat_title": "客户交付 Alpha 项目群"},
        )
        engine.write(
            event=event,
            project_id="proj_product_alpha",
            user_id="pm_lixiang",
            memory_candidates=[
                MemoryCandidate(
                    memory_type="decision",
                    title="采用方案 B：WebSocket 长连接",
                    summary="最终决定采用方案 B：WebSocket 长连接。",
                    content={
                        "scope": "project",
                        "reason": "不需要公网 IP 和域名",
                        "current": True,
                        "stakeholders": [{"name": "李想", "role": "项目经理"}],
                    },
                    importance=0.9,
                    confidence=0.95,
                    evidence=[{"source_ref": "test://decision/websocket"}],
                    tags=["current", "stakeholder"],
                ),
                MemoryCandidate(
                    memory_type="task_status",
                    title="验收材料缺少安全说明",
                    summary="验收材料还缺最后一版安全说明，预计会影响周五验收。",
                    content={
                        "scope": "project",
                        "risk": "missing_security_note",
                        "risk_level": "high",
                        "impact": "影响周五验收",
                        "next_action": "补齐安全说明并同步客户",
                        "deadline": "周五",
                    },
                    importance=0.85,
                    confidence=0.9,
                    evidence=[{"source_ref": "test://risk/security-note"}],
                    tags=["risk", "next_action"],
                ),
            ],
        )


def _runtime_db() -> Path:
    path = Path("tests_runtime") / "product_api" / str(uuid.uuid4()) / "product.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_product_memory_view_builds_project_overview() -> None:
    db_path = _runtime_db()
    try:
        _seed_project(db_path)

        view = ProductMemoryView(db_path)
        overview = view.get_project_overview("proj_product_alpha")

        assert overview["name"] == "客户交付 Alpha 项目群"
        assert overview["key_decisions"][0]["title"] == "采用方案 B：WebSocket 长连接"
        assert overview["risks"][0]["risk_level"] == "high"
        assert overview["next_actions"][0] == "补齐安全说明并同步客户"
        assert overview["stakeholders"][0]["name"] == "李想"
    finally:
        shutil.rmtree(db_path.parent, ignore_errors=True)


def test_product_memory_view_quick_actions_return_memory_cards() -> None:
    db_path = _runtime_db()
    try:
        _seed_project(db_path)

        view = ProductMemoryView(db_path)
        summary = view.ask_question("proj_product_alpha", "总结当前项目进展 风险 下一步")
        decisions = view.ask_question("proj_product_alpha", "为什么选择当前方案 历史决策 原因")
        risks = view.ask_question("proj_product_alpha", "当前项目有哪些风险 阻塞 卡点")
        draft = view.draft_followup("proj_product_alpha")

        assert summary["memories"]
        assert decisions["memories"]
        assert all(item["memory_type"] == "decision" for item in decisions["memories"])
        assert risks["memories"]
        assert all(item["memory_type"] == "task_status" for item in risks["memories"])
        assert draft["draft"]
        assert draft["memories"]
    finally:
        shutil.rmtree(db_path.parent, ignore_errors=True)


def test_product_memory_view_uses_summary_subagent(monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _runtime_db()
    try:
        _seed_project(db_path)

        class FakeSummarySubAgent:
            def rewrite(self, **kwargs: object) -> str:
                assert kwargs["memories"]
                return "LLM 子 agent 改写后的项目摘要"

        import memory_engine.product_api as product_api

        monkeypatch.setattr(
            product_api.SummarySubAgent,
            "from_env",
            staticmethod(lambda: FakeSummarySubAgent()),
        )
        view = ProductMemoryView(db_path)
        response = view.ask_question("proj_product_alpha", "总结当前项目进展 风险 下一步")

        assert response["summary"] == "LLM 子 agent 改写后的项目摘要"
        assert response["summary_source"] == "llm_subagent"
    finally:
        shutil.rmtree(db_path.parent, ignore_errors=True)


def test_business_value_metrics_runs_track_m() -> None:
    metrics = business_value_metrics()

    assert metrics["track"] == "M"
    assert metrics["case_count"] == len(TRACK_M_CASES)
    assert metrics["passed"] == len(TRACK_M_CASES)
    assert metrics["failed"] == 0
    assert not [item for item in metrics["cases"] if not item["passed"]]
    comparison = {item["mode"]: item for item in metrics["baseline_comparison"]}
    assert comparison["baseline_no_memory"]["passed"] == 0
    assert 0 < comparison["recent_context_only"]["passed"] < comparison["memory_enabled"]["passed"]
    assert comparison["memory_enabled"]["passed"] == metrics["passed"]
    assert comparison["memory_enabled"]["passed_delta_vs_no_memory"] == metrics["passed"]


def test_product_api_routes_use_configured_db(monkeypatch) -> None:
    db_path = _runtime_db()
    try:
        _seed_project(db_path)

        import openclaw_adapter.api as api

        monkeypatch.setattr(api, "DB_PATH", str(db_path))
        client = TestClient(api.app)

        projects = client.get("/projects")
        assert projects.status_code == 200
        assert projects.json()[0]["project_id"] == "proj_product_alpha"

        overview = client.get("/projects/proj_product_alpha/overview")
        assert overview.status_code == 200
        assert overview.json()["risks"][0]["next_action"] == "补齐安全说明并同步客户"

        business = client.get("/benchmarks/business-value")
        assert business.status_code == 200
        assert business.json()["case_count"] == len(TRACK_M_CASES)
        assert business.json()["passed"] == len(TRACK_M_CASES)
        assert business.json()["baseline_comparison"][-1]["mode"] == "memory_enabled"
    finally:
        shutil.rmtree(db_path.parent, ignore_errors=True)
