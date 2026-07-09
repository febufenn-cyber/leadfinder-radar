"""Alert formatting + notifier selection."""

from datetime import UTC, datetime, timedelta

from app.adapters.reddit_rss import RawPostData
from app.core.config import Settings
from app.notify import ConsoleNotifier, TelegramNotifier, format_alert, get_notifier


def make_post(**overrides) -> RawPostData:
    kwargs = dict(
        source="reddit",
        external_id="t3_1abc23",
        url="https://www.reddit.com/r/smallbusiness/comments/1abc23/x/",
        author_handle="/u/shopowner42",
        author_url="https://www.reddit.com/user/shopowner42",
        community="smallbusiness",
        title="Need a website <for> my & bakery",
        text="I need a website for my bakery, budget around $500.",
        created_at=datetime.now(UTC) - timedelta(minutes=12),
    )
    kwargs.update(overrides)
    return RawPostData(**kwargs)


def test_format_alert_contains_essentials_and_escapes_html():
    text = format_alert(make_post(), "robofox_web", ["need a website"])
    assert "robofox_web" in text
    assert "r/smallbusiness" in text
    assert "need a website" in text
    assert "https://www.reddit.com/r/smallbusiness/comments/1abc23/x/" in text
    assert "&lt;for&gt;" in text  # title html-escaped
    assert "<for>" not in text
    assert "12m ago" in text


def test_format_alert_truncates_long_body():
    text = format_alert(make_post(text="x" * 6000), "robofox_web", ["need a website"])
    assert len(text) <= 4000


def test_url_survives_pathological_escaping():
    """html.escape expands quotes 6x — the link must never be truncated off the card."""
    post = make_post(title='"' * 300, text='"' * 3000)
    text = format_alert(post, "robofox_web", ["need a website"])
    assert len(text) <= 4000
    assert "https://www.reddit.com/r/smallbusiness/comments/1abc23/x/" in text


def test_url_is_escaped_for_telegram_html():
    post = make_post(url="https://news.ycombinator.com/item?id=1&ref=x")
    text = format_alert(post, "robofox_web", ["need a website"])
    assert "id=1&amp;ref=x" in text
    assert "id=1&ref=x" not in text


async def test_console_notifier_always_succeeds():
    assert await ConsoleNotifier().send("hello") is True


def test_get_notifier_falls_back_to_console_without_token():
    settings = Settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="", _env_file=None)
    assert isinstance(get_notifier(settings), ConsoleNotifier)


def test_get_notifier_uses_telegram_when_configured():
    settings = Settings(TELEGRAM_BOT_TOKEN="123:abc", TELEGRAM_CHAT_ID="42", _env_file=None)
    notifier = get_notifier(settings)
    assert isinstance(notifier, TelegramNotifier)
