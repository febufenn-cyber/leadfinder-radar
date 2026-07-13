"""Manually generate this month's M5 prompt-tuning proposals."""

from __future__ import annotations

import asyncio
import json
import logging

from app.prompt_tuner import run_prompt_tuning_cycle

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


async def _main() -> None:
    summary = await run_prompt_tuning_cycle()
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
