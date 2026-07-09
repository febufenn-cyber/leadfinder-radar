"""Keyword prefilter + age gate (DESIGN §3.1/§3.2, M0 substring version)."""

from datetime import UTC, datetime, timedelta

from app.filtering import is_fresh, match_keywords, strip_html


def test_include_match_case_insensitive():
    assert match_keywords("I Need A Website for my shop", ["need a website"], []) == [
        "need a website"
    ]


def test_no_match_returns_empty():
    assert match_keywords("growing my gym to 200 members", ["need a website"], []) == []


def test_exclude_vetoes_all_matches():
    text = "need a website but only for free please"
    assert match_keywords(text, ["need a website"], ["for free"]) == []


def test_multiple_includes_all_reported():
    text = "Need a website — specifically a landing page for my shop"
    matched = match_keywords(text, ["need a website", "landing page"], [])
    assert matched == ["need a website", "landing page"]


def test_strip_html():
    html = '<div class="md"><p>I need a &amp; website</p></div>'
    assert strip_html(html) == "I need a & website"


def test_is_fresh_within_window():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    assert is_fresh(now - timedelta(minutes=179), 180, now=now)
    assert not is_fresh(now - timedelta(minutes=181), 180, now=now)
