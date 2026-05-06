"""Feishu ingest — adapter pipeline for memory-engine."""

from feishu_ingest.models import FeishuEvent
from feishu_ingest.adapters.base import FeishuSourceAdapter
from feishu_ingest.pipeline import run_ingest, PipelineResult
from feishu_ingest.project_registry import ProjectRegistry, ProjectRegistryProject

__all__ = [
    "FeishuEvent",
    "FeishuSourceAdapter",
    "run_ingest",
    "PipelineResult",
    "ProjectRegistry",
    "ProjectRegistryProject",
]
