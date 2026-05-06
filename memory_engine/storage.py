from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import EventEntry, MemoryCandidate, SourceEvent, utc_now

DEFAULT_TASK_STATUS_VALIDITY_DAYS = 14


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            actors_json TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            content TEXT NOT NULL,
            scope TEXT NOT NULL,
            project_id TEXT,
            task_id TEXT,
            user_id TEXT,
            payload_json TEXT NOT NULL,
            content_hash TEXT,
            source_version TEXT,
            validated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT NOT NULL UNIQUE,
            memory_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            content_json TEXT NOT NULL,
            scope TEXT NOT NULL,
            project_id TEXT,
            task_id TEXT,
            user_id TEXT,
            importance REAL NOT NULL,
            confidence REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            version INTEGER NOT NULL,
            replaces_memory_id INTEGER,
            superseded_by TEXT,
            change_reason TEXT,
            source_event_id INTEGER NOT NULL,
            evidence_json TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(source_event_id) REFERENCES events(id)
        );

        CREATE TABLE IF NOT EXISTS recall_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER,
            query TEXT NOT NULL,
            raw_score REAL NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0,
            rank_index INTEGER,
            was_returned INTEGER NOT NULL,
            project_id TEXT,
            task_id TEXT,
            user_id TEXT,
            recalled_at TEXT NOT NULL,
            FOREIGN KEY(memory_id) REFERENCES memories(id)
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            actor TEXT NOT NULL,
            detail TEXT,
            sensitive_detections INTEGER NOT NULL DEFAULT 0,
            audited_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS event_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_event_id INTEGER NOT NULL,
            event_time TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            subject TEXT NOT NULL,
            relation TEXT NOT NULL,
            object TEXT NOT NULL,
            qualifiers_json TEXT NOT NULL,
            project_id TEXT,
            task_id TEXT,
            user_id TEXT,
            confidence REAL NOT NULL DEFAULT 0.6,
            created_at TEXT NOT NULL,
            FOREIGN KEY(source_event_id) REFERENCES events(id)
        );

        CREATE TABLE IF NOT EXISTS memory_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_memory_id INTEGER NOT NULL,
            reviewer_name TEXT NOT NULL,
            vote TEXT NOT NULL,
            score REAL,
            reason TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(candidate_memory_id) REFERENCES memories(id)
        );
        """
    )
    _ensure_column(conn, "memories", "memory_layer", "TEXT NOT NULL DEFAULT 'factual'")
    _ensure_column(conn, "memories", "promotion_candidate", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "memories", "promoted_from_memory_id", "INTEGER")
    _ensure_column(conn, "memories", "logical_layer", "TEXT NOT NULL DEFAULT 'L1'")
    _ensure_column(conn, "memories", "last_reviewed_at", "TEXT")
    _ensure_column(conn, "memories", "token_list", "TEXT")
    _ensure_column(conn, "memories", "doc_len", "INTEGER")
    _ensure_column(conn, "memories", "valid_from", "TEXT")
    _ensure_column(conn, "memories", "valid_until", "TEXT")
    _ensure_column(conn, "memory_votes", "assembly_id", "TEXT")
    _ensure_column(conn, "memory_votes", "ballot_kind", "TEXT")
    _ensure_column(conn, "memory_votes", "reviewer_role", "TEXT")
    _ensure_column(conn, "memory_votes", "evidence_refs_json", "TEXT")

    # Performance indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_project_id ON memories(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_user_id ON memories(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_source_ref ON events(source_ref)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_entries_source_event ON event_entries(source_event_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_entries_project ON event_entries(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_entries_relation ON event_entries(relation)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_votes_candidate ON memory_votes(candidate_memory_id)")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_sql: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")


def _parse_positive_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _task_status_valid_until(candidate: MemoryCandidate, now: str) -> str | None:
    if candidate.memory_type != "task_status" or candidate.content.get("valid_until"):
        return candidate.content.get("valid_until")

    ttl_hours = _parse_positive_float(candidate.content.get("ttl_hours"))
    if ttl_hours is None:
        validity_days = _parse_positive_float(candidate.content.get("validity_days"))
        ttl_hours = (validity_days if validity_days is not None else DEFAULT_TASK_STATUS_VALIDITY_DAYS) * 24

    base = datetime.fromisoformat(now).astimezone(timezone.utc)
    return (base + timedelta(hours=ttl_hours)).isoformat()


def insert_event(
    conn: sqlite3.Connection,
    event: SourceEvent,
    project_id: str | None,
    task_id: str | None,
    user_id: str | None,
) -> int:
    content_hash = event.payload.get("content_hash") or hashlib.sha256(event.content.encode("utf-8")).hexdigest()
    source_version = event.payload.get("source_version")
    cursor = conn.execute(
        """
        INSERT INTO events (
            source_type, source_ref, actors_json, timestamp, content, scope,
            project_id, task_id, user_id, payload_json, content_hash, source_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.source_type, event.source_ref,
            json.dumps(event.actors, ensure_ascii=True),
            event.timestamp, event.content, event.scope,
            project_id, task_id, user_id,
            json.dumps(event.payload, ensure_ascii=True),
            content_hash, source_version,
        ),
    )
    return int(cursor.lastrowid)


