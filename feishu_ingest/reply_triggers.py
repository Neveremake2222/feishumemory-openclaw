"""Trigger detection for proactive memory replies (A2/A3/C2)."""

from __future__ import annotations

import re

# A2: related historical memory push
RELATED_TRIGGERS = [
    r"what was",
    r"which one",
    r"decided",
    r"previously",
    r"before",
    r"ago",
    r"previous decision",
    r"last time",
    r"earlier",
    r"\u4e4b\u524d",
    r"\u4e0a\u6b21",
    r"\u4e0a\u4e00\u4e2a",
    r"\u6765\u7740",
    r"\u51b3\u5b9a",
    r"\u5b9a.*\u65b9\u6848",
    r"\u9009\u578b",
    r"之前",
    r"上次",
    r"上一个",
    r"之前那个",
]

# A3: structured memory summary request
SUMMARY_TRIGGERS = [
    r"summary",
    r"summarize",
    r"overview",
    r"recap",
    r"\u6c47\u603b",
    r"\u603b\u7ed3",
    r"\u6982\u89c8",
    r"\u4e00\u89c8",
    r"\u6574\u7406",
    r"\u9879\u76ee.*\u60c5\u51b5",
    r"\u6709\u54ea\u4e9b.*\u51b3\u7b56",
    r"汇总",
    r"总结",
    r"概览",
    r"一览",
    r"整理一下",
]

# C2: operation context that should remind preferences
OPERATION_TRIGGERS = [
    r"current task",
    r"task progress",
    r"next step",
    r"working on",
    r"plan",
    r"review task",
    r"\u5f00\u59cb",
    r"\u5199\u4ee3\u7801",
    r"\u5904\u7406.*\u4efb\u52a1",
    r"\u5468\u62a5",
    r"\u89c4\u5212",
    r"\u5f53\u524d\u4efb\u52a1",
    r"\u4efb\u52a1\u8fdb\u5ea6",
    r"\u4e0b\u4e00\u6b65",
    r"\u6b63\u5728",
    r"正在",
    r"当前任务",
    r"任务进度",
    r"下一步",
]

PREFERENCE_CONFIRM_RE = re.compile(r"^\s*(?:确认偏好|confirm preference)\s+([\w.\-:]+)\s*$", re.I)
PREFERENCE_REJECT_RE = re.compile(r"^\s*(?:拒绝偏好|reject preference)\s+([\w.\-:]+)\s*$", re.I)

WORKFLOW_CONFIRM_RE = re.compile(
    r"^\s*(?:\u786e\u8ba4\u5de5\u4f5c\u6d41(?:\u6280\u80fd)?|confirm workflow(?: skill)?)\s+([\w.\-:]+)\s*$",
    re.I,
)
WORKFLOW_REJECT_RE = re.compile(
    r"^\s*(?:\u62d2\u7edd\u5de5\u4f5c\u6d41(?:\u6280\u80fd)?|reject workflow(?: skill)?)\s+([\w.\-:]+)\s*$",
    re.I,
)


def is_related_trigger(content: str) -> bool:
    """A2: Check whether content asks for related historical context."""
    return any(re.search(pattern, content, re.IGNORECASE) for pattern in RELATED_TRIGGERS)


def is_summary_trigger(content: str) -> bool:
    """A3: Check whether content requests a structured memory summary."""
    return any(re.search(pattern, content, re.IGNORECASE) for pattern in SUMMARY_TRIGGERS)


def is_operation_trigger(content: str) -> bool:
    """C2: Check whether content describes an operation context."""
    return any(re.search(pattern, content, re.IGNORECASE) for pattern in OPERATION_TRIGGERS)


def parse_preference_candidate_command(content: str) -> tuple[str, str] | None:
    """Parse preference candidate confirmation commands.

    Returns (action, pattern_key), where action is "confirm" or "reject".
    """
    confirm = PREFERENCE_CONFIRM_RE.match(content or "")
    if confirm:
        return "confirm", confirm.group(1)
    reject = PREFERENCE_REJECT_RE.match(content or "")
    if reject:
        return "reject", reject.group(1)
    return None


def parse_workflow_strategy_command(content: str) -> tuple[str, str] | None:
    """Parse workflow strategy confirmation commands.

    Returns (action, task_type), where action is "confirm" or "reject".
    """
    confirm = WORKFLOW_CONFIRM_RE.match(content or "")
    if confirm:
        return "confirm", confirm.group(1)
    reject = WORKFLOW_REJECT_RE.match(content or "")
    if reject:
        return "reject", reject.group(1)
    return None
