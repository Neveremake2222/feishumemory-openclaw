from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from .env import load_project_env


@dataclass(frozen=True)
class SummarySubAgent:
    """Small LLM-backed rewriter for dashboard answer summaries."""

    api_base: str | None = None
    api_key: str | None = None
    model: str | None = None
    timeout_seconds: float = 8.0

    @classmethod
    def from_env(cls) -> "SummarySubAgent":
        load_project_env()
        timeout_raw = os.getenv("SUMMARY_SUBAGENT_TIMEOUT_SECONDS", "8")
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError:
            timeout_seconds = 8.0
        return cls(
            api_base=os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("SUMMARY_SUBAGENT_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini",
            timeout_seconds=timeout_seconds,
        )

    def rewrite(
        self,
        *,
        question: str,
        memories: list[dict[str, Any]],
        fallback: str,
        draft: str | None = None,
    ) -> str | None:
        if os.getenv("SUMMARY_SUBAGENT_ENABLED", "true").lower() in {"0", "false", "no", "off"}:
            return None
        if not self.api_key or not self.api_base or not self.model:
            return None

        context = _memory_context(memories)
        if not context and not draft:
            return None

        user_prompt = (
            "用户问题：\n"
            f"{question or '生成项目摘要'}\n\n"
            "候选摘要或草稿：\n"
            f"{draft or fallback}\n\n"
            "可引用记忆：\n"
            f"{context or '无'}\n\n"
            "请用中文改写成简短摘要，最多 3 条要点。只使用给定记忆，不补充未知事实。"
        )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是企业项目管理助手的摘要子 agent。"
                        "你的任务是把召回到的项目记忆改写成清晰、克制、可审计的摘要。"
                        "不要编造事实；不确定就写证据不足。"
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 220,
        }

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    _chat_completions_url(self.api_base),
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except Exception:
            return None

        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        summary = str(content or "").strip()
        if not summary:
            return None
        return summary[:800]


def _chat_completions_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _memory_context(memories: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, item in enumerate(memories[:5], start=1):
        memory_type = item.get("memory_type") or "memory"
        title = item.get("title") or ""
        summary = item.get("summary") or ""
        source = item.get("source_ref") or ""
        time = item.get("updated_at") or item.get("created_at") or ""
        lines.append(
            f"{index}. type={memory_type}; title={title}; summary={summary}; source={source}; time={time}"
        )
    return "\n".join(lines)
