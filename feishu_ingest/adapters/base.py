"""Abstract base for Feishu source adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from feishu_ingest.models import FeishuEvent


class FeishuSourceAdapter(ABC):
    """Abstract adapter — fixture / lark-cli / live event all plug in here."""

    @abstractmethod
    def stream_events(self) -> Iterator["FeishuEvent"]:
        """Yield normalised FeishuEvent objects. Raise on fatal setup errors."""
        raise NotImplementedError

    def close(self) -> None:
        """Optional teardown; default no-op."""
        pass
