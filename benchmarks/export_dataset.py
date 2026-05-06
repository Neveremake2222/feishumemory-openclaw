"""Export benchmark case definitions to JSONL.

Usage:
    python -m benchmarks.export_dataset benchmarks_runtime/benchmark_cases.jsonl
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from benchmarks.runner import _benchmark_tracks
from benchmarks.structures import BenchmarkCase


def benchmark_cases() -> list[BenchmarkCase]:
    """Return all benchmark cases in runner order."""
    cases: list[BenchmarkCase] = []
    for _, _, track_cases in _benchmark_tracks():
        cases.extend(track_cases)
    return cases


def case_to_record(case: BenchmarkCase) -> dict[str, Any]:
    """Convert a benchmark case into a stable JSON-compatible record."""
    raw = _to_jsonable(asdict(case))
    complexity = {
        "reasoning": raw["complexity_reasoning"],
        "tool": raw["complexity_tool"],
        "interaction": raw["complexity_interaction"],
    }
    return {
        "case_id": raw["case_id"],
        "track": raw["track"],
        "capability": raw["capability"],
        "description": raw["description"],
        "direction": raw["direction"],
        "complexity": complexity,
        "memory_types": raw["memory_types"],
        "memory_target": raw["memory_target"] or _infer_memory_target(raw),
        "difficulty": raw["difficulty"] or _infer_difficulty(complexity),
        "source_anchor": raw["source_anchor"] or raw["spec_ref"] or f"benchmark:{raw['case_id']}",
        "baseline_mode": raw["baseline_mode"],
        "setup_events": raw["setup_events"],
        "setup_memories": raw["setup_memories"],
        "workflow_outcomes": raw["workflow_outcomes"],
        "distractor_turns": raw["distractor_turns"],
        "interference": raw["interference"],
        "recalls": raw["recalls"],
        "evaluation_task": raw["evaluation_task"] or _infer_evaluation_task(raw),
        "expected_behavior": raw["expected_behavior"] or _infer_expected_behavior(raw),
        "expected_titles": raw["expected_titles"],
        "forbidden_titles": raw["forbidden_titles"],
        "expected_memory_ids": raw["expected_memory_ids"],
        "forbidden_memory_ids": raw["forbidden_memory_ids"],
        "expected_count_range": raw["expected_count_range"],
        "expect_zero_results": raw["expect_zero_results"],
        "assertions": raw["assertions"],
        "event_assertions": raw["event_assertions"],
        "ground_truth": raw["ground_truth"] or _infer_ground_truth(raw),
        "scoring_rubric": raw["scoring_rubric"] or _infer_scoring_rubric(raw),
        "memory_type_dimension": raw.get("memory_type_dimension", ""),
        "recall_intent": raw.get("recall_intent", ""),
        "scale_level": raw.get("scale_level"),
        "query_set": raw.get("query_set", []),
        "agent_config": raw.get("agent_config"),
        "spec_ref": raw["spec_ref"],
        "notes": raw["notes"],
    }


def export_cases_jsonl(path: str | Path, cases: list[BenchmarkCase] | None = None) -> int:
    """Write benchmark case records to JSONL and return the number exported."""
    selected = cases if cases is not None else benchmark_cases()
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in selected:
            handle.write(json.dumps(case_to_record(case), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return len(selected)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    return value


def _infer_memory_target(raw: dict[str, Any]) -> str:
    memory_types = raw.get("memory_types") or []
    if len(memory_types) == 1:
        return str(memory_types[0])
    if memory_types:
        return "mixed:" + ",".join(str(item) for item in memory_types)
    track = str(raw.get("track") or "").strip()
    track_defaults = {
        "A": "dialogue_memory",
        "B": "task_decision",
        "C": "preference",
        "D": "structured_memory",
        "E": "event",
        "F": "workflow_trace",
        "G": "governance",
        "H": "self_improvement",
        "I": "agent_memory",
        "J": "retrieval_quality",
        "J-gen": "retrieval_quality",
        "K": "scale",
        "L": "agent_task",
        "M": "project_management_business_value",
    }
    return track_defaults.get(track, "memory")


def _infer_difficulty(complexity: dict[str, Any]) -> str:
    values = [str(complexity.get(name) or "low") for name in ("reasoning", "tool", "interaction")]
    if "high" in values:
        return "hard"
    if values.count("medium") >= 2:
        return "medium"
    if "medium" in values:
        return "medium"
    return "easy"


def _infer_evaluation_task(raw: dict[str, Any]) -> str:
    recalls = raw.get("recalls") or []
    queries = [str(item.get("query")) for item in recalls if isinstance(item, dict) and item.get("query")]
    if queries:
        return " | ".join(queries)
    return str(raw.get("description") or raw.get("capability") or raw.get("case_id"))


def _infer_expected_behavior(raw: dict[str, Any]) -> str:
    parts: list[str] = []
    if raw.get("expect_zero_results"):
        parts.append("Return zero recall results when no grounded memory exists.")
    expected_titles = raw.get("expected_titles") or []
    if expected_titles:
        parts.append("Recall expected titles: " + "; ".join(str(title) for title in expected_titles))
    forbidden_titles = raw.get("forbidden_titles") or []
    if forbidden_titles:
        parts.append("Exclude forbidden titles: " + "; ".join(str(title) for title in forbidden_titles))
    if raw.get("assertions"):
        parts.append("Satisfy result assertions.")
    if raw.get("event_assertions"):
        parts.append("Satisfy event/governance assertions.")
    if raw.get("expected_count_range") is not None:
        parts.append(f"Return result count within {raw['expected_count_range']}.")
    if not parts:
        parts.append(str(raw.get("description") or "Satisfy the benchmark case assertions."))
    return " ".join(parts)


def _infer_ground_truth(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "expected_titles": raw.get("expected_titles") or [],
        "forbidden_titles": raw.get("forbidden_titles") or [],
        "expected_count_range": raw.get("expected_count_range"),
        "expect_zero_results": bool(raw.get("expect_zero_results")),
        "assertions": raw.get("assertions") or [],
        "event_assertions": raw.get("event_assertions") or [],
    }


def _infer_scoring_rubric(raw: dict[str, Any]) -> dict[str, Any]:
    criteria: list[dict[str, Any]] = []
    if raw.get("expected_titles"):
        criteria.append({
            "name": "context_recall",
            "weight": 0.4,
            "description": "All expected memory titles must be selected.",
        })
    if raw.get("forbidden_titles") or raw.get("expect_zero_results"):
        criteria.append({
            "name": "context_precision",
            "weight": 0.3,
            "description": "Forbidden or unsupported memories must not be selected.",
        })
    if raw.get("assertions"):
        criteria.append({
            "name": "result_assertions",
            "weight": 0.2,
            "description": "Structured recall assertions must pass.",
        })
    if raw.get("event_assertions"):
        criteria.append({
            "name": "event_assertions",
            "weight": 0.2,
            "description": "Event, workflow, governance, or self-improvement assertions must pass.",
        })
    if raw.get("expected_count_range") is not None:
        criteria.append({
            "name": "bounded_context",
            "weight": 0.1,
            "description": "Returned context count must stay within the expected range.",
        })
    if not criteria:
        criteria.append({
            "name": "case_pass",
            "weight": 1.0,
            "description": "The benchmark case must pass without errors.",
        })
    return {
        "score_type": "binary_with_diagnostics",
        "pass_threshold": 1.0,
        "criteria": criteria,
    }


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    output = Path(args[0]) if args else Path("benchmarks_runtime") / "benchmark_cases.jsonl"
    count = export_cases_jsonl(output)
    print(f"Exported {count} benchmark cases to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
