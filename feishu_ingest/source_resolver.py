"""Read-only Feishu source fingerprint resolver.

The memory engine calls resolvers as ``resolver(source_type, source_ref)`` and
expects fingerprint metadata only. This module keeps raw Feishu content out of
validation results and logs by returning hashes and versions, not message text.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from feishu_ingest.adapters.message_utils import normalize_message_content


ResolverFetcher = Callable[[str, str], Mapping[str, Any] | None]

_SOURCE_TYPE_ALIASES = {
    "message": "message",
    "im.message": "message",
    "feishu_message": "message",
    "doc": "doc",
    "feishu_doc": "doc",
    "docs.document": "doc",
    "document": "doc",
    "wiki": "wiki",
    "feishu_wiki": "wiki",
    "wiki_node": "wiki",
}


@dataclass
class SourceFingerprint:
    exists: bool | None
    content_hash: str | None = None
    source_version: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"exists": self.exists}
        if self.content_hash is not None:
            result["content_hash"] = self.content_hash
        if self.source_version is not None:
            result["source_version"] = self.source_version
        if self.reason:
            result["reason"] = self.reason
        return result


class FeishuSourceResolver:
    """Resolve Feishu message/doc source fingerprints without returning content."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        fetcher: ResolverFetcher | None = None,
        source_fetchers: Mapping[str, ResolverFetcher] | None = None,
    ) -> None:
        self._client = client
        self._fetcher = fetcher
        self._source_fetchers = {
            _normalize_source_type(source_type): fetcher
            for source_type, fetcher in (source_fetchers or {}).items()
        }

    @classmethod
    def from_app_credentials(cls, app_id: str, app_secret: str) -> "FeishuSourceResolver":
        lark = _load_lark()
        client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        return cls(client=client)

    def __call__(self, source_type: str, source_ref: str) -> dict[str, Any]:
        normalized_type = _normalize_source_type(source_type)
        typed_fetcher = self._source_fetchers.get(normalized_type)
        if typed_fetcher is not None:
            return _fingerprint_from_mapping(typed_fetcher(normalized_type, source_ref)).as_dict()
        if self._fetcher is not None:
            return _fingerprint_from_mapping(self._fetcher(normalized_type, source_ref)).as_dict()
        if normalized_type == "message":
            return self._resolve_message(source_ref).as_dict()
        if normalized_type in {"doc", "wiki"}:
            return SourceFingerprint(
                exists=None,
                reason=f"no Feishu {normalized_type} fetcher configured",
            ).as_dict()
        return SourceFingerprint(
            exists=None,
            reason=f"unsupported source_type: {source_type}",
        ).as_dict()

    def _resolve_message(self, message_id: str) -> SourceFingerprint:
        if self._client is None:
            return SourceFingerprint(exists=None, reason="no Feishu client configured")
        try:
            from lark_oapi.api.im.v1 import GetMessageRequest

            request = GetMessageRequest.builder().message_id(message_id).build()
            response = self._client.im.v1.message.get(request)
        except Exception as exc:
            return SourceFingerprint(exists=None, reason=f"Feishu SDK error: {type(exc).__name__}")

        if hasattr(response, "success") and not response.success():
            code = str(getattr(response, "code", ""))
            msg = str(getattr(response, "msg", ""))
            if _looks_missing(code, msg):
                return SourceFingerprint(exists=False, reason="source missing")
            return SourceFingerprint(exists=None, reason=f"Feishu API error: {code or msg or 'unknown'}")

        data = getattr(response, "data", response)
        return _fingerprint_from_mapping(_object_to_mapping(data))


def _load_lark() -> Any:
    try:
        import lark_oapi as lark
    except ImportError as exc:
        raise RuntimeError("lark-oapi not installed; run: pip install lark-oapi") from exc
    return lark


def _normalize_source_type(source_type: str) -> str:
    return _SOURCE_TYPE_ALIASES.get(source_type.strip().lower(), source_type.strip().lower())


def _fingerprint_from_mapping(data: Mapping[str, Any] | None) -> SourceFingerprint:
    if not data:
        return SourceFingerprint(exists=None, reason="resolver returned no data")
    if data.get("exists") is False:
        return SourceFingerprint(exists=False, reason=str(data.get("reason") or "source missing"))

    content_hash = _first_string(data, ("content_hash", "hash", "sha256"))
    content = _first_string(data, ("content", "text", "body"))
    if content_hash is None and content is not None:
        normalized = normalize_message_content(content)
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    source_version = _first_string(
        data,
        (
            "source_version",
            "update_time",
            "updated_time",
            "modified_time",
            "create_time",
            "version",
            "revision_id",
            "document_revision_id",
            "node_version",
        ),
    )
    if content_hash is None and source_version is None:
        return SourceFingerprint(exists=True, reason="source returned no fingerprint")
    return SourceFingerprint(exists=True, content_hash=content_hash, source_version=source_version)


def _first_string(data: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _find_key(data, key)
        if value is not None:
            return str(value)
    return None


def _find_key(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        if key in value and value[key] not in (None, ""):
            return value[key]
        for child in value.values():
            found = _find_key(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_key(child, key)
            if found is not None:
                return found
    return None


def _object_to_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, list):
        return {"items": [_object_to_mapping(item) for item in value]}
    if hasattr(value, "to_dict"):
        converted = value.to_dict()
        if isinstance(converted, Mapping):
            return converted
    if hasattr(value, "__dict__"):
        return {
            key: _object_to_mapping(child)
            for key, child in vars(value).items()
            if not key.startswith("_")
        }
    return {"value": value}


def _looks_missing(code: str, msg: str) -> bool:
    lower = f"{code} {msg}".lower()
    return any(token in lower for token in ("404", "not found", "deleted", "missing"))
