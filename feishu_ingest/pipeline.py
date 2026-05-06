"""Ingest pipeline — orchestrates adapter → extract → dedupe → engine.write."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from memory_engine import MemoryEngine

from feishu_ingest.adapters.base import FeishuSourceAdapter
from feishu_ingest.extractors import extract_candidates
from feishu_ingest.models import FeishuEvent
from feishu_ingest.scope import infer_project_id, infer_scope, infer_task_id

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    events_processed: int = 0
    events_written: int = 0
    events_skipped_dup: int = 0
    events_skipped_no_candidate: int = 0
    memory_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_ingest(
    adapter: FeishuSourceAdapter,
    engine: MemoryEngine,
    *,
    dedupe_by_hash: bool = True,
) -> PipelineResult:
    """Full ingest pipeline: stream → extract → dedupe → write.

    Error policy (design doc §11):
      - malformed event → skip + record
      - engine write failure → abort this event, continue next
      - duplicate hash → skip silently
      - sensitive content → masked by engine's scan_and_mask (inside write())
    """
    result = PipelineResult()
    seen_hashes: set[str] = set()

    for event in adapter.stream_events():
        result.events_processed += 1

        # Persistent dedup: query events table before processing
        existing = _check_existing(engine, event)
        if existing == "skip":
            result.events_skipped_dup += 1
            logger.debug("Skipped duplicate msg %s", event.source_ref)
            continue
        if existing == "update":
            # Content changed for same source_ref — write new event+memory
            # (old memories remain active; supersession handled by conflict detection on write)
            logger.info("Content changed for %s, writing update", event.source_ref)

        # Adapter-level dedupe by content_hash (within this run)
        if dedupe_by_hash:
            ch = event._content_hash
            if ch in seen_hashes:
                result.events_skipped_dup += 1
                continue
            seen_hashes.add(ch)

        # Resolve scope/project/task
        scope = infer_scope(event)
        project_id = infer_project_id(event)
        task_id = infer_task_id(event)

        # Extract candidates with the final scope metadata that the engine persists.
        candidates = extract_candidates(event, scope=scope, project_id=project_id, task_id=task_id)
        if not candidates:
            result.events_skipped_no_candidate += 1
            continue

        # Build enriched SourceEvent with inferred scope
        source_event = _build_source_event(event, scope)

        try:
            write_result = engine.write(
                event=source_event,
                memory_candidates=candidates,
                project_id=project_id,
                task_id=task_id,
                user_id=event.user_id,
            )
            result.events_written += 1
            result.memory_ids.extend(write_result.get("memory_ids", []))
        except Exception as exc:
            msg = f"engine.write failed for {event.source_ref}: {exc}"
            logger.error(msg)
            result.errors.append(msg)

    return result


def _event_already_ingested(engine: MemoryEngine, event: FeishuEvent) -> bool:
    """Return True only for the same source identity and same content fingerprint."""
    source_type_map = {
        "message": "message",
        "doc": "doc",
        "wiki": "doc",
    }
    engine_source_type = source_type_map.get(event.source_type, event.source_type)
    row = engine.conn.execute(
        """
        SELECT id
        FROM events
        WHERE source_type = ? AND source_ref = ? AND content_hash = ?
        LIMIT 1
        """,
        (engine_source_type, event.source_ref, event._content_hash),
    ).fetchone()
    return row is not None


def _check_existing(engine: MemoryEngine, event: FeishuEvent) -> str:
    """Check whether an event was already processed.

    Returns:
        'skip'    — exact duplicate (same source_ref + content_hash)
        'update'  — same source_ref but content changed
        'new'     — never seen before
    """
    source_type_map = {
        "message": "message",
        "doc": "doc",
        "wiki": "doc",
    }
    engine_source_type = source_type_map.get(event.source_type, event.source_type)
    row = engine.conn.execute(
        "SELECT content_hash FROM events WHERE source_ref = ? AND source_type = ? LIMIT 1",
        (event.source_ref, engine_source_type),
    ).fetchone()
    if row is None:
        return "new"
    if row[0] == event._content_hash:
        return "skip"
    return "update"


def _build_source_event(event: FeishuEvent, scope: "Scope") -> "SourceEvent":
    """Build a SourceEvent with inferred scope overriding the event's own scope."""
    from memory_engine.models import SourceEvent, SourceType

    source_type_map = {
        "message": SourceType.MESSAGE.value,
        "doc": SourceType.DOC.value,
        "wiki": SourceType.DOC.value,
    }
    engine_source_type = source_type_map.get(event.source_type, event.source_type)

    return SourceEvent(
        source_type=engine_source_type,
        source_ref=event.source_ref,
        actors=event.actors,
        timestamp=event.timestamp,
        content=event.content,
        scope=scope.value,
        payload=event._sanitised_payload(),
    )
