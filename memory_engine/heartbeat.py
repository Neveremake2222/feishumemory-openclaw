"""Background heartbeat maintenance for memory-engine.

Usage:
    from memory_engine.heartbeat import run_once, run_periodic
    from memory_engine import MemoryEngine

    engine = MemoryEngine("memory_engine.sqlite3")

    # Single maintenance pass
    result = run_once(engine)

    # Periodic (blocking loop, 30min interval)
    run_periodic(engine, interval_seconds=1800)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from memory_engine.engine import MemoryEngine

logger = logging.getLogger(__name__)


def run_once(engine: MemoryEngine, source_resolver: Any | None = None) -> dict[str, Any]:
    """Execute one maintenance cycle: compact + review + validate_sources.

    Returns a dict with results from each step.
    Safe to call from any thread (engine uses SQLite with check_same_thread=False).
    """
    result: dict[str, Any] = {}

    try:
        compact_result = engine.compact()
        result["compact"] = compact_result
        logger.info(
            "heartbeat compact: archived=%d, merged=%d, expired=%d",
            compact_result.get("archived_count", 0),
            compact_result.get("merged_count", 0),
            compact_result.get("expired_count", 0),
        )
    except Exception as exc:
        result["compact_error"] = str(exc)
        logger.exception("heartbeat compact failed")

    try:
        review_result = engine.review()
        result["review"] = review_result
        logger.info(
            "heartbeat review: promotions=%d, demotions=%d",
            review_result.get("promotion_count", 0),
            len(review_result.get("demotions", [])),
        )
    except Exception as exc:
        result["review_error"] = str(exc)
        logger.exception("heartbeat review failed")

    try:
        validation_result = engine.validate_sources(source_resolver)
        result["validate_sources"] = validation_result
        status_counts: dict[str, int] = {}
        for item in validation_result:
            status = item.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        result["source_validation_summary"] = status_counts
        logger.info("heartbeat validate_sources: %s", status_counts)
    except Exception as exc:
        result["validate_sources_error"] = str(exc)
        logger.exception("heartbeat validate_sources failed")

    result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return result


def run_periodic(
    engine: MemoryEngine,
    interval_seconds: int = 1800,
    max_cycles: int = 0,
    source_resolver: Any | None = None,
) -> list[dict[str, Any]]:
    """Run heartbeat in a blocking loop.

    Args:
        engine: MemoryEngine instance.
        interval_seconds: Seconds between cycles (default 30 min).
        max_cycles: Max number of cycles. 0 = run forever until interrupted.

    Returns:
        List of results from each cycle.
    """
    results: list[dict[str, Any]] = []
    cycle = 0

    logger.info("heartbeat starting: interval=%ds, max_cycles=%s", interval_seconds, max_cycles or "unlimited")

    while True:
        cycle += 1
        logger.info("heartbeat cycle %d starting", cycle)

        result = run_once(engine, source_resolver=source_resolver)
        result["cycle"] = cycle
        results.append(result)

        if max_cycles > 0 and cycle >= max_cycles:
            logger.info("heartbeat reached max_cycles=%d, stopping", max_cycles)
            break

        try:
            time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("heartbeat interrupted, stopping after %d cycles", cycle)
            break

    return results
