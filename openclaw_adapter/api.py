"""FastAPI server exposing memory-engine recall/write to OpenClaw.

Run:
    uvicorn openclaw_adapter.api:app --host 0.0.0.0 --port 8000

Endpoints:
    POST /recall  — recall memories, returns Markdown injection snippet
    POST /write   — write an event to memory-engine
    GET  /health  — health check
    GET  /projects — list product-facing projects for the demo dashboard
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from memory_engine.env import load_project_env
from memory_engine.product_api import ProductMemoryView, business_value_metrics
from openclaw_adapter.engine_client import DirectEngineClient
from openclaw_adapter.project_resolver import resolve_project_id
from openclaw_adapter.recall_hook import recall
from openclaw_adapter.types import OpenClawContext, OpenClawEvent
from openclaw_adapter.write_hook import write

logger = logging.getLogger(__name__)

load_project_env()
DB_PATH = os.environ.get("MEMORY_ENGINE_DB", "memory_engine.sqlite3")

app = FastAPI(title="openclaw-adapter", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared engine client (lives for the process lifetime)
_client: DirectEngineClient | None = None
_client_lock = threading.Lock()


def _get_client() -> DirectEngineClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = DirectEngineClient(DB_PATH)
    return _client


@app.on_event("shutdown")
def _shutdown() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


# -- Pydantic request/response models ----------------------------------------

class RecallRequest(BaseModel):
    query: str = ""
    user_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    limit: int = 5
    memory_layer: str | None = None
    # full context fields (optional — used if provided)
    latest_message: str = ""
    current_task: str | None = None
    open_files: list[str] = []
    already_recalled_ids: list[str] = []
    session_id: str | None = None


class MemoryItem(BaseModel):
    """Single structured memory result for OpenClaw consumption."""
    id: int
    memory_type: str
    title: str
    summary: str
    confidence: float
    logical_layer: str | None = None
    importance: float | None = None
    evidence: list[dict[str, Any]] = []
    tags: list[str] = []
    created_at: str | None = None
    source_ref: str | None = None
    scope: str | None = None
    project_id: str | None = None


class RecallResponse(BaseModel):
    injection_md: str
    count: int
    memory_ids: list[int]
    query: str
    tier_counts: dict[str, int]
    memory_type_counts: dict[str, int]
    memories: list[MemoryItem] = []


class WriteRequestBody(BaseModel):
    user_message: str = ""
    user_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    tool_name: str | None = None
    tool_output: str | None = None
    assistant_summary: str | None = None
    timestamp: str = ""
    session_id: str | None = None
    recalled_memory_ids: list[str] = []


class WriteResponse(BaseModel):
    action: str
    written: bool
    memory_ids: list[int]
    skip_reason: str | None = None
    conflict_detected: bool = False
    workflow_outcome_memory_ids: list[int] = []


class HealthResponse(BaseModel):
    status: str
    db_path: str


class AskProjectRequest(BaseModel):
    question: str
    limit: int = 5


class DraftFollowupRequest(BaseModel):
    context: str = ""


# -- Endpoints ----------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", db_path=DB_PATH)


@app.get("/projects")
def list_projects_endpoint() -> list[dict[str, Any]]:
    return ProductMemoryView(DB_PATH).list_projects()


@app.get("/projects/{project_id}/overview")
def project_overview_endpoint(project_id: str) -> dict[str, Any]:
    return ProductMemoryView(DB_PATH).get_project_overview(project_id)


@app.get("/projects/{project_id}/timeline")
def project_timeline_endpoint(project_id: str, limit: int = 50) -> list[dict[str, Any]]:
    return ProductMemoryView(DB_PATH).get_project_timeline(project_id, limit=limit)


@app.post("/projects/{project_id}/ask")
def project_ask_endpoint(project_id: str, req: AskProjectRequest) -> dict[str, Any]:
    return ProductMemoryView(DB_PATH).ask_question(project_id, req.question, limit=req.limit)


@app.post("/projects/{project_id}/draft-followup")
def project_draft_followup_endpoint(project_id: str, req: DraftFollowupRequest) -> dict[str, Any]:
    return ProductMemoryView(DB_PATH).draft_followup(project_id, context=req.context)


@app.get("/benchmarks/business-value")
def business_value_endpoint() -> dict[str, Any]:
    return business_value_metrics()


@app.post("/recall", response_model=RecallResponse)
def recall_endpoint(req: RecallRequest) -> RecallResponse:
    # Resolve project_id if not explicitly provided
    resolved_project_id = req.project_id
    if not resolved_project_id:
        resolved_project_id = resolve_project_id(
            workspace_id=req.session_id,
            cwd=os.getcwd(),
        )
    ctx = OpenClawContext(
        user_id=req.user_id,
        project_id=resolved_project_id,
        task_id=req.task_id,
        latest_message=req.latest_message or req.query,
        current_task=req.current_task,
        open_files=tuple(req.open_files),
        already_recalled_ids=tuple(req.already_recalled_ids),
        session_id=req.session_id,
    )
    client = _get_client()
    output = recall(ctx, limit=req.limit, client=client, memory_layer=req.memory_layer)
    tier_counts: dict[str, int] = {"tier1": 0, "tier2": 0, "tier3": 0}
    type_counts: dict[str, int] = {}
    memory_items: list[MemoryItem] = []
    for r in output.results:
        tier = r.get("confidence_tier", 3)
        tier_key = f"tier{tier}"
        tier_counts[tier_key] = tier_counts.get(tier_key, 0) + 1
        mt = r.get("memory_type", "unknown")
        type_counts[mt] = type_counts.get(mt, 0) + 1
        # Extract evidence source_ref for top-level field
        evidence = r.get("evidence", [])
        source_ref = None
        if evidence and isinstance(evidence, list):
            source_ref = evidence[0].get("source_ref") if evidence[0] else None
        memory_items.append(MemoryItem(
            id=r.get("id", 0),
            memory_type=mt,
            title=r.get("title", ""),
            summary=r.get("summary", ""),
            confidence=r.get("confidence", 0.0),
            logical_layer=r.get("logical_layer"),
            importance=r.get("importance"),
            evidence=evidence if isinstance(evidence, list) else [],
            tags=r.get("tags", []),
            created_at=r.get("created_at"),
            source_ref=source_ref,
            scope=r.get("scope"),
            project_id=r.get("project_id"),
        ))
    return RecallResponse(
        injection_md=output.injection_md,
        count=len(output.results),
        memory_ids=[r["id"] for r in output.results],
        query=output.query,
        tier_counts=tier_counts,
        memory_type_counts=type_counts,
        memories=memory_items,
    )


@app.post("/write", response_model=WriteResponse)
def write_endpoint(req: WriteRequestBody) -> WriteResponse:
    # Resolve project_id if not explicitly provided
    resolved_project_id = req.project_id
    if not resolved_project_id:
        resolved_project_id = resolve_project_id(
            workspace_id=req.session_id,
            cwd=os.getcwd(),
        )
    event = OpenClawEvent(
        user_id=req.user_id,
        project_id=resolved_project_id,
        task_id=req.task_id,
        user_message=req.user_message,
        tool_name=req.tool_name,
        tool_output=req.tool_output,
        assistant_summary=req.assistant_summary,
        timestamp=req.timestamp,
        session_id=req.session_id,
        recalled_memory_ids=tuple(req.recalled_memory_ids),
    )
    client = _get_client()
    result = write(event, client=client)
    return WriteResponse(
        action=result.action,
        written=result.written,
        memory_ids=result.memory_ids,
        skip_reason=result.skip_reason,
        conflict_detected=result.conflict_detected,
        workflow_outcome_memory_ids=result.workflow_outcome_memory_ids,
    )
