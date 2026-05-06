"""Backfill project_id for historical records.

Scans events and memories with project_id=NULL, checks if their source
has a chat_id/doc_id that maps to a registered project, and updates them.

Usage:
    # Dry run (show what would change, don't modify)
    python scripts/backfill_project_ids.py --dry-run

    # Apply changes
    python scripts/backfill_project_ids.py

    # Specify DB path
    python scripts/backfill_project_ids.py --db-path /path/to/memory_engine.sqlite3
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from feishu_ingest.project_registry import ProjectRegistry

DB_PATH = os.environ.get("MEMORY_ENGINE_DB", "memory_engine.sqlite3")


def backfill(db_path: str = DB_PATH, dry_run: bool = True) -> None:
    import sqlite3

    registry_path = os.environ.get("PROJECT_REGISTRY_PATH", "config/project_registry.json")
    if not os.path.exists(registry_path):
        print(f"ERROR: No project registry at {registry_path}")
        sys.exit(1)

    registry = ProjectRegistry.load(registry_path)
    projects = registry.get_all_projects()
    print(f"Loaded registry: {len(projects)} projects")
    for p in projects:
        chats = ", ".join(p.chat_ids) if p.chat_ids else "(none)"
        print(f"  {p.project_id}: chat_ids=[{chats}]")
    print()

    conn = sqlite3.connect(db_path)
    mode = "DRY RUN" if dry_run else "APPLY"
    print(f"Mode: {mode}")
    print(f"DB: {db_path}")
    print()

    # -- Events with project_id NULL -------------------------------------------
    events_null = conn.execute(
        "SELECT id, source_type, source_ref, content, payload_json FROM events WHERE project_id IS NULL"
    ).fetchall()
    print(f"Events with project_id=NULL: {len(events_null)}")

    event_updates = []
    for eid, source_type, source_ref, content, payload_json in events_null:
        project_id = _resolve_from_payload(payload_json, registry)
        strategy = STRATEGY_PAYLOAD
        if not project_id and source_ref.startswith("openclaw:"):
            # All OpenClaw events in single-project setup belong to the first project
            if len(projects) == 1:
                project_id = projects[0].project_id
                strategy = STRATEGY_OPENCLAW
        if not project_id:
            project_id = _resolve_from_content(content, registry)
            strategy = STRATEGY_CONTENT
        if not project_id:
            project_id = _resolve_from_source_ref(source_ref, source_type, registry)
            strategy = STRATEGY_SOURCE_REF
        if project_id:
            event_updates.append((project_id, eid, strategy))

    print(f"Events that can be updated: {len(event_updates)}")
    for pid, eid, strat in event_updates:
        print(f"  event {eid} -> {pid} (strategy: {strat})")

    # -- Apply event updates FIRST so memories can look up updated events ------
    if not dry_run and event_updates:
        print()
        print("Applying event updates...")
        for pid, eid, _ in event_updates:
            conn.execute("UPDATE events SET project_id = ? WHERE id = ?", (pid, eid))
        conn.commit()
        print(f"Updated {len(event_updates)} events")

    # -- Memories with project_id NULL (after events are updated) ---------------
    memories_null = conn.execute(
        "SELECT id, title, source_event_id, content_json FROM memories WHERE project_id IS NULL"
    ).fetchall()
    print(f"\nMemories with project_id=NULL: {len(memories_null)}")

    memory_updates = []
    for mid, title, source_event_id, content_json in memories_null:
        project_id = None

        # Try to get project_id from the linked event (now updated)
        if source_event_id:
            row = conn.execute(
                "SELECT project_id FROM events WHERE id = ?", (source_event_id,)
            ).fetchone()
            if row and row[0]:
                project_id = row[0]

        # Fallback: single-project setup
        if not project_id and len(projects) == 1:
            project_id = projects[0].project_id

        if project_id:
            memory_updates.append((project_id, mid, title))

    print(f"Memories that can be updated: {len(memory_updates)}")
    for pid, mid, title in memory_updates:
        print(f"  memory {mid} '{title[:40]}' -> {pid}")

    # -- Apply memory updates ----------------------------------------------------
    if not dry_run and memory_updates:
        print()
        print("Applying memory updates...")
        for pid, mid, _ in memory_updates:
            conn.execute("UPDATE memories SET project_id = ? WHERE id = ?", (pid, mid))
        conn.commit()
        print(f"Updated {len(memory_updates)} memories")
    elif dry_run and (event_updates or memory_updates):
        print()
        print("DRY RUN: no changes applied. Run without --dry-run to apply.")
    else:
        print()
        print("Nothing to update.")

    # -- Summary ----------------------------------------------------------------
    print()
    remaining_events = conn.execute(
        "SELECT COUNT(*) FROM events WHERE project_id IS NULL"
    ).fetchone()[0]
    remaining_memories = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE project_id IS NULL"
    ).fetchone()[0]
    print(f"Remaining: {remaining_events} events, {remaining_memories} memories with project_id=NULL")

    conn.close()


def _resolve_from_payload(payload_raw: str, registry: ProjectRegistry) -> str | None:
    """Try to find chat_id in event payload and resolve project_id."""
    if not payload_raw:
        return None
    try:
        data = json.loads(payload_raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    chat_id = data.get("chat_id")
    if chat_id:
        return registry.project_for_chat(chat_id)
    return None


def _resolve_from_content(content_raw: str, registry: ProjectRegistry) -> str | None:
    """Try to find chat_id or doc_id in content/payload and resolve project_id."""
    if not content_raw:
        return None
    try:
        data = json.loads(content_raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    # Check chat_id in payload-like structures
    chat_id = data.get("chat_id")
    if chat_id:
        return registry.project_for_chat(chat_id)

    # Check scope/project context
    scope = data.get("scope", "")
    if scope == "project":
        # Has project scope but no explicit project_id — check source_ref
        pass

    return None


def _resolve_from_source_ref(
    source_ref: str, source_type: str, registry: ProjectRegistry
) -> str | None:
    """Try to resolve project_id from source_ref (for doc/wiki sources)."""
    if source_type in ("doc", "wiki"):
        pid = registry.project_for_doc(source_ref)
        if pid:
            return pid
        return registry.project_for_wiki(source_ref)
    return None


# Strategy: how to resolve project_id for records with project_id=NULL
# Available strategies (applied in order):
STRATEGY_PAYLOAD = "payload"      # Read chat_id from events.payload_json
STRATEGY_OPENCLAW = "openclaw"   # source_ref starts with "openclaw:" -> assign project_id
STRATEGY_CONTENT = "content"     # Read from content text
STRATEGY_SOURCE_REF = "source_ref"  # doc/wiki source_ref


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    db_path = DB_PATH
    for i, arg in enumerate(sys.argv):
        if arg == "--db-path" and i + 1 < len(sys.argv):
            db_path = sys.argv[i + 1]
    backfill(db_path=db_path, dry_run=dry_run)
