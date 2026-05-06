from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from memory_engine.env import load_project_env


def test_load_project_env_reads_simple_key_values() -> None:
    case_dir = Path("tests_runtime") / "env_loader" / str(uuid.uuid4())
    case_dir.mkdir(parents=True, exist_ok=True)
    env_path = case_dir / ".env"
    env_path.write_text(
        "\n".join(
            [
                "OPENAI_API_BASE=https://example.test/v1",
                "OPENAI_API_KEY='secret-value'",
                'OPENAI_MODEL="test-model"',
                "# ignored comment",
            ]
        ),
        encoding="utf-8",
    )
    old_values = {key: os.environ.get(key) for key in ("OPENAI_API_BASE", "OPENAI_API_KEY", "OPENAI_MODEL")}
    try:
        for key in old_values:
            os.environ.pop(key, None)

        loaded = load_project_env(env_path)

        assert loaded["OPENAI_API_BASE"] == "https://example.test/v1"
        assert os.environ["OPENAI_API_KEY"] == "secret-value"
        assert os.environ["OPENAI_MODEL"] == "test-model"
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(case_dir, ignore_errors=True)
