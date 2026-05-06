from __future__ import annotations

import os
from pathlib import Path


def load_project_env(path: str | Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Load simple KEY=VALUE pairs from a local .env file without extra deps."""
    env_path = Path(path) if path is not None else Path(__file__).resolve().parent.parent / ".env"
    loaded: dict[str, str] = {}
    if not env_path.exists() or not env_path.is_file():
        return loaded

    text = env_path.read_text(encoding="utf-8-sig", errors="ignore")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = _clean_env_value(value.strip())
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def _clean_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
