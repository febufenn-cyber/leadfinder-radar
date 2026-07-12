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


def test_signal_opens_gate_without_topic_include():
    # "someone to build … will pay" has no website include, but the hire/build
    # signal is a demand opener — it must reach the classifier, not get dropped.
    text = "Need someone to build an online store for my shop, paid project"
    matched = match_keywords(
        text, ["need a website"], [], signals=["someone to build", "paid project"]
    )
    assert matched == ["someone to build", "paid project"]


def test_signals_default_is_backward_compatible():
    # Omitting signals must behave exactly as the 3-arg call always did.
    assert match_keywords("need a website now", ["need a website"], []) == ["need a website"]
    assert match_keywords("nothing relevant here", ["need a website"], []) == []


def test_exclude_still_vetoes_a_signal_match():
    # A freebie post is dropped even when a hire/build signal is present.
    text = "someone to build my site for free please"
    assert match_keywords(text, ["need a website"], ["for free"], signals=["someone to build"]) == []


def test_include_and_signal_both_reported_deduped():
    text = "Need a website — looking to hire a developer for it"
    matched = match_keywords(
        text, ["need a website"], [], signals=["hire a developer", "need a website"]
    )
    # include matches first, signals appended, no duplicate of the shared phrase
    assert matched == ["need a website", "hire a developer"]


def test_strip_html():
    html = '<div class="md"><p>I need a &amp; website</p></div>'
    assert strip_html(html) == "I need a & website"


def test_is_fresh_within_window():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    assert is_fresh(now - timedelta(minutes=179), 180, now=now)
    assert not is_fresh(now - timedelta(minutes=181), 180, now=now)
