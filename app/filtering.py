"""Zero-cost gates that run before anything expensive (DESIGN §3.1 age, §3.2 keywords).

M0 keeps this to case-insensitive substring matching; the richer regex/language
prefilter and the LLM classifier arrive in M1.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime, timedelta

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(value: str) -> str:
    """Drop tags, unescape entities, collapse whitespace."""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()


def match_keywords(text: str, include: list[str], exclude: list[str]) -> list[str]:
    """Matched include terms, in include-list order. Any exclude hit vetoes the post."""
    lowered = text.lower()
    if any(term.lower() in lowered for term in exclude):
        return []
    return [term for term in include if term.lower() in lowered]


def is_fresh(created_at: datetime, max_age_minutes: int, now: datetime | None = None) -> bool:
    """DESIGN §3.1: skip posts older than max_age_minutes — stale threads convert poorly."""
    now = now or datetime.now(UTC)
    return now - created_at <= timedelta(minutes=max_age_minutes)
