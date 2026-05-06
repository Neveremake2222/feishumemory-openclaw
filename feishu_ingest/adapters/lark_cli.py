"""lark-cli read-only adapter for feishu-ingest MVP 2.

This adapter reads Feishu data via the lark-cli CLI tool.
Command shapes are parameterized templates — exact syntax must be verified
with `lark-cli schema` when the CLI is available. All output parsing is
defensive: malformed items are skipped without crashing the stream.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator

from memory_engine.models import Scope

from feishu_ingest.adapters.base import FeishuSourceAdapter
from feishu_ingest.adapters.message_utils import normalize_message_content, with_inferred_scope
from feishu_ingest.models import FeishuEvent

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SetupError(Exception):
    """Raised when lark-cli is missing, unauthenticated, or misconfigured."""


class CommandRunner(ABC):
    """Abstract command execution — injectable for testing."""

    @abstractmethod
    def run(self, cmd: list[str], timeout: int = 30) -> str:
        """Execute a command and return stdout."""
        raise NotImplementedError

    @abstractmethod
    def check_auth(self) -> dict[str, Any]:
        """Run `lark-cli auth status`. Returns dict with 'authenticated' bool."""
        raise NotImplementedError

    def run_background(self, cmd: list[str]) -> "BackgroundProcess":
        """Start a long-running command and return immediately. Does not block."""
        raise NotImplementedError("Background execution not supported for this runner")


class SubprocessCommandRunner(CommandRunner):
    """Real command runner using subprocess. Handles Windows path and UTF-8."""

    def __init__(self, lark_cli_path: str | None = None) -> None:
        self._cli = lark_cli_path or self._find_lark_cli()

    @staticmethod
    def _find_lark_cli() -> str:
        if sys.platform == "win32":
            npm_root = Path.home() / "AppData" / "Roaming" / "npm"
            for ext in (".cmd", ".ps1", ""):
                candidate = npm_root / f"lark-cli{ext}"
                if candidate.exists():
                    return str(candidate)
        return "lark-cli"

    def run(self, cmd: list[str], timeout: int = 30) -> str:
        actual_cmd = [self._cli] + cmd[1:] if cmd and cmd[0] == "lark-cli" else cmd
        result = subprocess.run(actual_cmd, capture_output=True, timeout=timeout)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"lark-cli exited {result.returncode}: {stderr}")
        return result.stdout.decode("utf-8", errors="replace")

    def check_auth(self) -> dict[str, Any]:
        try:
            output = self.run([self._cli, "auth", "status"], timeout=10)
        except FileNotFoundError as exc:
            raise SetupError(
                "lark-cli not found. Install with: npm install -g @larksuite/cli"
            ) from exc
        try:
            status = json.loads(output)
        except json.JSONDecodeError:
            return {"authenticated": False, "raw": output}
        has_identity = bool(status.get("identity"))
        return {"authenticated": has_identity, **status}

    def run_background(self, cmd: list[str]) -> "BackgroundProcess":
        """Start a long-running command and return immediately."""
        actual_cmd = [self._cli] + cmd[1:] if cmd and cmd[0] == "lark-cli" else cmd
        proc = subprocess.Popen(
            actual_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return BackgroundProcess(proc)


class BackgroundProcess:
    """Wrapper around a running subprocess for streaming consumption."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc

    def stdout_lines(self) -> Iterator[str]:
        """Yield stdout lines as they arrive. Blocks on each line."""
        if self._proc.stdout is None:
            return
        for raw_line in self._proc.stdout:
            yield raw_line.decode("utf-8", errors="replace").rstrip("\n\r")

    def terminate(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()


# ---------------------------------------------------------------------------
# Command templates — parameterized, NOT hardcoded.
# Verified with `lark-cli schema` when CLI is available.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Command templates — verified against lark-cli 1.0.23
#
# lark-cli im +chat-messages-list --chat-id <id> --format json [--start ISO] [--end ISO] [--sort desc|asc] [--page-size N]
# lark-cli docs +fetch --doc <token_or_url> --format json
# ---------------------------------------------------------------------------

_MSG_LIST_CMD = ["lark-cli", "im", "+chat-messages-list", "--format", "json"]
_DOC_FETCH_CMD = ["lark-cli", "docs", "+fetch", "--format", "json"]


class LarkCLIAdapter(FeishuSourceAdapter):
    """Read-only Feishu adapter using lark-cli commands.

    Usage:
        runner = SubprocessCommandRunner()
        adapter = LarkCLIAdapter(
            command_runner=runner,
            sources=[LarkCLISource.im(chat_id="oc_xxx", limit=50)],
        )
        for event in adapter.stream_events():
            ...
    """

    def __init__(
        self,
        *,
        command_runner: CommandRunner,
        sources: Iterable["LarkCLISource"],
    ) -> None:
        self._runner = command_runner
        self._sources = list(sources)
        self._authenticated: bool | None = None

    def _check_auth(self) -> None:
        """Fail fast if not authenticated."""
        try:
            status = self._runner.check_auth()
        except FileNotFoundError as exc:
            raise SetupError(
                "lark-cli not found. Install with: npm install -g @larksuite/cli"
            ) from exc
        except Exception as exc:
            raise SetupError(f"lark-cli auth check failed: {exc}") from exc

        if not status.get("authenticated"):
            raise SetupError(
                "lark-cli not authenticated. "
                "Run `lark-cli auth login --recommend` first. "
                f"Status: {status}"
            )
        self._authenticated = True

    def stream_events(self) -> Iterator[FeishuEvent]:
        """Yield normalised FeishuEvent objects from lark-cli output."""
        self._check_auth()

        for source in self._sources:
            try:
                cmd = source.build_command()
            except ValueError as exc:
                logger.error("Invalid source config: %s", exc)
                continue

            try:
                output = self._runner.run(cmd, timeout=60)
            except Exception as exc:
                logger.error("Command failed for source %s: %s", source.kind, exc)
                continue

            parsed = _parse_output(source.kind, output)
            for event in parsed:
                yield event

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Source descriptors
# ---------------------------------------------------------------------------

class LarkCLISource:
    """Describes a single lark-cli data source to fetch."""

    def __init__(self, kind: str, params: dict[str, Any] | None = None) -> None:
        if kind not in ("im", "doc"):
            raise ValueError(f"Unknown source kind: {kind!r}. Expected 'im' or 'doc'.")
        self.kind = kind
        self.params = dict(params or {})

    def build_command(self) -> list[str]:
        if self.kind == "im":
            cmd = list(_MSG_LIST_CMD)
            chat_id = self.params.get("chat_id")
            user_id = self.params.get("user_id")
            if chat_id:
                cmd.extend(["--chat-id", str(chat_id)])
            elif user_id:
                cmd.extend(["--user-id", str(user_id)])
            else:
                raise ValueError("im source requires 'chat_id' or 'user_id' parameter")
            if self.params.get("limit"):
                cmd.extend(["--page-size", str(self.params["limit"])])
            if self.params.get("start"):
                cmd.extend(["--start", str(self.params["start"])])
            if self.params.get("end"):
                cmd.extend(["--end", str(self.params["end"])])
            if self.params.get("sort"):
                cmd.extend(["--sort", str(self.params["sort"])])
            return cmd
        if self.kind == "doc":
            doc_id = self.params.get("doc_id")
            if not doc_id:
                raise ValueError("doc source requires 'doc_id' parameter")
            cmd = list(_DOC_FETCH_CMD)
            cmd.extend(["--doc", str(doc_id)])
            return cmd
        raise ValueError(f"Cannot build command for kind: {self.kind}")

    @classmethod
    def im(
        cls,
        *,
        chat_id: str | None = None,
        user_id: str | None = None,
        limit: int = 50,
        start: str | None = None,
        end: str | None = None,
        sort: str = "desc",
    ) -> "LarkCLISource":
        return cls("im", {
            "chat_id": chat_id,
            "user_id": user_id,
            "limit": limit,
            "start": start,
            "end": end,
            "sort": sort,
        })

    @classmethod
    def doc(cls, doc_id: str) -> "LarkCLISource":
        return cls("doc", {"doc_id": doc_id})


# ---------------------------------------------------------------------------
# Output parsing — defensive, never crashes on bad items
# ---------------------------------------------------------------------------

def _parse_output(kind: str, raw: str) -> list[FeishuEvent]:
    """Parse lark-cli JSON output into FeishuEvents.

    Skips items with missing required fields. Logs parse errors.
    Output shape is tentative — field names verified when CLI is available.
    """
    if kind == "im":
        return _parse_messages(raw)
    if kind == "doc":
        return _parse_doc(raw)
    logger.warning("Unknown source kind for parsing: %s", kind)
    return []


def _parse_messages(raw: str) -> list[FeishuEvent]:
    """Parse message list JSON output."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse message JSON: %s", exc)
        return []

    items = _extract_items(data)
    events: list[FeishuEvent] = []

    for item in items:
        try:
            event = _message_to_event(item)
            events.append(event)
        except Exception as exc:
            msg_id = item.get("message_id", "<unknown>")
            logger.error("Skipping malformed message %s: %s", msg_id, exc)
            continue

    return events


def _parse_doc(raw: str) -> list[FeishuEvent]:
    """Parse document fetch JSON output."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse doc JSON: %s", exc)
        return []

    # Doc response may be a single item or wrapped in data
    doc_data = data.get("data", data)
    try:
        event = _doc_to_event(doc_data)
        return [event]
    except Exception as exc:
        doc_id = doc_data.get("document_id", "<unknown>")
        logger.error("Skipping malformed doc %s: %s", doc_id, exc)
        return []


