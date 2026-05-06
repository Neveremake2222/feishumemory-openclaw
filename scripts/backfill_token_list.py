"""Backfill token_list and doc_len for existing memories.

Computes tokens from (title + summary) and writes them to the token_list
and doc_len columns. Existing values are overwritten.

Usage:
    # Dry run (show what would change, don't modify)
    python scripts/backfill_token_list.py --dry-run

    # Apply
    python scripts/backfill_token_list.py

    # Custom DB path
    python scripts/backfill_token_list.py --db-path /path/to/memory_engine.sqlite3
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory_engine.ranking import _tokenize

DB_PATH = os.environ.get("MEMORY_ENGINE_DB", "memory_engine.sqlite3")


def backfill(db_path: str = DB_PATH, dry_run: bool = True) -> None:
    from memory_engine import MemoryEngine

    # Use MemoryEngine to ensure all columns exist (init_db + _ensure_column)
    engine = MemoryEngine(db_path)
    conn = engine.conn
    mode = "DRY RUN" if dry_run else "APPLY"
    print(f"Mode: {mode}")
    print(f"DB: {db_path}")

    # Ensure token_list and doc_len columns exist
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if "token_list" not in columns:
        conn.execute("ALTER TABLE memories ADD COLUMN token_list TEXT")
        conn.commit()
        print("Added token_list column")
    if "doc_len" not in columns:
        conn.execute("ALTER TABLE memories ADD COLUMN doc_len INTEGER")
        conn.commit()
        print("Added doc_len column")

    rows = conn.execute(
        "SELECT id, title, summary, token_list FROM memories"
    ).fetchall()
    print(f"Total memories: {len(rows)}")

    to_update = []
    for row in rows:
        text = " ".join([row["title"], row["summary"]])
        tokens = _tokenize(text)
        token_list_json = json.dumps(tokens, ensure_ascii=True)
        doc_len = len(tokens)
        to_update.append((token_list_json, doc_len, row["id"]))

    needs_update = [
        (tj, dl, rid) for tj, dl, rid in to_update
        if rows[rid - 1]["token_list"] is None
    ]

    print(f"Memories needing token_list: {len(needs_update)}")
    for tj, dl, rid in needs_update[:5]:
        print(f"  memory {rid}: doc_len={dl}")
    if len(needs_update) > 5:
        print(f"  ... and {len(needs_update) - 5} more")

    if not dry_run and needs_update:
        print()
        print("Applying updates...")
        for tj, dl, rid in needs_update:
            conn.execute(
                "UPDATE memories SET token_list = ?, doc_len = ? WHERE id = ?",
                (tj, dl, rid),
            )
        conn.commit()
        print(f"Updated {len(needs_update)} memories")
    elif dry_run and needs_update:
        print()
        print("DRY RUN: no changes applied. Run without --dry-run to apply.")
    else:
        print()
        print("All memories already have token_list. Nothing to update.")

    engine.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    db_path = DB_PATH
    for i, arg in enumerate(sys.argv):
        if arg == "--db-path" and i + 1 < len(sys.argv):
            db_path = sys.argv[i + 1]
    backfill(db_path=db_path, dry_run=dry_run)
