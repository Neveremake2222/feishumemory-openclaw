"""Push trigger evaluation for A2/A3/C2 proactive replies.

Usage:
    python benchmarks/evaluation/push_eval.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from feishu_ingest.reply_triggers import (
    is_operation_trigger,
    is_related_trigger,
    is_summary_trigger,
)

MIN_PRECISION = 0.90
MIN_RECALL = 0.90

A2_SHOULD_TRIGGER = [
    "what was the previous SQLite decision?",
    "which one did we decide before?",
    "show me the earlier architecture decision",
    "上次的方案是什么？",
    "之前那个 SQLite 决策是什么？",
]

A2_SHOULD_NOT_TRIGGER = [
    "please summarize the current project",
    "current task progress",
    "I prefer markdown output",
    "决定使用 SQLite 作为本地存储",
    "给我总结一下当前项目",
]

A3_SHOULD_TRIGGER = [
    "summarize the current project",
    "give me a memory overview",
    "project recap please",
    "给我总结一下当前项目",
    "整理一下项目记忆",
]

A3_SHOULD_NOT_TRIGGER = [
    "what was the previous decision?",
    "current task progress",
    "I prefer concise replies",
    "决定采用 PostgreSQL",
    "之前那个方案是什么？",
]

C2_SHOULD_TRIGGER = [
    "current task progress",
    "what is the next step for this project?",
    "I am working on the review task",
    "当前任务进度",
    "下一步怎么做？",
]

C2_SHOULD_NOT_TRIGGER = [
    "what was the previous decision?",
    "summarize the current project",
    "I prefer markdown output",
    "决定采用 Redis",
    "之前那个方案是什么？",
]


def _eval_trigger(
    name: str,
    trigger_fn: Callable[[str], bool],
    should: list[str],
    should_not: list[str],
) -> dict:
    tp = sum(1 for msg in should if trigger_fn(msg))
    fn = len(should) - tp
    fp = sum(1 for msg in should_not if trigger_fn(msg))
    tn = len(should_not) - fp

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "name": name,
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_negatives": [msg for msg in should if not trigger_fn(msg)],
        "false_positives": [msg for msg in should_not if trigger_fn(msg)],
    }


def main() -> int:
    results = [
        _eval_trigger("A2 related", is_related_trigger, A2_SHOULD_TRIGGER, A2_SHOULD_NOT_TRIGGER),
        _eval_trigger("A3 summary", is_summary_trigger, A3_SHOULD_TRIGGER, A3_SHOULD_NOT_TRIGGER),
        _eval_trigger("C2 operation", is_operation_trigger, C2_SHOULD_TRIGGER, C2_SHOULD_NOT_TRIGGER),
    ]

    print("| trigger | precision | recall | f1 | TP FP FN TN |")
    print("|---|---:|---:|---:|---:|")
    for result in results:
        print(
            f"| {result['name']} | {result['precision']:.2f} | {result['recall']:.2f} | "
            f"{result['f1']:.2f} | {result['tp']} {result['fp']} {result['fn']} {result['tn']} |"
        )

    all_pass = True
    for result in results:
        if result["precision"] < MIN_PRECISION or result["recall"] < MIN_RECALL:
            all_pass = False
        if result["false_positives"]:
            print(f"\n{result['name']} false positives:")
            for msg in result["false_positives"]:
                print(f"- {msg}")
        if result["false_negatives"]:
            print(f"\n{result['name']} false negatives:")
            for msg in result["false_negatives"]:
                print(f"- {msg}")

    if all_pass:
        print("\nPASS: push trigger precision/recall targets met.")
        return 0
    print("\nFAIL: push trigger precision/recall targets not met.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