def _extract_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract items list from API response, handling multiple envelope shapes."""
    # Shape 1: {"data": {"items": [...]}} or {"data": {"messages": [...]}}
    if "data" in data and isinstance(data["data"], dict):
        items = data["data"].get("items") or data["data"].get("messages") or []
        if items:
            return items
    # Shape 2: {"items": [...]}
    if "items" in data:
        return data["items"]
    # Shape 3: single item (no list wrapper)
    return [data]


def _message_to_event(item: dict[str, Any]) -> FeishuEvent:
    """Convert a single message item to FeishuEvent. Raises on missing fields."""
    message_id = item.get("message_id")
    text = _extract_message_text(item)
    if not message_id:
        raise ValueError("missing message_id")
    if not text:
        raise ValueError("missing text content")

    sender = item.get("sender", {})
    user_id = (sender.get("id") or sender.get("user_id", "")) if isinstance(sender, dict) else str(sender)
    actors = [user_id] if user_id else []

    timestamp = _normalize_timestamp(item.get("create_time", ""))

    chat_id = item.get("chat_id", "")

    return with_inferred_scope(FeishuEvent(
        source_type="message",
        source_ref=message_id,
        source_url=None,
        actors=actors,
        timestamp=timestamp,
        content=text,
        scope=Scope.USER,
        project_id=None,
        task_id=None,
        user_id=user_id or None,
        payload={
            "chat_id": chat_id,
            "msg_type": item.get("msg_type", "text"),
            "chat_title": item.get("chat_name", ""),
        },
        content_hash=None,
        source_version=None,
    ))


def _extract_message_text(item: dict[str, Any]) -> str:
    """Extract human-readable text from lark-cli message fields."""
    return normalize_message_content(item.get("text") or item.get("content") or item.get("raw_content", ""))


def _doc_to_event(data: dict[str, Any]) -> FeishuEvent:
    """Convert a doc response to FeishuEvent. Raises on missing fields."""
    doc_id = data.get("document_id") or data.get("doc_id")
    content = data.get("content") or data.get("text") or data.get("raw_content", "")
    if not doc_id:
        raise ValueError("missing document_id")
    if not content:
        raise ValueError("missing document content")

    owner = data.get("owner", {})
    user_id = owner.get("user_id", "") if isinstance(owner, dict) else str(owner)
    actors = [user_id] if user_id else []

    timestamp = _normalize_timestamp(data.get("create_time") or data.get("last_modified_time", ""))

    return with_inferred_scope(FeishuEvent(
        source_type="doc",
        source_ref=doc_id,
        source_url=data.get("url"),
        actors=actors,
        timestamp=timestamp,
        content=content,
        scope=Scope.USER,
        project_id=None,
        task_id=None,
        user_id=user_id or None,
        payload={
            "doc_title": data.get("title", ""),
        },
        content_hash=None,
        source_version=None,
    ))


def _normalize_timestamp(raw: str | int | None) -> str:
    """Normalize various timestamp formats to ISO 8601."""
    if not raw:
        return datetime.now(timezone.utc).isoformat()

    # Unix epoch seconds (int or string)
    try:
        ts = int(raw)
        if ts > 1_000_000_000_000:
            ts = ts // 1000
        if ts > 1_000_000_000:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        pass

    # Already ISO 8601
    if isinstance(raw, str) and "T" in raw:
        return raw

    return str(raw)
