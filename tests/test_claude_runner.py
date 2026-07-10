"""ClaudeRunner: subprocess JSON calls with a mandatory llm_calls audit row per call."""

import json

from sqlalchemy import select

from app.models.llm_call import LlmCall
from app.services.claude_runner import ClaudeRunner


class FakeRunner(ClaudeRunner):
    """Overrides the subprocess boundary with canned (rc, stdout, stderr)."""

    def __init__(self, rc=0, result_event=None, stderr=b""):
        super().__init__()
        self._rc = rc
        self._stdout = json.dumps(result_event).encode() if result_event is not None else b""
        self._stderr = stderr
        self.seen_args: list[str] | None = None

    async def _run_cli(self, args, timeout):
        self.seen_args = args
        return self._rc, self._stdout, self._stderr


def ok_event(result_text: str) -> dict:
    return {
        "type": "result",
        "is_error": False,
        "result": result_text,
        "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 5},
        "total_cost_usd": 0.0012,
    }


async def get_only_call(db_factory) -> LlmCall:
    async with db_factory() as session:
        return (await session.execute(select(LlmCall))).scalars().one()


async def test_success_parses_fenced_json_and_records_call(db_factory):
    runner = FakeRunner(result_event=ok_event('```json\n{"fit_score": 82}\n```'))
    runner.audit_factory = db_factory
    payload = await runner.run_json(
        purpose="classify",
        system_prompt="you are a classifier",
        user_prompt="classify this",
        tier="fast",
    )
    assert payload == {"fit_score": 82}
    call = await get_only_call(db_factory)
    assert call.success is True
    assert call.purpose == "classify"
    assert call.input_tokens == 100 and call.output_tokens == 20
    assert float(call.cost_usd) == 0.0012
    # the DoD guardrails from the thesis-studio runner must be present
    assert "--strict-mcp-config" in runner.seen_args
    assert "--no-session-persistence" in runner.seen_args
    # a silent regression here would break EVERY call's result parsing
    assert "--output-format" in runner.seen_args
    assert runner.seen_args[runner.seen_args.index("--output-format") + 1] == "json"


async def test_cli_failure_returns_none_and_records_failure(db_factory):
    runner = FakeRunner(rc=1, stderr=b"boom")
    runner.audit_factory = db_factory
    payload = await runner.run_json(
        purpose="classify", system_prompt="s", user_prompt="u", tier="fast"
    )
    assert payload is None
    call = await get_only_call(db_factory)
    assert call.success is False
    assert "rc=1" in call.error


async def test_unparseable_payload_returns_none_but_keeps_usage(db_factory):
    runner = FakeRunner(result_event=ok_event("sorry, I cannot produce JSON"))
    runner.audit_factory = db_factory
    payload = await runner.run_json(
        purpose="classify", system_prompt="s", user_prompt="u", tier="fast"
    )
    assert payload is None
    call = await get_only_call(db_factory)
    assert call.success is False
    assert call.input_tokens == 100  # usage still audited


async def test_json_with_raw_newlines_in_strings_is_repaired(db_factory):
    raw = '{"variants": [{"text": "line one\nline two\n\ttabbed"}]}'
    runner = FakeRunner(result_event=ok_event(raw))
    runner.audit_factory = db_factory
    payload = await runner.run_json(
        purpose="draft", system_prompt="s", user_prompt="u", tier="standard"
    )
    assert payload == {"variants": [{"text": "line one\nline two\n\ttabbed"}]}


async def test_json_extracted_from_prose_wrapper(db_factory):
    runner = FakeRunner(result_event=ok_event('Here you go:\n{"a": 1}\nHope that helps!'))
    runner.audit_factory = db_factory
    payload = await runner.run_json(
        purpose="classify", system_prompt="s", user_prompt="u", tier="fast"
    )
    assert payload == {"a": 1}


async def test_stderr_secrets_scrubbed_from_error(db_factory):
    runner = FakeRunner(rc=1, stderr=b"auth failed: Bearer sk-ant-abc123xyz789 rejected")
    runner.audit_factory = db_factory
    await runner.run_json(purpose="classify", system_prompt="s", user_prompt="u", tier="fast")
    call = await get_only_call(db_factory)
    assert "sk-ant-" not in call.error
    assert "[redacted]" in call.error


async def test_trailing_prose_with_braces_still_parses(db_factory):
    runner = FakeRunner(result_event=ok_event('{"a": 1}\nHope that {helps}!'))
    runner.audit_factory = db_factory
    payload = await runner.run_json(
        purpose="classify", system_prompt="s", user_prompt="u", tier="fast"
    )
    assert payload == {"a": 1}


async def test_cancellation_writes_audit_row_then_reraises(db_factory):
    import asyncio

    import pytest

    class CancellingRunner(FakeRunner):
        async def _run_cli(self, args, timeout):
            raise asyncio.CancelledError

    runner = CancellingRunner()
    runner.audit_factory = db_factory
    with pytest.raises(asyncio.CancelledError):
        await runner.run_json(purpose="classify", system_prompt="s", user_prompt="u", tier="fast")
    call = await get_only_call(db_factory)
    assert call.success is False
    assert "cancelled" in call.error


async def test_audit_row_survives_pre_try_failures(db_factory):
    runner = FakeRunner()
    runner.audit_factory = db_factory
    payload = await runner.run_json(
        purpose="classify", system_prompt="s", user_prompt="u", tier="no_such_tier"
    )
    assert payload is None
    call = await get_only_call(db_factory)
    assert call.success is False
    assert "unknown tier" in call.error
