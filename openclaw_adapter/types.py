"""Data types for openclaw_adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class OpenClawContext:
    """Agent execution context snapshot — input for recall."""

    user_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    latest_message: str = ""
    current_task: str | None = None
    open_files: tuple[str, ...] = ()
    already_recalled_ids: tuple[str, ...] = ()
    session_id: str | None = None


@dataclass(frozen=True)
class OpenClawEvent:
    """Agent execution result event — input for write."""

    user_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    user_message: str = ""
    tool_name: str | None = None
    tool_output: str | None = None
    assistant_summary: str | None = None
    timestamp: str = ""
    session_id: str | None = None
    recalled_memory_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class WriteDecision:
    """Result of write-worthiness classification."""

    action: Literal["write", "skip", "reject"]
    reason: str
    memory_type: str | None = None
    confidence: float = 0.0
    importance: float = 0.0


@dataclass(frozen=True)
class WriteResult:
    """Final result of a write operation."""

    action: str
    written: bool
    memory_ids: list[int]
    skip_reason: str | None = None
    conflict_detected: bool = False
    workflow_outcome_memory_ids: list[int] = field(default_factory=list)
