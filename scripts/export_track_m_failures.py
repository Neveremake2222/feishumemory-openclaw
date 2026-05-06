from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.cases.track_m import TRACK_M_CASES
from benchmarks.runner import BASELINE_MEMORY_ENABLED, run_track


def export_track_m_failures(
    output_path: str | Path = "benchmarks_runtime/track_m_failures.jsonl",
    *,
    baseline_mode: str = BASELINE_MEMORY_ENABLED,
) -> int:
    report = run_track(TRACK_M_CASES, "M", baseline_mode=baseline_mode)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for result in report.cases:
            if result.passed:
                continue
            record: dict[str, Any] = {
                "case_id": result.case_id,
                "track": result.track,
                "capability": result.capability,
                "baseline_mode": result.baseline_mode,
                "failure_type": result.failure_type,
                "errors": result.errors,
                "missing_memory": result.missing_memory or [],
                "wrong_memory_used": result.wrong_memory_used or [],
                "context_precision": result.context_precision,
                "context_recall": result.context_recall,
                "retrieval_latency_ms": result.retrieval_latency_ms,
                "transcript": result.transcript or {},
            }
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Export failing Track M cases to JSONL.")
    parser.add_argument(
        "--output",
        default="benchmarks_runtime/track_m_failures.jsonl",
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--baseline-mode",
        default=BASELINE_MEMORY_ENABLED,
        help="Benchmark baseline mode.",
    )
    args = parser.parse_args()

    count = export_track_m_failures(args.output, baseline_mode=args.baseline_mode)
    print(f"exported {count} failing Track M case(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