def insert_memory(
    conn: sqlite3.Connection,
    candidate: MemoryCandidate,
    event_id: int,
    project_id: str | None,
    task_id: str | None,
    user_id: str | None,
    version: int = 1,
    forced_uuid: str | None = None,
    memory_layer: str = "factual",
) -> int:
    now = utc_now()
    mem_uuid = forced_uuid or str(uuid.uuid4())
    scope = candidate.content.get("scope", "task")
    valid_from = candidate.content.get("valid_from")
    valid_until = _task_status_valid_until(candidate, now)

    # Pre-compute token_list for recall performance
    from .ranking import _tokenize
    text = " ".join([candidate.title, candidate.summary])
    tokens = _tokenize(text)
    token_list_json = json.dumps(tokens, ensure_ascii=True)
    doc_len = len(tokens)

    cursor = conn.execute(
        """
        INSERT INTO memories (
            uuid, memory_type, title, summary, content_json, scope, project_id, task_id,
            user_id, importance, confidence, status, version, replaces_memory_id,
            superseded_by, change_reason, source_event_id, evidence_json, tags_json,
            created_at, updated_at,
            memory_layer, promotion_candidate, promoted_from_memory_id,
            token_list, doc_len, valid_from, valid_until
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL, ?, ?, ?, ?, ?, ?,
                  ?, 0, NULL, ?, ?, ?, ?)
        """,
        (
            mem_uuid, candidate.memory_type, candidate.title, candidate.summary,
            json.dumps(candidate.content, ensure_ascii=True), scope,
            project_id, task_id, user_id,
            max(0.0, min(candidate.importance, 1.0)),
            max(0.0, min(candidate.confidence, 1.0)),
            version, candidate.replaces_memory_id, candidate.change_reason,
            event_id,
            json.dumps(candidate.evidence, ensure_ascii=True),
            json.dumps(candidate.tags, ensure_ascii=True),
            now, now,
            memory_layer,
            token_list_json, doc_len,
            str(valid_from) if valid_from else None,
            str(valid_until) if valid_until else None,
        ),
    )
    return int(cursor.lastrowid)


def insert_event_entry(conn: sqlite3.Connection, entry: EventEntry) -> int:
    cursor = conn.execute(
        """
        INSERT INTO event_entries (
            source_event_id, event_time, entry_type, subject, relation, object,
            qualifiers_json, project_id, task_id, user_id, confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.source_event_id,
            entry.event_time,
            entry.entry_type,
            entry.subject,
            entry.relation,
            entry.object,
            json.dumps(entry.qualifiers, ensure_ascii=True),
            entry.project_id,
            entry.task_id,
            entry.user_id,
            max(0.0, min(entry.confidence, 1.0)),
            utc_now(),
        ),
    )
    return int(cursor.lastrowid)
