"""Clear active send halts (DESIGN §3.7) — the deliberate human step that
resumes sending after an auto-halt or manual pause.

    uv run python scripts/clear_halt.py           # list active halts
    uv run python scripts/clear_halt.py 3         # clear halt #3
    uv run python scripts/clear_halt.py --all     # clear everything
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.session import get_session_factory  # noqa: E402
from app.models.event import Event  # noqa: E402
from app.models.halt import Halt  # noqa: E402


async def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    factory = get_session_factory()
    async with factory() as session:
        active = (
            (
                await session.execute(
                    select(Halt).where(Halt.cleared_at.is_(None)).order_by(Halt.created_at)
                )
            )
            .scalars()
            .all()
        )
        if not active:
            print("no active halts — sending is unblocked")
            return
        if arg is None:
            for h in active:
                print(f"#{h.id}  [{h.platform}]  since {h.created_at:%Y-%m-%d %H:%M}  {h.reason}")
            print(f"\n{len(active)} active — clear with: clear_halt.py <id> | --all")
            return

        targets = active if arg == "--all" else [h for h in active if h.id == int(arg)]
        if not targets:
            print(f"no active halt #{arg}")
            return
        for h in targets:
            h.cleared_at = datetime.now(UTC)
            session.add(
                Event(kind="halt_cleared", payload={"halt_id": h.id, "platform": h.platform})
            )
            print(f"cleared #{h.id} [{h.platform}] {h.reason}")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
