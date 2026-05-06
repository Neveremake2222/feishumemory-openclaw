"""CLI entry point for subprocess-based integration.

Usage:
    echo '<json>' | python -m openclaw_adapter.cli recall
    echo '<json>' | python -m openclaw_adapter.cli write
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root is importable
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid JSON: {exc}"}), file=sys.stderr)
        sys.exit(1)

    if cmd == "recall":
        from openclaw_adapter.recall_hook import recall as do_recall
        from openclaw_adapter.types import OpenClawContext

        ctx = OpenClawContext(**payload)
        output = do_recall(ctx, limit=payload.get("limit", 5))
        print(json.dumps({
            "injection_md": output.injection_md,
            "query": output.query,
            "memory_count": len(output.results),
        }))

    elif cmd == "write":
        from openclaw_adapter.write_hook import write as do_write
        from openclaw_adapter.types import OpenClawEvent

        event = OpenClawEvent(**payload)
        result = do_write(event)
        print(json.dumps({
            "action": result.action,
            "written": result.written,
            "memory_ids": result.memory_ids,
            "skip_reason": result.skip_reason,
            "conflict_detected": result.conflict_detected,
        }))

    else:
        print(json.dumps({"error": f"unknown command: {cmd}"}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
