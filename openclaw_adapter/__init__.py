"""openclaw_adapter — bridge between OpenClaw and memory-engine.

Architecture (cloud-hosted OpenClaw on Feishu Miaoda):
    OpenClaw tool call → FastAPI server (api.py)
        → DirectEngineClient → memory_engine SQLite

Files:
    types.py        — data classes (OpenClawContext, OpenClawEvent, WriteDecision, WriteResult)
    engine_client.py — DirectEngineClient (import memory_engine directly)
    recall_hook.py  — recall() logic: query build → engine recall → injection format
    write_hook.py   — write() logic: WriteFilter → dedupe → engine write
    dedupe.py       — AdapterDedupe (session + 10min TTL dedup)
    injection.py    — InjectionFormatter (recall result → Markdown snippet)
    api.py          — FastAPI server exposing /recall and /write endpoints
    cli.py          — CLI entry point: python -m openclaw_adapter.cli <recall|write>

Usage:
    # Start API server
    uvicorn openclaw_adapter.api:app --host 0.0.0.0 --port 8000

    # Or call directly
    from openclaw_adapter.recall_hook import recall
    from openclaw_adapter.types import OpenClawContext
    snippet = recall(OpenClawContext(...))
"""

from openclaw_adapter.types import (
    OpenClawContext,
    OpenClawEvent,
    WriteDecision,
    WriteResult,
)
from openclaw_adapter.engine_client import DirectEngineClient
from openclaw_adapter.recall_hook import RecallOutput, recall
from openclaw_adapter.write_hook import write

__all__ = [
    "OpenClawContext",
    "OpenClawEvent",
    "WriteDecision",
    "WriteResult",
    "DirectEngineClient",
    "RecallOutput",
    "recall",
    "write",
]
