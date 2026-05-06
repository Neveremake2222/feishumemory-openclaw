"""Live integration test 鈥?reads real Feishu group messages via lark-cli and writes to memory engine.

Run directly (no mocks):
    python tests/live_feishu_test.py

Requires:
    - lark-cli authenticated with a Feishu App that has im:message permission
    - Bot added to at least one group chat
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory_engine import MemoryEngine, RecallRequest

LARK_CLI = "C:/Users/鍗庣/AppData/Roaming/npm/lark-cli.cmd"

# Real chat IDs from the authenticated bot
_CHAT_IDS = [
    "oc_example_chat_id",  # 123娴嬭瘯
    "oc_example_chat_id",  # 123娴嬭瘯2
]


def _run_cmd(cmd: list[str], timeout: int = 30) -> str:
    result = subprocess.run(
        [LARK_CLI] + cmd[1:], capture_output=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed {result.returncode}: {result.stderr}")
    return result.stdout.decode("utf-8", errors="replace")


def fetch_messages(chat_id: str, limit: int = 20) -> list[dict]:
    output = _run_cmd([
        "lark-cli", "im", "+chat-messages-list",
        "--chat-id", chat_id,
        "--format", "json",
        "--page-size", str(limit),
        "--sort", "desc",
    ])
    data = json.loads(output)
    return data.get("data", {}).get("messages", [])


def _normalize_timestamp(raw: str) -> str:
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    try:
        ts = int(raw)
        if ts > 1_000_000_000:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        pass
    if isinstance(raw, str) and "T" in raw:
        return raw
    return str(raw)


def _content_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_candidates(text: str) -> list[dict]:
    """Simple rule-based extraction matching feishu_ingest extractors."""
    import re
    candidates = []

    # Decision patterns (Chinese + English)
    decision_kw = re.compile(
        r"鍐冲畾|缁撹|閲囩敤|纭|鏂规"
        r"|涓嶅啀浣跨敤|閫夊畾浜?
        r"|\bdecid|\bconclusion\b",
        re.I,
    )
    task_kw = re.compile(
        r"瀹屾垚|杩涜涓瓅闃诲|寰呭姙|涓嬩竴姝?
        r"|杩涘害|\bcompleted?\b|\bblocked?\b|\bin progress\b",
        re.I,
    )
    pref_kw = re.compile(
        r"鎴戝笇鏈泑鎴戞洿鍠滄|浼樺厛"
        r"|浠ュ悗閮絴浠ュ悗榛樿|涓嶈鍐峾寤鸿鐢?
        r"|\bprefer\b",
        re.I,
    )

    dec_hits = len(decision_kw.findall(text))
    task_hits = len(task_kw.findall(text))
    pref_hits = len(pref_kw.findall(text))

    if dec_hits > 0:
        candidates.append({"type": "decision", "confidence": 0.8 if dec_hits >= 2 else 0.6})
    if task_hits > 0:
        candidates.append({"type": "task_status", "confidence": 0.8 if task_hits >= 2 else 0.6})
    if pref_hits > 0:
        candidates.append({"type": "preference", "confidence": 0.8 if pref_hits >= 2 else 0.6})

    return candidates


def main() -> None:
    db_path = Path(__file__).parent.parent / "benchmarks_runtime" / "live_feishu.db"
    shutil.rmtree(db_path.parent, ignore_errors=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = MemoryEngine(db_path)
    total_written = 0
    total_candidates = 0

    for chat_id in _CHAT_IDS:
        print(f"\n=== Fetching chat {chat_id} ===")
        try:
            messages = fetch_messages(chat_id)
        except Exception as e:
            print(f"  SKIP: {e}")
            continue

        print(f"  Got {len(messages)} messages")

        for msg in messages:
            msg_type = msg.get("msg_type", "")
            if msg_type == "system":
                continue

            content = msg.get("content", "")
            if not content or msg_type == "post":
                # Try to extract text from post
                try:
                    post_data = json.loads(content)
                    # Skip rich text posts for now
                    continue
                except (json.JSONDecodeError, TypeError):
                    if not content:
                        continue

            sender = msg.get("sender", {})
            user_id = sender.get("id", "") if isinstance(sender, dict) else str(sender)

            timestamp = _normalize_timestamp(msg.get("create_time", ""))
            source_ref = msg.get("message_id", "")
            content_hash = _content_hash(content)

            from memory_engine.models import SourceEvent
            event = SourceEvent(
                source_type="message",
                source_ref=source_ref,
                actors=[user_id] if user_id else [],
                timestamp=timestamp,
                content=content[:2000],  # truncate long messages
                scope="project",
                payload={
                    "content_hash": content_hash,
                    "source_version": None,
                    "chat_id": chat_id,
                    "msg_type": msg_type,
                },
            )

            candidates = _extract_candidates(content)
            if not candidates:
                continue

            total_candidates += len(candidates)
            print(f"  [{msg_type}] cand={len(candidates)} | {content[:60]}...")

            from memory_engine.models import MemoryCandidate
            mc_list = [
                MemoryCandidate(
                    memory_type=c["type"],
                    title=content[:50],
                    summary=content[:500],
                    content={"scope": "project", "chat_id": chat_id},
                    importance=0.7,
                    confidence=c["confidence"],
                    evidence=[{"source_ref": source_ref, "chat_id": chat_id}],
                )
                for c in candidates
            ]

            result = engine.write(event=event, memory_candidates=mc_list, project_id="feishu_test")
            print(f"    -> written {len(result['memory_ids'])} memories, conflicts={len(result.get('conflicts', []))}")
            total_written += len(result["memory_ids"])

    print(f"\n=== Summary: {total_written} memories written from {total_candidates} candidates ===")

    # Verify recall
    print("\n=== Recall: learning progress ===")
    results = engine.recall(RecallRequest(query="瀛︿範杩涘害", project_id="feishu_test"), limit=5)
    print(f"  Found {len(results)} results")
    for r in results:
        print(f"  [{r['memory_type']}] {r['title'][:60]}")

    print("\n=== Recall: study time ===")
    results = engine.recall(RecallRequest(query="瀛︿範鏃堕暱 灏忔椂", project_id="feishu_test"), limit=5)
    print(f"  Found {len(results)} results")
    for r in results:
        print(f"  [{r['memory_type']}] {r['title'][:60]}")

    # DB stats
    row = engine.conn.execute("SELECT COUNT(*) as total, SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active FROM memories").fetchone()
    print(f"\n=== DB: {row['total']} total memories, {row['active']} active ===")

    engine.close()


if __name__ == "__main__":
    main()
