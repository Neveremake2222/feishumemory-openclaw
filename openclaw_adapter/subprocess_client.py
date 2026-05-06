"""SubprocessClient — calls openclaw_adapter.cli via subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any


class SubprocessClient:
    """Calls openclaw_adapter.cli via subprocess for process isolation."""

    def __init__(self, timeout: int = 10) -> None:
        self._timeout = timeout

    def _run(self, cmd: list[str], payload: dict[str, Any]) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, "-m", "openclaw_adapter.cli"] + cmd,
            input=json.dumps(payload),
            capture_output=True,
            timeout=self._timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"adapter CLI failed: {proc.stderr.decode()}")
        return json.loads(proc.stdout)

    def recall(self, **kwargs: Any) -> list[dict[str, Any]]:
        result = self._run(["recall"], kwargs)
        return result.get("memories", [])

    def write(self, **kwargs: Any) -> dict[str, Any]:
        return self._run(["write"], kwargs)
