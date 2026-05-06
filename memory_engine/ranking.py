from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def _is_chinese_segment(text: str) -> bool:
    return all(_is_cjk(ch) or not ch.isalnum() for ch in text)


def _split_segments(text: str) -> list[str]:
    """Split text into runs of Chinese characters vs. non-Chinese runs."""
    segments: list[str] = []
    current: list[str] = []
    current_is_chinese = False

    for ch in text:
        is_cjk = _is_cjk(ch)
        if is_cjk == current_is_chinese:
            current.append(ch)
        else:
            if current:
                segments.append("".join(current))
            current = [ch]
            current_is_chinese = is_cjk
    if current:
        segments.append("".join(current))
    return segments


def _tokenize(text: str) -> list[str]:
    # C1: add bigrams for consecutive Chinese characters (cc-soul启发)
    tokens: list[str] = []
    for seg in _split_segments(text):
        if _is_chinese_segment(seg):
            chars = [c for c in seg if _is_cjk(c)]
            if chars:
                tokens.extend(chars)  # individual CJK characters
                for i in range(len(chars) - 1):
                    tokens.append(chars[i] + chars[i + 1])  # bigrams
        else:
            # Same normalization as original tokenizer: strip punctuation, lowercase, split
            normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in seg)
            tokens.extend(part for part in normalized.split() if part)
    return [t for t in tokens if t]


def _token_counts(text: str) -> Counter:
    return Counter(_tokenize(text))


def _memory_text(row: sqlite3.Row) -> str:
    """Extract searchable text from a memory row (title + summary only).

    content_json is excluded — it contains structural fields like scope/project_id
    that dilute BM25 scoring rather than contribute meaningful tokens.
    """
    return " ".join([row["title"], row["summary"]])


def _get_row_tokens(row: sqlite3.Row) -> list[str]:
    """Get pre-computed tokens from row, falling back to on-the-fly tokenize."""
    token_list_json = row["token_list"] if "token_list" in row.keys() else None
    if token_list_json:
        try:
            return json.loads(token_list_json)
        except (json.JSONDecodeError, TypeError):
            pass
    return _tokenize(_memory_text(row))


def _overlap_score(query: str, text: str) -> float:
    q = _token_counts(query)
    t = _token_counts(text)
    if not q or not t:
        return 0.0
    overlap = sum(min(q[token], t[token]) for token in q)
    return overlap / max(sum(q.values()), 1)


_BM25_K1 = 1.5
_BM25_B = 0.75


def _compute_lexical_stats(all_rows: list[sqlite3.Row]) -> dict[str, Any]:
    """Compute BM25 corpus stats over ALL active memories."""
    n = len(all_rows)
    if n == 0:
        return {"n": 0, "avgdl": 1.0, "df": Counter()}

    total_len = 0
    df: Counter = Counter()
    for row in all_rows:
        tokens = _get_row_tokens(row)
        total_len += len(tokens)
        for token in set(tokens):
            df[token] += 1

    return {"n": n, "avgdl": total_len / n, "df": df}


def _lexical_score(query: str, row: sqlite3.Row, stats: dict[str, Any]) -> float:
    """BM25 score for a single query-document pair."""
    n = stats["n"]
    avgdl = stats["avgdl"]
    df = stats["df"]
    if n == 0:
        return 0.0

    doc_tokens = _get_row_tokens(row)
    doc_len = len(doc_tokens)
    doc_tf = Counter(doc_tokens)

    score = 0.0
    for qt in _tokenize(query):
        if qt not in doc_tf:
            continue
        tf = doc_tf[qt]
        d_freq = df.get(qt, 0)
        idf = math.log((n - d_freq + 0.5) / (d_freq + 0.5) + 1.0)
        tf_component = (tf * (_BM25_K1 + 1.0)) / (
            tf + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * doc_len / avgdl)
        )
        score += idf * tf_component

    return score


_HALF_LIFE_HOURS: dict[str, float] = {
    "decision": 60 * 24,
    "task_status": 14 * 24,
    "preference": 90 * 24,
    "habit_rule": 120 * 24,
}
_HALF_LIFE_DEFAULT_HOURS = 30 * 24
_DEFAULT_LOCAL_TZ = timezone(timedelta(hours=8))


def _freshness_score(timestamp: str, memory_type: str | None = None) -> float:
    try:
        dt = datetime.fromisoformat(timestamp)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_DEFAULT_LOCAL_TZ)
    age_hours = max((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 0.0)
    half_life = _HALF_LIFE_HOURS.get(memory_type or "", _HALF_LIFE_DEFAULT_HOURS)
    return 0.5 ** (age_hours / half_life)


def _mmr_diversity(
    scored: list[dict[str, Any]],
    limit: int,
    lambda_param: float = 0.7,
) -> list[dict[str, Any]]:
    if not scored or limit <= 0:
        return []

    remaining = list(range(len(scored)))
    result: list[dict[str, Any]] = []
    selected_tokens: Counter = Counter()

    first_idx = max(remaining, key=lambda idx: scored[idx]["score"])
    remaining.remove(first_idx)
    result.append(scored[first_idx])
    selected_tokens.update(_tokenize(scored[first_idx].get("title", "") + " " + scored[first_idx].get("summary", "")))

    while remaining and len(result) < limit:
        best_idx = -1
        best_mmr = float("-inf")
        for idx in remaining:
            rel = scored[idx]["score"]
            candidate_tokens = _token_counts(scored[idx].get("title", "") + " " + scored[idx].get("summary", ""))
            div = 0.0
            if selected_tokens and candidate_tokens:
                overlap = sum(min(selected_tokens[t], candidate_tokens[t]) for t in candidate_tokens)
                div = overlap / max(sum(candidate_tokens.values()), 1)
            mmr = lambda_param * rel - (1 - lambda_param) * div
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = idx
        if best_idx < 0:
            break
        remaining.remove(best_idx)
        result.append(scored[best_idx])
        selected_tokens.update(_tokenize(scored[best_idx].get("title", "") + " " + scored[best_idx].get("summary", "")))

    while remaining and len(result) < limit:
        next_idx = max(remaining, key=lambda idx: scored[idx]["score"])
        remaining.remove(next_idx)
        result.append(scored[next_idx])

    return result


_WEIGHT_RELEVANCE = 0.4
_WEIGHT_FRESHNESS = 0.2
_WEIGHT_IMPORTANCE = 0.25
_WEIGHT_CONFIDENCE = 0.15

_MIN_SCORE = 0.35
_TOP_K = 10
