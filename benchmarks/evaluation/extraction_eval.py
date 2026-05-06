"""Extraction accuracy evaluation — 30 labeled messages.

Measures precision, recall, F1 for decision/preference/task_status extraction.

Usage:
    python benchmarks/evaluation/extraction_eval.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from feishu_ingest.extractors import extract_candidates
from feishu_ingest.models import FeishuEvent
from memory_engine.models import Scope
from benchmarks.evaluation.test_data import TEST_MESSAGES


def _make_event(content: str) -> FeishuEvent:
    return FeishuEvent(
        source_type="message",
        source_ref="eval://test",
        source_url=None,
        actors=["eval_user"],
        timestamp="2026-05-03T12:00:00+08:00",
        content=content,
        scope=Scope.PROJECT,
        project_id="eval_project",
        user_id="eval_user",
    )


def main() -> None:
    print("=" * 70)
    print("提取准确率评测 (Extraction Accuracy Evaluation)")
    print("=" * 70)
    print(f"测试消息数: {len(TEST_MESSAGES)}")
    print()

    results = []
    for tc in TEST_MESSAGES:
        event = _make_event(tc["message"])
        candidates = extract_candidates(event)
        actual_types = [c.memory_type for c in candidates]
        expected = tc["expected"]

        # Determine pass/fail
        if expected is None:
            passed = len(actual_types) == 0
        else:
            passed = expected in actual_types

        # Check confidence
        conf_ok = True
        if expected is not None and passed:
            for c in candidates:
                if c.memory_type == expected and c.confidence < tc["confidence_min"]:
                    conf_ok = False

        results.append({
            "id": tc["id"],
            "message": tc["message"][:40],
            "expected": expected,
            "actual": actual_types,
            "passed": passed,
            "conf_ok": conf_ok,
        })

    # Print detail table
    print(f"| {'ID':<5} | {'期望':<14} | {'实际':<20} | {'结果':<4} | 消息 |")
    print(f"|-------|----------------|----------------------|------|------|")

    for r in results:
        exp_str = r["expected"] or "-"
        act_str = ", ".join(r["actual"]) if r["actual"] else "-"
        status = "PASS" if r["passed"] else "FAIL"
        print(f"| {r['id']:<5} | {exp_str:<14} | {act_str:<20} | {status:<4} | {r['message'][:30]}... |")

    # Compute metrics per type
    print()
    print("=" * 70)
    print("分类指标 (Per-Class Metrics)")
    print("=" * 70)

    types = ["decision", "preference", "task_status"]
    print(f"| {'类型':<14} | {'精确率':<8} | {'召回率':<8} | {'F1':<8} | {'样本数':<6} |")
    print(f"|----------------|----------|----------|--------|--------|")

    for t in types:
        tp = sum(1 for r in results if r["expected"] == t and t in r["actual"])
        fp = sum(1 for r in results if r["expected"] != t and t in r["actual"] and r["expected"] is not None)
        fn = sum(1 for r in results if r["expected"] == t and t not in r["actual"])
        # Also count as FP if expected=None but got this type
        fp += sum(1 for r in results if r["expected"] is None and t in r["actual"])

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        total = tp + fn

        print(f"| {t:<14} | {precision:<8.2f} | {recall:<8.2f} | {f1:<8.2f} | {total:<6} |")

    # Overall accuracy
    correct = sum(1 for r in results if r["passed"])
    total = len(results)
    print()
    print(f"总体准确率: {correct}/{total} = {correct/total*100:.1f}%")

    # Question filtering accuracy
    questions = [r for r in results if r["expected"] is None and any(
        kw in TEST_MESSAGES[[tc["id"] for tc in TEST_MESSAGES].index(r["id"])]["message"]
        for kw in ["什么", "怎么", "谁", "啥", "来着", "？"]
    )]
    if questions:
        q_correct = sum(1 for q in questions if q["passed"])
        print(f"问句过滤准确率: {q_correct}/{len(questions)} = {q_correct/len(questions)*100:.1f}%")

    all_pass = all(r["passed"] for r in results)
    print()
    if all_pass:
        print("全部通过 — 提取准确率验证成功!")
    else:
        fails = [r for r in results if not r["passed"]]
        print(f"存在 {len(fails)} 个失败项:")
        for f in fails:
            print(f"  {f['id']}: 期望={f['expected']}, 实际={f['actual']}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
