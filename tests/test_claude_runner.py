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


async def test_success_parses_fenced_json_and_records_call(db_session):
    runner = FakeRunner(result_event=ok_event('```json\n{"fit_score": 82}\n```'))
    payload = await runner.run_json(
        purpose="classify",
        system_prompt="you are a classifier",
        user_prompt="classify this",
        tier="fast",
        session=db_session,
    )
    assert payload == {"fit_score": 82}
    call = (await db_session.execute(select(LlmCall))).scalars().one()
    assert call.success is True
    assert call.purpose == "classify"
    assert call.input_tokens == 100 and call.output_tokens == 20
    assert float(call.cost_usd) == 0.0012
    # the DoD guardrails from the thesis-studio runner must be present
    assert "--strict-mcp-config" in runner.seen_args
    assert "--no-session-persistence" in runner.seen_args


async def test_cli_failure_returns_none_and_records_failure(db_session):
    runner = FakeRunner(rc=1, stderr=b"boom")
    payload = await runner.run_json(
        purpose="classify",
        system_prompt="s",
        user_prompt="u",
        tier="fast",
        session=db_session,
    )
    assert payload is None
    call = (await db_session.execute(select(LlmCall))).scalars().one()
    assert call.success is False
    assert "rc=1" in call.error


async def test_unparseable_payload_returns_none_but_keeps_usage(db_session):
    runner = FakeRunner(result_event=ok_event("sorry, I cannot produce JSON"))
    payload = await runner.run_json(
        purpose="classify", system_prompt="s", user_prompt="u", tier="fast", session=db_session
    )
    assert payload is None
    call = (await db_session.execute(select(LlmCall))).scalars().one()
    assert call.success is False
    assert call.input_tokens == 100  # usage still audited


async def test_json_extracted_from_prose_wrapper(db_session):
    runner = FakeRunner(result_event=ok_event('Here you go:\n{"a": 1}\nHope that helps!'))
    payload = await runner.run_json(
        purpose="classify", system_prompt="s", user_prompt="u", tier="fast", session=db_session
    )
    assert payload == {"a": 1}
