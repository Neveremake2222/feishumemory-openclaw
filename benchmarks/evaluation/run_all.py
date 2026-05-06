"""Benchmark evaluation runner — runs all evaluations and generates a report.

Usage:
    python benchmarks/evaluation/run_all.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Benchmark entry points
# ---------------------------------------------------------------------------
BENCHMARKS = [
    {
        "name": "提取准确率 (Extraction Accuracy)",
        "script": "benchmarks/evaluation/extraction_eval.py",
        "description": "30 条预设消息的决策/偏好/进度分类准确率",
    },
    {
        "name": "推送误报率 (Push False-Positive Rate)",
        "script": "benchmarks/evaluation/push_eval.py",
        "description": "A2/A3/C2 触发词精确率和召回率",
    },
    {
        "name": "抗干扰测试 (Anti-Interference)",
        "script": "benchmarks/interference_benchmark.py",
        "description": "噪声干扰下关键记忆的 Top-1/Top-3 召回命中率",
    },
    {
        "name": "矛盾更新测试 (Contradiction Update)",
        "script": "benchmarks/contradiction_demo.py",
        "description": "新旧决策冲突时的 supersede 机制验证",
    },
    {
        "name": "召回基线 (Recall Baseline)",
        "script": "benchmarks/recall_baseline.py",
        "description": "不同规模下的召回延迟 P50/P95",
    },
    {
        "name": "作用域隔离 (Scope Isolation)",
        "script": "benchmarks/scope_filter_benchmark.py",
        "description": "跨项目记忆零泄漏验证",
    },
    {
        "name": "检索质量 (Retrieval Quality - Track J)",
        "script": "benchmarks/retrieval_eval.py",
        "description": "Track J: Recall@K / NDCG@10 / MRR 分层检索评测",
    },
    {
        "name": "规模扩展 (Scale Benchmark - Track K)",
        "script": "benchmarks/scale_eval.py",
        "description": "Track K: 100/1k/5k/10k 规模下延迟和衰减评测",
    },
]


def _run(script: str, description: str) -> dict:
    """Run a benchmark script and capture output."""
    path = REPO_ROOT / script
    result = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "name": description,
        "script": script,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _extract_key_metric(output: str, patterns: list[tuple[str, str]]) -> dict:
    """Extract named metrics from output text using regex patterns."""
    import re
    metrics = {}
    for key, pattern in patterns:
        m = re.search(pattern, output)
        if m:
            try:
                metrics[key] = float(m.group(1).replace("%", "").strip())
            except (ValueError, IndexError):
                metrics[key] = m.group(1).strip()
    return metrics


def main() -> None:
    print("=" * 70)
    print("记忆引擎评测报告")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()

    print(f"| {'#':<3} | {'评测项目':<30} | {'状态':<6} |")
    print(f"|-----|------------------------------|--------|")

    all_pass = True
    report_lines = []

    for i, bench in enumerate(BENCHMARKS, 1):
        print(f"| {i:<3} | {bench['name']:<30} | 运行中... |", end="\r")
        result = _run(bench["script"], bench["name"])

        if result["returncode"] == 0:
            status = "PASS"
        else:
            status = "FAIL"
            all_pass = False

        print(f"| {i:<3} | {bench['name']:<30} | {status:<6} |")

        # Extract key metrics
        stdout = result["stdout"]
        metrics = {}

        if "提取准确率" in bench["name"]:
            m = _extract_key_metric(stdout, [
                ("extraction_accuracy", r"总体准确率.*?(\d+\.\d+)%"),
            ])
            metrics.update(m)
        elif "抗干扰" in bench["name"]:
            m = _extract_key_metric(stdout, [
                ("top1_50", r"\|\s*50\s*\|.*?(\d+)%"),
                ("top3_50", r"\|.*?50.*?\|.*?\|.*?(\d+)%"),
            ])
            metrics.update(m)
        elif "矛盾更新" in bench["name"]:
            m = _extract_key_metric(stdout, [
                ("contradiction_pass", r"(全部通过|全部 PASS|SUCCESS)"),
            ])
            metrics.update(m)

        report_lines.append({
            "bench": bench,
            "result": result,
            "metrics": metrics,
        })

    print()
    print("=" * 70)
    print("评测摘要")
    print("=" * 70)
    print()

    # Print summary table
    print(f"| {'评测项目':<30} | {'结论':<20} | {'状态':<6} |")
    print(f"|------------------------------|----------------------|--------|")
    for item in report_lines:
        bench = item["bench"]
        result = item["result"]
        metrics = item["metrics"]

        if result["returncode"] == 0:
            status = "PASS"
            detail = "验证成功"
        else:
            status = "FAIL"
            detail = "存在问题"

        print(f"| {bench['name']:<30} | {detail:<20} | {status:<6} |")

    print()
    print("=" * 70)
    print("详细输出")
    print("=" * 70)
    for item in report_lines:
        bench = item["bench"]
        result = item["result"]
        print()
        print(f"--- {bench['name']} ---")
        # Print stdout, skipping the first few header lines to avoid repetition
        lines = result["stdout"].strip().split("\n")
        print("\n".join(lines[-30:]))  # Last 30 lines of each output

    print()
    print("=" * 70)
    print("竞赛要求验证")
    print("=" * 70)
    print()
    print("| 竞赛要求 | 评测脚本 | 状态 |")
    print("|----------|----------|------|")
    # Derive competition verification results from actual benchmark runs
    COMPETITION_MAP = {
        "抗干扰测试": "benchmarks/interference_benchmark.py",
        "矛盾更新测试": "benchmarks/contradiction_demo.py",
        "提取准确率": "benchmarks/evaluation/extraction_eval.py",
        "推送误报率": "benchmarks/evaluation/push_eval.py",
        "作用域隔离": "benchmarks/scope_filter_benchmark.py",
    }
    for item in report_lines:
        bench = item["bench"]
        result = item["result"]
        script = bench["script"]
        # Only show rows that are in the competition verification table
        match_name = next((k for k, v in COMPETITION_MAP.items() if v == script), None)
        if match_name is None:
            continue
        status = "PASS" if result["returncode"] == 0 else "FAIL"
        print(f"| {match_name} | {script} | {status} |")
    print()

    if all_pass:
        print("全部评测通过 — 系统满足竞赛评测要求!")
    else:
        print("部分评测失败 — 请查看上方详情")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
