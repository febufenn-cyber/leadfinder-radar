"""Claude subprocess runner — ported from Thesis Studio's claude_service.py (DESIGN §4).

Calls the Claude Code CLI (`claude -p`) as a subprocess; auth is the CLI's own
Max OAuth session (login once per host with `claude /login`). The flag stack is
inherited verbatim from Thesis Studio, where it was tuned the hard way:

- `--strict-mcp-config` + empty config strips this host's personal MCP servers
  out of the prompt prefix.
- `--system-prompt-file` (replace, not append) — append mode glues onto Claude
  Code's full system prompt and costs ~5x more cache_creation tokens.
- `--no-session-persistence` keeps the CLI from writing session state to disk.

Contract difference from Thesis Studio: `run_json` NEVER raises. Every call —
success or failure — writes an llm_calls audit row (the M-milestone DoD), and
failures return None so the pipeline can degrade gracefully (e.g. alert
unscored rather than lose a lead).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models.llm_call import LlmCall

log = logging.getLogger(__name__)

_EMPTY_MCP_CONFIG = str(Path(__file__).parent / "empty_mcp_config.json")
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")
# CLI stderr can theoretically echo credentials on auth failures — scrub before storing.
_SECRET_RE = re.compile(r"(sk-ant-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+)")


def _escape_ctrl_in_strings(s: str) -> str:
    """Escape raw newlines/tabs INSIDE string literals — LLMs writing multi-line
    reply text emit them constantly, and json.loads rejects them."""
    out: list[str] = []
    in_str = esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            elif ch in "\n\r\t":
                out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch])
                continue
        elif ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


def _extract_json(text: str) -> dict | None:
    """Best-effort JSON object extraction: strip fences, take the {...} span,
    and repair raw control characters inside strings."""
    candidate = _FENCE_RE.sub("", text.strip())
    span = candidate[candidate.find("{") : candidate.rfind("}") + 1]
    for attempt in (candidate, span, _escape_ctrl_in_strings(span)):
        if not attempt:
            continue
        try:
            parsed = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_cost(result_event: dict) -> Decimal | None:
    raw = result_event.get("total_cost_usd")
    if raw is None:
        return None
    try:
        return Decimal(str(raw)).quantize(Decimal("0.000001"))
    except (ValueError, ArithmeticError):
        return None


class ClaudeRunner:
    """One-shot JSON completions on tier 'fast' (Haiku) or 'standard' (Sonnet)."""

    def __init__(self) -> None:
        settings = get_settings()
        self.cli_path = settings.CLAUDE_CLI_PATH
        self._models = {
            "fast": settings.CLAUDE_FAST_MODEL,
            "standard": settings.CLAUDE_STANDARD_MODEL,
        }
        # The audit row commits in its OWN session so token spend survives any
        # rollback of the caller's transaction (worker kill mid-batch, etc.).
        # Tests override with the test-db factory.
        self.audit_factory = None

    async def _run_cli(self, args: list[str], timeout: int) -> tuple[int, bytes, bytes]:
        """Subprocess boundary — overridden in tests."""
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, b"", f"timeout after {timeout}s".encode()
        return proc.returncode or 0, stdout, stderr

    async def run_json(
        self,
        *,
        purpose: str,
        system_prompt: str,
        user_prompt: str,
        tier: str,
        raw_post_id: int | None = None,
        timeout: int | None = None,
    ) -> dict | None:
        """One completion expected to return a JSON object. Never raises.

        Commits an LlmCall audit row in its own session (success or failure).
        Returns the parsed payload dict, or None on any failure (details in
        the audit row + log).
        """
        started = time.monotonic()
        model = self._models.get(tier, f"unknown:{tier}")
        payload = usage = None
        cost = None
        error = None
        sys_file = None
        try:
            settings = get_settings()
            timeout = timeout or settings.CLASSIFY_TIMEOUT_SECONDS
            if tier not in self._models:
                raise ValueError(f"unknown tier {tier!r}")

            fd, sys_file = tempfile.mkstemp(suffix=".txt", prefix="leadfinder_sysprompt_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(system_prompt)

            args = [
                self.cli_path, "-p",
                "--model", model,
                "--tools", "",
                "--disable-slash-commands",
                "--no-session-persistence",
                "--strict-mcp-config",
                "--mcp-config", _EMPTY_MCP_CONFIG,
                "--system-prompt-file", sys_file,
                "--output-format", "json",
                user_prompt,
            ]
            rc, stdout, stderr = await self._run_cli(args, timeout)

            result_event: dict | None = None
            if stdout:
                try:
                    result_event = json.loads(stdout.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    result_event = None

            if rc != 0:
                error = f"claude CLI exited rc={rc}: {stderr.decode(errors='replace')[-500:]}"
            elif result_event is None:
                error = "claude CLI produced no parseable result event"
            elif result_event.get("is_error"):
                error = f"claude CLI API error status={result_event.get('api_error_status')}"
            else:
                usage = result_event.get("usage", {})
                cost = _extract_cost(result_event)
                payload = _extract_json(result_event.get("result", ""))
                if payload is None:
                    head = result_event.get("result", "")[:200]
                    error = f"result text was not a valid JSON object; head: {head!r}"
        except Exception as exc:  # audit row must survive anything
            error = f"runner exception: {exc!r}"
        finally:
            if sys_file is not None:
                try:
                    os.unlink(sys_file)
                except OSError:
                    pass

        usage = usage or {}
        if error:
            error = _SECRET_RE.sub("[redacted]", error)
        call_row = LlmCall(
            purpose=purpose,
            tier=tier,
            model=model,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cached_input_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cost_usd=cost,
            duration_ms=int((time.monotonic() - started) * 1000),
            success=payload is not None,
            error=error,
            raw_post_id=raw_post_id,
        )
        try:
            factory = self.audit_factory or get_session_factory()
            async with factory() as audit_session:
                audit_session.add(call_row)
                await audit_session.commit()
        except Exception:
            log.exception("failed to write llm_calls audit row (purpose=%s)", purpose)
        if error:
            log.warning("llm call failed purpose=%s tier=%s: %s", purpose, tier, error)
        return payload


_runner: ClaudeRunner | None = None


def get_runner() -> ClaudeRunner:
    global _runner
    if _runner is None:
        _runner = ClaudeRunner()
    return _runner
