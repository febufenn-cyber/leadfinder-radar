"""Run one poll cycle standalone: uv run python scripts/poll_once.py"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline import run_draft_cycle, run_poll_cycle  # noqa: E402

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs full request URLs at INFO — the Telegram URL contains the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)


async def main() -> None:
    print(f"poll cycle summary: {await run_poll_cycle()}")
    print(f"draft cycle summary: {await run_draft_cycle()}")


if __name__ == "__main__":
    asyncio.run(main())
