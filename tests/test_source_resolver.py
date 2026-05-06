from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from feishu_ingest.source_resolver import FeishuSourceResolver
from memory_engine import MemoryCandidate, MemoryEngine, SourceEvent
from memory_engine.heartbeat import run_once


def test_feishu_source_resolver_hashes_content_without_returning_raw_text() -> None:
    secret_text = "release note with phone 13812345678"
    resolver = FeishuSourceResolver(
        fetcher=lambda source_type, source_ref: {
            "exists": True,
            "content": secret_text,
            "update_time": "v2",
        }
    )

    result = resolver("message", "om_1")

    assert result["exists"] is True
    assert result["content_hash"] == hashlib.sha256(secret_text.encode("utf-8")).hexdigest()
    assert result["source_version"] == "v2"
    assert secret_text not in str(result)
    assert "13812345678" not in str(result)


def test_feishu_source_resolver_uses_typed_doc_fetcher() -> None:
    secret_doc = "doc content with email user@example.com"
    calls: list[tuple[str, str]] = []
    resolver = FeishuSourceResolver(
        source_fetchers={
            "doc": lambda source_type, source_ref: calls.append((source_type, source_ref)) or {
                "exists": True,
                "content": secret_doc,
                "revision_id": "rev-7",
            }
        }
    )

    result = resolver("feishu_doc", "doc_1")

    assert calls == [("doc", "doc_1")]
    assert result["exists"] is True
    assert result["content_hash"] == hashlib.sha256(secret_doc.encode("utf-8")).hexdigest()
    assert result["source_version"] == "rev-7"
    assert secret_doc not in str(result)
    assert "user@example.com" not in str(result)


def test_feishu_source_resolver_uses_typed_wiki_fetcher_for_missing_source() -> None:
    resolver = FeishuSourceResolver(
        source_fetchers={
            "wiki": lambda source_type, source_ref: {
                "exists": False,
                "reason": f"{source_type}:{source_ref} deleted",
            }
        }
    )

    result = resolver("feishu_wiki", "wiki_1")

    assert result == {
        "exists": False,
        "reason": "wiki:wiki_1 deleted",
    }


def test_feishu_source_resolver_recognizes_doc_without_guessing_api() -> None:
    resolver = FeishuSourceResolver()

    result = resolver("doc", "doc_1")

    assert result == {
        "exists": None,
        "reason": "no Feishu doc fetcher configured",
    }


def test_validate_sources_resolver_error_is_per_source_unknown() -> None:
    temp_dir = Path("tests_runtime") / "source_resolver" / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)
    engine = MemoryEngine(temp_dir / "memory.sqlite3")
    try:
        event = SourceEvent(
            source_type="message",
            source_ref="om_error",
            actors=["u1"],
            timestamp="2026-05-06T00:00:00+00:00",
            content="source validation event",
            scope="project",
            payload={"content_hash": "stored_hash"},
        )
        candidate = MemoryCandidate(
            memory_type="decision",
            title="Source validation decision",
            summary="Source validation decision summary.",
            content={"scope": "project"},
            importance=0.7,
            confidence=0.9,
            evidence=[{"source_ref": "om_error"}],
        )
        engine.write(event=event, memory_candidates=[candidate], project_id="p1")

        def failing_resolver(source_type: str, source_ref: str) -> dict:
            raise RuntimeError("network token secret should not leak")

        result = engine.validate_sources(failing_resolver)

        assert result[0]["status"] == "unknown"
        assert result[0]["reason"] == "resolver error: RuntimeError"
        assert "network token secret" not in str(result)
    finally:
        engine.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_heartbeat_passes_source_resolver_to_validation() -> None:
    temp_dir = Path("tests_runtime") / "source_resolver" / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)
    engine = MemoryEngine(temp_dir / "memory.sqlite3")
    try:
        event = SourceEvent(
            source_type="message",
            source_ref="om_ok",
            actors=["u1"],
            timestamp="2026-05-06T00:00:00+00:00",
            content="source validation event",
            scope="project",
            payload={"content_hash": "stored_hash", "source_version": "v1"},
        )
        candidate = MemoryCandidate(
            memory_type="decision",
            title="Heartbeat validation decision",
            summary="Heartbeat validation decision summary.",
            content={"scope": "project"},
            importance=0.7,
            confidence=0.9,
            evidence=[{"source_ref": "om_ok"}],
        )
        engine.write(event=event, memory_candidates=[candidate], project_id="p1")

        result = run_once(
            engine,
            source_resolver=lambda source_type, source_ref: {
                "exists": True,
                "content_hash": "stored_hash",
                "source_version": "v1",
            },
        )

        assert result["source_validation_summary"] == {"ok": 1}
    finally:
        engine.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
