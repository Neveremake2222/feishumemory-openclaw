"""DirectEngineClient — in-process client wrapping memory_engine.MemoryEngine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from memory_engine.engine import MemoryEngine
from memory_engine.governance import governance_ballot_provider_from_env
from memory_engine.models import MemoryCandidate, RecallRequest, SourceEvent


class DirectEngineClient:
    """In-process client that directly imports and calls memory_engine.

    Used when the adapter and engine run in the same process
    (e.g. FastAPI server on the cloud machine).
    """

    def __init__(self, db_path: str | Path = "memory_engine.sqlite3") -> None:
        self._engine = MemoryEngine(db_path, governance_ballot_provider=governance_ballot_provider_from_env())

    def recall(
        self,
        query: str,
        user_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        limit: int = 5,
        memory_layer: str | None = None,
    ) -> list[dict[str, Any]]:
        request = RecallRequest(
            query=query,
            user_id=user_id,
            project_id=project_id,
            task_id=task_id,
            memory_layer=memory_layer,
        )
        return self._engine.recall(request, limit=limit)

    def write(
        self,
        event: SourceEvent,
        candidates: list[MemoryCandidate],
        project_id: str | None = None,
        task_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return self._engine.write(
            event,
            candidates,
            project_id=project_id,
            task_id=task_id,
            user_id=user_id,
        )

    def record_workflow_skill_outcome(
        self,
        skill_id: int,
        *,
        outcome: str,
        summary: str,
        evidence: list[dict[str, Any]] | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return self._engine.record_workflow_skill_outcome(
            skill_id,
            outcome=outcome,
            summary=summary,
            evidence=evidence,
            project_id=project_id,
            task_id=task_id,
            user_id=user_id,
        )

    def close(self) -> None:
        self._engine.close()

    def __enter__(self) -> DirectEngineClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
