"""Rule-based candidate extraction from Feishu events."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from memory_engine.implicit_preferences import build_observation_candidate, detect_implicit_preference_signals
from memory_engine.models import MemoryCandidate

from feishu_ingest.evidence import build_evidence
from feishu_ingest.models import FeishuEvent


_DECISION_PATTERNS = [
    re.compile(r"\u51b3\u5b9a"),  # decide / decided
    re.compile(r"\u7ed3\u8bba"),  # conclusion
    re.compile(r"\u91c7\u7528"),  # adopt
    re.compile(r"\u786e\u8ba4"),  # confirm
    re.compile(r"\u65b9\u6848"),  # plan / proposal
    re.compile(r"\u4e0d\u518d\u4f7f\u7528"),  # stop using
    re.compile(r"\u9009\u5b9a\u4e86"),  # chose / decided on
    re.compile(r"\u51b3\u5b9a\u7528"),  # decide to use
    re.compile(r"\u6700\u7ec8\u51b3\u5b9a"),  # final decision
    re.compile(r"\u6539\u7528"),  # switch to
    re.compile(r"\u6539\u4e3a"),  # change to
    re.compile(r"\u6539\u6210"),  # change to
    re.compile(r"\u9009\u62e9"),  # select / choose
    re.compile(r"\u653e\u5f03"),  # abandon / give up
    re.compile(r"\u6682\u7f13"),  # postpone / defer
    re.compile(r"\u540c\u610f"),  # agree
    re.compile(r"\u5206\u5de5"),  # responsibility split
    re.compile(r"\bdecid(?:e|ed|ing)\b", re.I),
    re.compile(r"\bconclusion\b", re.I),
    re.compile(r"\bfinal decision\b", re.I),
    re.compile(r"\bchose\b", re.I),
    re.compile(r"\bopted\b", re.I),
    re.compile(r"\bsettled on\b", re.I),
]

_QUESTION_PATTERNS = [
    re.compile(r"\u4ec0\u4e48"),    # \u4ec0\u4e48
    re.compile(r"\u600e\u4e48"),    # \u600e\u4e48
    re.compile(r"\u4e3a\u4ec0\u4e48"),  # \u4e3a\u4ec0\u4e48
    re.compile(r"\u8c01"),          # \u8c01
    re.compile(r"\u54ea"),          # \u54ea
    re.compile(r"\u5417"),          # \u5417
    re.compile(r"\u55ce"),          # \u5417 (variant)
    re.compile(r"\u5462"),          # \u5462
    re.compile(r"\u6765\u7740"),    # \u6765\u7740
    re.compile(r"\u5565"),          # \u5565
    re.compile(r"\u5417\uff1f"),    # \u5417\uff1f
    re.compile(r"\uff1f"),          # \uff1f
    re.compile(r"\?"),
    re.compile(r"\bwhat\b", re.I),
    re.compile(r"\bwhy\b", re.I),
    re.compile(r"\bwho\b", re.I),
    re.compile(r"\bhow\b", re.I),
    re.compile(r"\bwhen\b", re.I),
]

_TASK_PATTERNS = [
    re.compile(r"\u5b8c\u6210"),  # completed
    re.compile(r"\u8fdb\u884c\u4e2d"),  # in progress
    re.compile(r"\u963b\u585e"),  # blocked
    re.compile(r"\u5f85\u529e"),  # todo
    re.compile(r"\u5df2\u89e3\u51b3"),  # resolved
    re.compile(r"\u4e0b\u4e00\u6b65"),  # next step
    re.compile(r"\u8fdb\u5ea6"),  # progress
    re.compile(r"\u5f53\u524d\u8fdb\u5ea6"),  # current progress
    re.compile(r"\u5df2\u7d2f\u8ba1"),  # accumulated
    re.compile(r"\u5269\u4f59"),  # remaining
    re.compile(r"\u7b49\u5f85"),  # waiting
    re.compile(r"\u5df2\u6062\u590d"),  # resumed
    re.compile(r"\u5df2\u8bb0\u5f55"),  # recorded
    re.compile(r"\u5df2\u5b8c\u6210"),  # already completed
    re.compile(r"\u5f53\u524d\u72b6\u6001"),  # current status
    re.compile(r"\u8fdb\u5ea6\u5927\u7ea6"),  # progress about
    re.compile(r"\u7b49\u5f85\u4e2d"),  # waiting
    re.compile(r"\u505c\u6ede"),  # paused / suspended
    re.compile(r"\u63a8\u8fdf"),  # delayed
    re.compile(r"\u9884\u8ba1"),  # estimate
    re.compile(r"\u9884\u8ba1\u5b8c\u6210"),  # expected completion
    re.compile(r"\u98ce\u9669"),  # risk
    re.compile(r"\u5f71\u54cd"),  # impact
    re.compile(r"\u7f3a\u5c11"),  # missing
    re.compile(r"\u5f02\u5e38"),  # abnormal
    re.compile(r"\u64cd\u4f5c\u5931\u8d25"),  # operation failed
    re.compile(r"\u8986\u76d6\u7387"),  # \u8986\u76d6\u7387
    re.compile(r"\u63d0\u5347"),  # \u63d0\u5347
    re.compile(r"\u6bd4\u4f8b"),  # ratio / %
    re.compile(r"\u5192\u70df\u6d4b\u8bd5"),  # smoke test
    re.compile(r"\u4fee\u590d"),  # fix
    re.compile(r"\u95ed\u73af"),  # close loop
    re.compile(r"\bbug\b", re.I),
    re.compile(r"\bcompleted?\b", re.I),
    re.compile(r"\bblocked?\b", re.I),
    re.compile(r"\bin progress\b", re.I),
    re.compile(r"\bnext step\b", re.I),
    re.compile(r"\bcurrently\b", re.I),
    re.compile(r"\bdeadline\b", re.I),
]

_PREFERENCE_PATTERNS = [
    re.compile(r"\u6211\u5e0c\u671b"),  # I hope / want
    re.compile(r"\u6211\u66f4\u559c\u6b22"),  # I prefer
    re.compile(r"\u4f18\u5148"),  # prioritize
    re.compile(r"\u4ee5\u540e\u90fd"),  # always do this later
    re.compile(r"\u4ee5\u540e\u9ed8\u8ba4"),  # default to this later
    re.compile(r"\u9ed8\u8ba4"),  # default
    re.compile(r"\u4e0d\u8981\u518d"),  # stop doing
    re.compile(r"\u5efa\u8bae\u7528"),  # suggest using
    re.compile(r"\u63a8\u8350\u7528"),  # recommend using
    re.compile(r"\u4f18\u5148\u8003\u8651"),  # prioritize considering
    re.compile(r"\u503e\u5411"),  # tend toward
    re.compile(r"\u5efa\u8bae\u4e0d\u8981"),  # suggest not doing
    re.compile(r"\u901a\u5e38\u9009\u62e9"),  # usually choose
    re.compile(r"\u4ee5\u540e\u90fd\u7528"),  # use this from now on
    re.compile(r"\u4ee5\u540e\u5168\u90e8"),  # all from now on
    re.compile(r"\u9996\u9009"),  # first choice
    re.compile(r"\u9ed8\u8ba4\u60c5\u51b5"),  # default situation
    re.compile(r"\u8bf7\u7528"),  # \u8bf7\u7528
    re.compile(r"\u6bcf\u5468"),  # \u6bcf\u5468
    re.compile(r"\u6bcf\u5929"),  # \u6bcf\u5929
    re.compile(r"\u5b9a\u671f"),  # \u5b9a\u671f
    re.compile(r"\u56fa\u5b9a"),  # \u56fa\u5b9a
    re.compile(r"\bprefer\b", re.I),
    re.compile(r"\busually\b", re.I),
    re.compile(r"\bfrom now on\b", re.I),
    re.compile(r"\bavoid\b", re.I),
]

_RISK_PATTERNS = [
    re.compile(r"\u98ce\u9669"),
    re.compile(r"\u963b\u585e"),
    re.compile(r"\u5361\u70b9"),
    re.compile(r"\u5f71\u54cd"),
    re.compile(r"\u63a8\u8fdf"),
    re.compile(r"\u5ef6\u671f"),
    re.compile(r"\u4e0d\u7a33\u5b9a"),
    re.compile(r"\u7f3a(\u5c11|\u5931)?"),
    re.compile(r"\u5f02\u5e38"),
    re.compile(r"\u5931\u8d25"),
    re.compile(r"\bblock(?:ed|er)?\b", re.I),
    re.compile(r"\brisk\b", re.I),
]

_DEADLINE_RE = re.compile(
    r"(\u5468[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u65e5\u5929]|\u4eca\u5929|\u660e\u5929|\u672c\u5468[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u65e5\u5929]|\d{1,2}\s*\u6708\s*\d{1,2}\s*\u65e5)"
)
_WEEKDAY_TO_INDEX = {
    "\u4e00": 0,
    "\u4e8c": 1,
    "\u4e09": 2,
    "\u56db": 3,
    "\u4e94": 4,
    "\u516d": 5,
    "\u65e5": 6,
    "\u5929": 6,
}
_PROGRESS_RE = re.compile(r"(\d{1,3})\s*%")
_OWNER_RE = re.compile(r"([\u4e00-\u9fff]{2,4})\u8d1f\u8d23([^,，。；;\n]{2,30})")
_ACTION_MARKERS = (
    "\u4e0b\u4e00\u6b65",
    "\u9700\u8981",
    "\u8bf7",
    "\u8865\u9f50",
    "\u786e\u8ba4",
    "\u8ddf\u8fdb",
    "\u4fee\u590d",
)

# \u2500\u2500 Decision structure extraction \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

# Patterns that split decision text into components.
# Order matters: more specific (longer) patterns first.
_REASON_MARKERS = [
    r"\u56e0\u4e3a",       # \u56e0\u4e3a
    r"\u539f\u56e0\u662f", # \u539f\u56e0\u662f
    r"\u7406\u7531\u662f", # \u7406\u7531\u662f
    r"\u8003\u8651\u5230", # \u8003\u8651\u5230
    r"\u4e3b\u8981\u662f", # \u4e3b\u8981\u662f\u56e0\u4e3a
    r"\u56e0\u6b64",       # \u56e0\u6b64
    r"\bby\s+the\s+way\b",
    r"\bbecause\b",
    r"\breason\b",
    r"\bsince\b",
]
_REASON_RE = re.compile("|".join(f"({p})" for p in _REASON_MARKERS), re.I)

_ALT_MARKERS = [
    r"\u800c\u4e0d\u662f",  # \u800c\u4e0d\u662f
    r"\u800c\u975e",         # \u800c\u975e
    r"\u653e\u5f03\u4e86",  # \u653e\u5f03\u4e86
    r"\u4e0d\u9009",         # \u4e0d\u9009
    r"\u66ff\u6362\u4e3a",   # \u66ff\u6362\u4e3a
    r"\binstead\s+of\b",
    r"\brather\s+than\b",
    r"\bgive\s+up\b",
]
_ALT_RE = re.compile("|".join(f"({p})" for p in _ALT_MARKERS), re.I)

_CONCL_MARKERS = [
    r"\u6700\u7ec8\u51b3\u5b9a", # \u6700\u7ec8\u51b3\u5b9a
    r"\u7ec8\u4e8e",             # \u7ec8\u4e8e
    r"\u7ed3\u8bba\u662f",      # \u7ed3\u8bba\u662f
    r"\u603b\u7ed3",            # \u603b\u7ed3
    r"\u6240\u4ee5",             # \u6240\u4ee5
    r"\u5404\u65b9\u540c\u610f", # \u5404\u65b9\u540c\u610f
    r"\u5168\u5458\u4e00\u81f4\u540c\u610f", # \u5168\u5458\u4e00\u81f4\u540c\u610f
    r"\bfinal\s+decision\b",
    r"\bin\s+summary\b",
]
_CONCL_RE = re.compile("|".join(f"({p})" for p in _CONCL_MARKERS), re.I)


def _extract_decision_fields(text: str) -> dict[str, str]:
    """Parse structured decision fields from raw text.

    Extracts:
      - decision: the core decision statement
      - reason: the rationale (after \u539f\u56e0/\u56e0\u4e3a/etc.)
      - conclusion: the final binding conclusion (after \u6700\u7ec8/\u7ed3\u8bba/etc.)
      - alternatives: rejected options (after \u800c\u4e0d\u662f/\u653e\u5f03/etc.)

    Returns a dict with any fields found; all fields are optional.
    """
    fields: dict[str, str] = {}

    # Extract alternatives first (usually at the end of sentence)
    alt_m = _ALT_RE.search(text)
    if alt_m:
        fields["alternatives"] = alt_m.group(0) + text[alt_m.end():].strip()

    # Extract reason
    reason_m = _REASON_RE.search(text)
    if reason_m:
        # Split at reason marker: before = decision, after = reason
        before = text[:reason_m.start()].strip()
        after = text[reason_m.end():].strip()
        if before and before not in (".", "\uff0c", "\u3002", "\u3001", ",", " "):
            fields["decision"] = before
        if after and len(after) > 1:
            fields["reason"] = after
        return fields

    # Extract conclusion marker
    concl_m = _CONCL_RE.search(text)
    if concl_m:
        before = text[:concl_m.start()].strip()
        after = text[concl_m.end():].strip()
        if after and len(after) > 1:
            fields["conclusion"] = after
        if before and len(before) > 2:
            fields["decision"] = before
        return fields

    # No structure found \u2014 store raw text as decision
    fields["decision"] = text.strip()
    return fields


def _extract_project_management_fields(event: FeishuEvent) -> dict[str, object]:
    text = event.content
    fields: dict[str, object] = {}

    progress = _extract_progress(text)
    if progress:
        fields["progress"] = progress

    deadline = _extract_deadline(text)
    if deadline:
        fields["deadline"] = deadline
        valid_until = _deadline_to_valid_until(deadline, event.timestamp)
        if valid_until:
            fields["valid_until"] = valid_until
            fields["valid_until_source"] = "deadline"

    action = _extract_next_action(text)
    if action:
        fields["next_action"] = action

    if _has_current_risk(text):
        fields["risk"] = _truncate(text, 120)
        fields["risk_level"] = _risk_level(text)
        impact = _extract_impact(text)
        if impact:
            fields["impact"] = impact

    stakeholders = _extract_stakeholders(event)
    if stakeholders:
        fields["stakeholders"] = stakeholders

    customer = _extract_customer_context(event)
    if customer:
        fields["customer"] = customer

    status = _extract_status(text)
    if status:
        fields["status"] = status

    return fields


def _extract_progress(text: str) -> str | None:
    match = _PROGRESS_RE.search(text)
    if match:
        return f"{match.group(1)}%"
    return None


def _extract_deadline(text: str) -> str | None:
    match = _DEADLINE_RE.search(text)
    return match.group(1) if match else None


def _deadline_to_valid_until(deadline: str, event_timestamp: str) -> str | None:
    base = _parse_event_timestamp(event_timestamp)
    if base is None:
        return None

    target_date = None
    if deadline == "\u4eca\u5929":
        target_date = base.date()
    elif deadline == "\u660e\u5929":
        target_date = (base + timedelta(days=1)).date()
    elif "\u5468" in deadline:
        weekday_char = deadline[-1]
        target_weekday = _WEEKDAY_TO_INDEX.get(weekday_char)
        if target_weekday is None:
            return None
        delta_days = target_weekday - base.weekday()
        if delta_days < 0 and not deadline.startswith("\u672c\u5468"):
            delta_days += 7
        target_date = (base + timedelta(days=delta_days)).date()
    else:
        match = re.search(r"(\d{1,2})\s*\u6708\s*(\d{1,2})\s*\u65e5", deadline)
        if not match:
            return None
        month = int(match.group(1))
        day = int(match.group(2))
        year = base.year
        try:
            target_date = datetime(year, month, day, tzinfo=base.tzinfo).date()
        except ValueError:
            return None
        if target_date < base.date():
            try:
                target_date = datetime(year + 1, month, day, tzinfo=base.tzinfo).date()
            except ValueError:
                return None

    if target_date is None:
        return None
    return datetime.combine(target_date, time(23, 59, 59), tzinfo=base.tzinfo).isoformat()


def _parse_event_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def _extract_next_action(text: str) -> str | None:
    for marker in _ACTION_MARKERS:
        idx = text.find(marker)
        if idx < 0:
            continue
        if marker == "\u540c\u6b65" and idx <= 2:
            continue
        action = text[idx:].strip(" ，,。；;\n")
        action = re.split(r"[。；;\n]", action, maxsplit=1)[0].strip(" ，,")
        if action:
            return _truncate(action, 80)
    return None


def _extract_impact(text: str) -> str | None:
    marker = "\u5f71\u54cd"
    idx = text.find(marker)
    if idx >= 0:
        return _truncate(text[idx:].strip(" ，,。；;\n"), 80)
    if "\u9a8c\u6536" in text:
        return "\u53ef\u80fd\u5f71\u54cd\u9a8c\u6536"
    if "\u4e0d\u7a33\u5b9a" in text:
        return "\u53ef\u80fd\u5f71\u54cd\u7a33\u5b9a\u6027"
    return None


def _risk_level(text: str) -> str:
    if any(term in text for term in ("\u5468\u4e94", "P0", "\u4e25\u91cd", "\u963b\u585e", "\u5931\u8d25")):
        return "high"
    if any(term in text for term in ("\u5f71\u54cd", "\u7f3a", "\u5f02\u5e38", "P1")):
        return "medium"
    return "low"


def _has_current_risk(text: str) -> bool:
    if _count_matches(_RISK_PATTERNS, text) <= 0:
        return False
    if any(term in text for term in ("\u6ca1\u6709\u51fa\u73b0\u4efb\u4f55\u5f02\u5e38", "\u6ca1\u6709\u5f02\u5e38", "\u65e0\u5f02\u5e38")):
        return False
    resolved_terms = (
        "\u5df2\u89e3\u51b3",
        "\u987a\u5229\u901a\u8fc7",
        "\u4e0a\u7ebf\u6210\u529f",
        "\u5168\u90e8\u4fee\u590d",
        "\u5b8c\u6210\u4ea4\u4ed8",
        "\u5706\u6ee1\u7ed3\u675f",
    )
    active_terms = ("\u4f46", "\u8fd8", "\u9700\u8981", "\u5f71\u54cd", "\u963b\u585e", "\u5931\u8d25", "\u5f02\u5e38", "\u7f3a")
    if any(term in text for term in resolved_terms) and not any(term in text for term in active_terms):
        return False
    return True


def _extract_stakeholders(event: FeishuEvent) -> list[dict[str, str]]:
    stakeholders: list[dict[str, str]] = []
    sender_name = event.payload.get("sender_name")
    sender_role = event.payload.get("sender_role")
    if sender_name:
        item = {"name": str(sender_name)}
        if sender_role:
            item["role"] = str(sender_role)
        stakeholders.append(item)

    for match in _OWNER_RE.finditer(event.content):
        item = {"name": match.group(1), "responsibility": match.group(2).strip()}
        if item not in stakeholders:
            stakeholders.append(item)
    return stakeholders


def _extract_customer_context(event: FeishuEvent) -> str | None:
    if "\u5ba2\u6237" in event.content:
        return "\u5ba2\u6237\u4ea4\u4ed8\u573a\u666f"
    chat_title = event.payload.get("chat_title")
    if isinstance(chat_title, str) and "\u5ba2\u6237" in chat_title:
        return chat_title
    return None


def _extract_status(text: str) -> str | None:
    if any(term in text for term in ("\u963b\u585e", "\u5361\u70b9", "\u5f02\u5e38", "\u5931\u8d25")):
        return "blocked"
    if any(term in text for term in ("\u5b8c\u6210", "\u5df2\u89e3\u51b3", "\u5df2\u901a\u8fc7", "\u4e0a\u7ebf\u6210\u529f")):
        return "done"
    if any(term in text for term in ("\u8fdb\u884c\u4e2d", "\u5f53\u524d", "\u8fdb\u5ea6")):
        return "in_progress"
    return None


def _project_management_tags(content: dict[str, object]) -> list[str]:
    tags: list[str] = []
    for key, tag in (
        ("risk", "risk"),
        ("next_action", "next_action"),
        ("stakeholders", "stakeholder"),
        ("customer", "customer"),
        ("deadline", "deadline"),
        ("progress", "progress"),
    ):
        if key in content:
            tags.append(tag)
    status = content.get("status")
    if isinstance(status, str) and status not in tags:
        tags.append(status)
    return tags


def extract_candidates(
    event: FeishuEvent,
    *,
    scope: object | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
) -> list[MemoryCandidate]:
    """Extract memory candidates from a FeishuEvent using deterministic rules.

    Confidence:
      - strong match (2+ patterns hit): 0.8
      - weak match (1 pattern hit): 0.6
    """
    candidates: list[MemoryCandidate] = []

    decision_hits = _count_matches(_DECISION_PATTERNS, event.content)
    task_hits = _count_matches(_TASK_PATTERNS, event.content)
    preference_hits = _count_matches(_PREFERENCE_PATTERNS, event.content)

    is_question = _is_question(event.content)

    evidence = build_evidence(event)
    content_meta = _candidate_content(event, scope=scope, project_id=project_id, task_id=task_id)

    if decision_hits > 0 and not is_question:
        decision_content = dict(content_meta)
        decision_content.update(_extract_decision_fields(event.content))
        decision_content.update(_extract_project_management_fields(event))
        candidates.append(
            MemoryCandidate(
                memory_type="decision",
                title=_truncate(event.content, 50),
                summary=event.content,
                content=decision_content,
                importance=0.8,
                confidence=0.8 if decision_hits >= 2 else 0.6,
                evidence=evidence,
                tags=_project_management_tags(decision_content),
            )
        )

    if task_hits > 0 and not is_question:
        task_content = dict(content_meta)
        task_content.update(_extract_project_management_fields(event))
        candidates.append(
            MemoryCandidate(
                memory_type="task_status",
                title=_truncate(event.content, 50),
                summary=event.content,
                content=task_content,
                importance=0.7,
                confidence=0.8 if task_hits >= 2 else 0.6,
                evidence=evidence,
                tags=_project_management_tags(task_content),
            )
        )

    if preference_hits > 0 and _should_extract_preference(
        event.content,
        decision_hits=decision_hits,
        task_hits=task_hits,
    ):
        candidates.append(
            MemoryCandidate(
                memory_type="preference",
                title=_truncate(event.content, 50),
                summary=event.content,
                content=dict(content_meta),
                importance=0.6,
                confidence=0.8 if preference_hits >= 2 else 0.6,
                evidence=evidence,
            )
        )

    if preference_hits == 0:
        for signal in detect_implicit_preference_signals(event.content):
            candidates.append(
                build_observation_candidate(
                    signal=signal,
                    source_text=event.content,
                    content_meta=content_meta,
                    evidence=evidence,
                    observed_at=event.timestamp,
                )
            )

    return candidates


def _count_matches(patterns: list[re.Pattern[str]], text: str) -> int:
    return sum(1 for p in patterns if p.search(text))


def _is_question(text: str) -> bool:
    """Return True if the text looks like a question (should not trigger decision classification)."""
    return any(p.search(text) for p in _QUESTION_PATTERNS)


_FIRST_PERSON_PREFERENCE_PATTERNS = [
    re.compile(r"\u6211.*(\u559c\u6b22|\u5e0c\u671b|\u503e\u5411|\u66f4\u559c\u6b22)"),
    re.compile(r"\u4ee5\u540e.*(\u9ed8\u8ba4|\u90fd|\u5168\u90e8|\u7528)"),
    re.compile(r"\u4e0d\u8981\u518d"),
    re.compile(r"\u8bf7\u7528"),
    re.compile(r"\b(i|we)\s+(prefer|usually|want)\b", re.I),
    re.compile(r"\bfrom now on\b", re.I),
    re.compile(r"\bavoid\b", re.I),
]


def _should_extract_preference(text: str, *, decision_hits: int, task_hits: int) -> bool:
    """Avoid double-writing operational decisions as user preferences."""
    if any(pattern.search(text) for pattern in _FIRST_PERSON_PREFERENCE_PATTERNS):
        return True
    return decision_hits == 0 and task_hits == 0


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _candidate_content(
    event: FeishuEvent,
    *,
    scope: object | None,
    project_id: str | None,
    task_id: str | None,
) -> dict[str, str]:
    scope_value = getattr(scope, "value", scope) or event.scope.value
    content = {"scope": str(scope_value), "source_type": event.source_type}
    if project_id:
        content["project_id"] = project_id
    if task_id:
        content["task_id"] = task_id
    return content
