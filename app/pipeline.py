"""One poll cycle (DESIGN §3): pollers -> age gate + keyword filter -> dedup insert -> alert.

Every stage is injectable for tests; defaults wire the real Reddit adapter,
the configured notifier, and the app database.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from sqlalchemy import select

from app.adapters import reddit_rss
from app.classify import LeadScore
from app.core.config import get_settings
from app.db.session import get_session_factory, insert_new_posts
from app.filtering import is_fresh, match_keywords
from app.models.draft import Draft
from app.models.event import Event
from app.models.lead import Lead, transition
from app.models.mute import Mute
from app.models.raw_post import RawPost
from app.notify import format_alert, format_approval_card, get_notifier
from app.packs import load_packs

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve_packs_dir(configured: str) -> Path:
    """Relative PACKS_DIR must not depend on the process cwd — anchor it to the repo."""
    path = Path(configured)
    return path if path.is_absolute() else _REPO_ROOT / path


class ClassifierBreaker:
    """Stops UNSCORED-alert spam during a sustained classifier outage (e.g. the
    Claude Max 5-hour window). After `threshold` consecutive failures the breaker
    opens: new leads are stored unclassified with NO per-post alert, one probe
    classification runs per cycle, and on recovery the backlog is drained."""

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        self.consecutive_failures = 0
        self._open = False

    def is_open(self) -> bool:
        return self._open

    def record_failure(self) -> bool:
        """Returns True iff this failure just opened the breaker."""
        self.consecutive_failures += 1
        if not self._open and self.consecutive_failures >= self.threshold:
            self._open = True
            return True
        return False

    def record_success(self) -> bool:
        """Returns True iff this success just closed the breaker."""
        was_open = self._open
        self._open = False
        self.consecutive_failures = 0
        return was_open


_breaker = ClassifierBreaker()

_BACKLOG_BATCH = 20


async def _fetch_backlog(session, pack_name: str, limit: int):
    result = await session.execute(
        select(RawPost)
        .where(
            RawPost.pack == pack_name,
            RawPost.classified_at.is_(None),
            RawPost.alerted_at.is_(None),
        )
        .order_by(RawPost.id)
        .limit(limit)
    )
    return list(result.scalars().all())


def _reddit_poll_fn(settings):
    """OAuth adapter when script-app creds exist, else the RSS fallback (DESIGN §2)."""
    if settings.REDDIT_CLIENT_ID and settings.REDDIT_CLIENT_SECRET:
        from app.adapters.reddit_oauth import get_oauth_adapter

        return get_oauth_adapter().poll
    return reddit_rss.poll


def select_poll_fn(settings):
    """Compose all configured sources per pack (M3): reddit + hn + threads.

    Threads only participates when a token exists; its adapter additionally
    enforces the daily query budget + min interval internally.
    """
    reddit_poll = _reddit_poll_fn(settings)
    threads_adapter = None
    if settings.THREADS_ACCESS_TOKEN:
        from app.adapters.threads import get_threads_adapter

        threads_adapter = get_threads_adapter()

    async def poll_all(pack, client):
        from app.adapters import hn

        posts = []
        if pack.reddit.subreddits or pack.reddit.search_queries:
            posts += await reddit_poll(pack, client)
        if pack.hn.search_queries:
            posts += await hn.poll(pack, client)
        if threads_adapter and pack.threads.search_queries:
            posts += await threads_adapter.poll(pack, client)
        return posts

    return poll_all


async def _default_classify(session, pack, row, raw_post_id):
    from app.classify import classify_post
    from app.services.claude_runner import get_runner

    return await classify_post(get_runner(), session, pack, row, raw_post_id=raw_post_id)


async def _default_draft(session, pack, row, score, lead_id):
    from app.draft import draft_lead
    from app.services.claude_runner import get_runner

    return await draft_lead(get_runner(), session, pack, row, score, lead_id)


async def _load_mutes(session_factory) -> dict[str, set[tuple[str | None, str]]]:
    async with session_factory() as session:
        mutes = (await session.execute(select(Mute))).scalars().all()
    out: dict[str, set[tuple[str | None, str]]] = {"keyword": set(), "community": set()}
    for m in mutes:
        out.setdefault(m.kind, set()).add((m.pack, m.value.lower()))
    return out


def _is_muted(muted: set[tuple[str | None, str]], pack_name: str, value: str) -> bool:
    v = value.lower()
    return (pack_name, v) in muted or (None, v) in muted


async def _push_approval_card(session, notifier, pack_name, np, lead, variants, score, summary):
    """Outbox delivery: the lead is already committed as 'drafted'; marking
    approval_pushed_at only after a successful send makes the push retryable
    without duplicating leads (at-least-once: a lost-response duplicate card
    is benign — the second approve fails cleanly)."""
    card, buttons = format_approval_card(
        np, pack_name, np.matched_keywords, score, variants, lead.id
    )
    now = datetime.now(UTC)
    if await notifier.send_with_buttons(card, buttons):
        lead.approval_pushed_at = now
        np.alerted_at = now
        summary["pushed"] += 1
        session.add(Event(kind="approval_pushed", payload={"lead_id": lead.id, "pack": pack_name}))
    else:
        lead.updated_at = now  # rotate to the back of the retry queue
        session.add(Event(kind="alert_failed", payload={"lead_id": lead.id, "pack": pack_name}))
    await session.commit()


async def _retry_unpushed_cards(session, notifier, summary) -> None:
    # ordered by updated_at with failed pushes re-stamped: leads 11+ rotate in
    # instead of starving behind ten permanently-failing cards
    rows = (
        await session.execute(
            select(Lead, RawPost)
            .join(RawPost, RawPost.id == Lead.raw_post_id)
            .where(Lead.status == "drafted", Lead.approval_pushed_at.is_(None))
            .order_by(Lead.updated_at)
            .limit(10)
        )
    ).all()
    for lead, np in rows:
        drafts = (
            await session.execute(
                select(Draft).where(Draft.lead_id == lead.id).order_by(Draft.variant)
            )
        ).scalars().all()
        score = LeadScore.model_validate(np.score) if np.score else None
        await _push_approval_card(session, notifier, lead.pack, np, lead, drafts, score, summary)


_DRAFT_MAX_ATTEMPTS = 3
_DRAFT_BATCH = 3


async def run_draft_cycle(
    *,
    session_factory=None,
    notifier=None,
    packs=None,
    draft_fn=None,
) -> dict:
    """Draft surfaced leads and deliver approval cards (decoupled from polling).

    Runs on its own cron. Attempt-capped: after _DRAFT_MAX_ATTEMPTS failures the
    lead falls back to a plain scored alert instead of re-spending Sonnet forever.
    Sessions stay short — no transaction is open while the LLM runs.
    """
    settings = get_settings()
    session_factory = session_factory or get_session_factory()
    notifier = notifier or get_notifier(settings)
    packs = packs if packs is not None else load_packs(_resolve_packs_dir(settings.PACKS_DIR))
    draft_fn = draft_fn or _default_draft
    packs_by_name = {p.name: p for p in packs}
    summary = {"drafted": 0, "pushed": 0, "draft_failed": 0, "fallback_alerts": 0, "alerted": 0}

    async with session_factory() as session:
        candidates = (
            await session.execute(
                select(Lead.id).where(
                    Lead.status == "surfaced", Lead.draft_attempts < _DRAFT_MAX_ATTEMPTS
                ).order_by(Lead.id).limit(_DRAFT_BATCH)
            )
        ).scalars().all()

    for lead_id in candidates:
        async with session_factory() as session:
            lead = await session.get(Lead, lead_id)
            np = await session.get(RawPost, lead.raw_post_id)
            pack = packs_by_name.get(lead.pack)
            if pack is None or np is None or not np.score:
                log.warning("lead %s undraftable (pack gone or unscored) — skipping", lead_id)
                lead.draft_attempts = _DRAFT_MAX_ATTEMPTS
                await session.commit()
                continue
            score = LeadScore.model_validate(np.score)
            row = {
                "community": np.community,
                "author_handle": np.author_handle,
                "title": np.title,
                "text": np.text,
                "raw_post_id": np.id,
            }
            variants = None
            try:
                # session has no SQL issued yet -> no transaction idles under the LLM
                variants = await draft_fn(session, pack, row, score, lead.id)
            except Exception:
                log.exception("draft_fn raised (lead_id=%s)", lead.id)
                session.add(
                    Event(kind="draft_failed", payload={"lead_id": lead.id, "reason": "exception"})
                )

            if variants:
                transition(lead, "drafted")
                summary["drafted"] += 1
                for v in variants:
                    session.add(
                        Draft(
                            lead_id=lead.id,
                            variant=v.variant,
                            channel=v.channel,
                            text=v.text,
                            risk_flags=v.risk_flags,
                        )
                    )
                await session.commit()  # outbox: durable before the push
                await _push_approval_card(
                    session, notifier, lead.pack, np, lead, variants, score, summary
                )
                continue

            lead.draft_attempts += 1
            summary["draft_failed"] += 1
            if lead.draft_attempts >= _DRAFT_MAX_ATTEMPTS:
                # give up on drafting; the lead still reaches the phone as a plain card
                card = format_alert(np, lead.pack, np.matched_keywords, score=score)
                await _send_and_mark(session, notifier, np, lead.pack, card, summary)
                summary["fallback_alerts"] += 1
                session.add(Event(kind="draft_gave_up", payload={"lead_id": lead.id}))
            await session.commit()

    # outbox retry: drafted leads whose card never reached Telegram
    async with session_factory() as session:
        await _retry_unpushed_cards(session, notifier, summary)

    if any(summary.values()):
        log.info("draft cycle done %s", summary)
    return summary


async def _send_and_mark(session, notifier, np, pack_name: str, card: str, summary: dict) -> None:
    if await notifier.send(card):
        np.alerted_at = datetime.now(UTC)
        summary["alerted"] += 1
        session.add(Event(kind="alert_sent", payload={"raw_post_id": np.id, "pack": pack_name}))
    else:
        session.add(Event(kind="alert_failed", payload={"raw_post_id": np.id, "pack": pack_name}))


async def run_poll_cycle(
    *,
    session_factory=None,
    notifier=None,
    packs=None,
    poll_fn=None,
    classify_fn=None,
    breaker: ClassifierBreaker | None = None,
) -> dict:
    """Poll every enabled pack once (fetch -> filter -> dedup -> classify ->
    threshold -> surfaced lead rows). Drafting/push happens in run_draft_cycle."""
    settings = get_settings()
    session_factory = session_factory or get_session_factory()
    notifier = notifier or get_notifier(settings)
    packs = packs if packs is not None else load_packs(_resolve_packs_dir(settings.PACKS_DIR))
    poll_fn = poll_fn or select_poll_fn(settings)
    classify_fn = classify_fn or _default_classify
    breaker = breaker or _breaker
    muted = await _load_mutes(session_factory)
    if not packs:
        log.warning("no enabled packs loaded from %s — nothing to poll", settings.PACKS_DIR)

    started = time.monotonic()
    summary = {
        "fetched": 0, "matched": 0, "new": 0,
        "classified": 0, "surfaced": 0, "suppressed": 0, "deferred": 0, "alerted": 0,
    }

    async with httpx.AsyncClient(
        headers={"User-Agent": settings.REDDIT_USER_AGENT},
        timeout=20.0,
        follow_redirects=True,
    ) as client:
        for pack in packs:
            posts = await poll_fn(pack, client)
            summary["fetched"] += len(posts)

            rows = []
            for post in posts:
                if not is_fresh(post.created_at, pack.max_age_minutes):
                    continue
                if post.community and _is_muted(muted["community"], pack.name, post.community):
                    continue
                matched = match_keywords(
                    f"{post.title or ''}\n{post.text}",
                    pack.keywords.include,
                    pack.keywords.exclude,
                )
                matched = [k for k in matched if not _is_muted(muted["keyword"], pack.name, k)]
                if not matched:
                    continue
                summary["matched"] += 1
                rows.append(asdict(post) | {"pack": pack.name, "matched_keywords": matched})

            async with session_factory() as session:
                new_posts = await insert_new_posts(session, rows)
                summary["new"] += len(new_posts)
                # commit the inserts immediately: LLM work below takes minutes per
                # lead, and an interrupted cycle recovers via the classified_at
                # backlog instead of losing the batch to a rollback
                await session.commit()

                # Work queue: fresh posts, plus (breaker closed) any outage backlog,
                # or (breaker open, nothing new) one backlog item as the probe.
                queue = list(new_posts)
                if not breaker.is_open():
                    seen_ids = {np.id for np in queue}
                    queue += [
                        p
                        for p in await _fetch_backlog(session, pack.name, _BACKLOG_BATCH)
                        if p.id not in seen_ids
                    ]
                elif not queue:
                    queue = await _fetch_backlog(session, pack.name, 1)

                probed_while_open = False
                for np in queue:
                    # open breaker: exactly one probe classification per cycle
                    if breaker.is_open() and probed_while_open:
                        summary["deferred"] += 1
                        continue
                    probed_while_open = probed_while_open or breaker.is_open()

                    row = {
                        "community": np.community,
                        "author_handle": np.author_handle,
                        "title": np.title,
                        "text": np.text,
                    }
                    try:
                        score = await classify_fn(session, pack, row, np.id)
                    except Exception:
                        log.exception("classify_fn raised (raw_post_id=%s)", np.id)
                        score = None

                    if score is None:
                        if breaker.record_failure():
                            # just opened: defer the REST of this cycle too — probing
                            # again seconds into the same outage is wasted spend
                            probed_while_open = True
                            session.add(
                                Event(
                                    kind="classifier_breaker_open",
                                    payload={"pack": pack.name, "raw_post_id": np.id},
                                )
                            )
                            await notifier.send(
                                "⚠️ LeadFinder: classifier appears down (rate limit?). "
                                "New leads are stored and will be scored on recovery."
                            )
                        if breaker.is_open():
                            summary["deferred"] += 1
                            continue
                        # sporadic failure -> surface UNSCORED rather than lose the lead
                        summary["surfaced"] += 1
                        card = format_alert(
                            np, pack.name, np.matched_keywords, score=None, unscored=True
                        )
                        await _send_and_mark(session, notifier, np, pack.name, card, summary)
                        continue

                    if breaker.record_success():
                        session.add(
                            Event(kind="classifier_breaker_closed", payload={"pack": pack.name})
                        )
                        log.info("classifier recovered — backlog drains over next cycles")
                    summary["classified"] += 1
                    np.classified_at = datetime.now(UTC)
                    np.fit_score = score.fit_score
                    np.score = score.model_dump()

                    # threshold gate: sub-threshold is stored, not surfaced (DESIGN §3.3)
                    if score.fit_score < pack.threshold:
                        summary["suppressed"] += 1
                        continue
                    summary["surfaced"] += 1

                    # surfaced -> lead row only. Drafting (~3 min of Sonnet per lead)
                    # is decoupled into run_draft_cycle so a lead burst can't stall
                    # polling freshness (DESIGN §0: 2-5 min post-to-phone).
                    session.add(Lead(raw_post_id=np.id, pack=pack.name))
                    await session.commit()  # short transactions: one lead at a time
                await session.commit()

    duration_ms = int((time.monotonic() - started) * 1000)
    async with session_factory() as session:
        session.add(Event(kind="poll_cycle", payload=summary | {"duration_ms": duration_ms}))
        await session.commit()

    interval_ms = settings.POLL_INTERVAL_MINUTES * 60_000
    if duration_ms > interval_ms:
        log.warning(
            "poll cycle took %.1f min (interval %d min) — subsequent ticks were skipped "
            "while drafting; freshness degraded this window",
            duration_ms / 60_000, settings.POLL_INTERVAL_MINUTES,
        )
    log.info("poll cycle done %s (%dms)", summary, duration_ms)
    return summary
