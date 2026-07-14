"""Safe M6 workflow mutations: redraft and keyword/community mute."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select

from app.classify import LeadScore
from app.core.config import get_settings
from app.db.session import get_session_factory
from app.draft import draft_lead
from app.models.draft import Draft
from app.models.draft_revision import DraftRevision
from app.models.event import Event
from app.models.lead import Lead
from app.models.raw_post import RawPost
from app.approval import add_mute
from app.mcp.schemas import MuteResult, RedraftResult
from app.packs import OfferPack, load_packs

_REPO_ROOT = Path(__file__).resolve().parents[2]


class MutationError(ValueError):
    pass


def _load_enabled_packs() -> list[OfferPack]:
    settings = get_settings()
    path = Path(settings.PACKS_DIR)
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return load_packs(path)


class LeadMutationService:
    def __init__(self, *, session_factory=None, runner=None, packs=None) -> None:
        self.session_factory = session_factory or get_session_factory()
        self.runner = runner
        self.packs = packs if packs is not None else _load_enabled_packs()

    def _pack(self, name: str) -> OfferPack:
        pack = next((candidate for candidate in self.packs if candidate.name == name), None)
        if pack is None:
            raise MutationError(f"offer pack {name!r} is not enabled")
        return pack

    async def redraft(self, lead_id: int, guidance: str) -> RedraftResult:
        guidance = guidance.strip()
        if lead_id <= 0:
            raise MutationError("lead_id must be positive")
        if not guidance:
            raise MutationError("guidance is required")
        if len(guidance) > 500:
            raise MutationError("guidance must be 500 characters or fewer")

        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(Lead, RawPost)
                    .join(RawPost, RawPost.id == Lead.raw_post_id)
                    .where(Lead.id == lead_id)
                )
            ).first()
            if row is None:
                raise MutationError(f"lead #{lead_id} not found")
            lead, post = row
            if lead.status != "drafted":
                raise MutationError(f"lead #{lead_id} is {lead.status}, not awaiting approval")
            if not post.score:
                raise MutationError(f"lead #{lead_id} has no classifier score")
            existing = (
                await session.execute(
                    select(Draft).where(Draft.lead_id == lead_id).order_by(Draft.variant)
                )
            ).scalars().all()
            if not existing:
                raise MutationError(f"lead #{lead_id} has no active drafts")
            expected_ids = {draft.id for draft in existing}
            pack = self._pack(lead.pack)
            score = LeadScore.model_validate(post.score)
            post_row = {
                "community": post.community,
                "author_handle": post.author_handle,
                "title": post.title,
                "text": post.text,
                "raw_post_id": post.id,
            }

        if self.runner is None:
            from app.services.claude_runner import get_runner

            self.runner = get_runner()

        async with self.session_factory() as generation_session:
            variants = await draft_lead(
                self.runner,
                generation_session,
                pack,
                post_row,
                score,
                lead_id,
                guidance=guidance,
            )
            if not variants:
                await generation_session.commit()
                raise MutationError("redraft generation failed; existing drafts were preserved")

        guidance_digest = hashlib.sha256(guidance.encode("utf-8")).hexdigest()
        async with self.session_factory() as session:
            lead = await session.get(Lead, lead_id, with_for_update=True)
            if lead is None or lead.status != "drafted":
                raise MutationError("lead changed while redrafting; retry from the current state")
            current = (
                await session.execute(
                    select(Draft).where(Draft.lead_id == lead_id).order_by(Draft.variant)
                )
            ).scalars().all()
            if {draft.id for draft in current} != expected_ids:
                raise MutationError("drafts changed while redrafting; retry")

            revisions: list[DraftRevision] = []
            for draft in current:
                revision = DraftRevision(
                    draft_id=draft.id,
                    lead_id=lead_id,
                    variant=draft.variant,
                    channel=draft.channel,
                    text=draft.text,
                    edited_text=draft.edited_text,
                    risk_flags=list(draft.risk_flags or []),
                    is_gold=draft.is_gold,
                    reason="mcp_redraft",
                    guidance_sha256=guidance_digest,
                )
                revisions.append(revision)
                session.add(revision)

            old_by_variant = {draft.variant: draft for draft in current}
            new_by_variant = {variant.variant: variant for variant in variants}
            for variant_name, old in old_by_variant.items():
                replacement = new_by_variant.get(variant_name)
                if replacement is None:
                    await session.delete(old)
                    continue
                old.channel = replacement.channel
                old.text = replacement.text
                old.risk_flags = list(replacement.risk_flags)
                old.edited_text = None
                old.is_gold = False

            for variant_name, replacement in new_by_variant.items():
                if variant_name in old_by_variant:
                    continue
                session.add(
                    Draft(
                        lead_id=lead_id,
                        variant=variant_name,
                        channel=replacement.channel,
                        text=replacement.text,
                        risk_flags=list(replacement.risk_flags),
                    )
                )

            await session.flush()
            active = (
                await session.execute(
                    select(Draft).where(Draft.lead_id == lead_id).order_by(Draft.variant)
                )
            ).scalars().all()
            session.add(
                Event(
                    kind="mcp_redraft",
                    payload={
                        "lead_id": lead_id,
                        "archived_revision_ids": [revision.id for revision in revisions],
                        "active_draft_ids": [draft.id for draft in active],
                        "variants": [draft.variant for draft in active],
                        "guidance_sha256": guidance_digest,
                    },
                )
            )
            await session.commit()
            return RedraftResult(
                lead_id=lead_id,
                archived_revision_ids=[revision.id for revision in revisions],
                active_draft_ids=[draft.id for draft in active],
                variants=[draft.variant for draft in active],
                guidance_sha256=guidance_digest,
            )

    async def mute(self, kind: str, value: str, pack: str | None = None) -> MuteResult:
        kind = kind.strip().lower()
        value = value.strip().lower()
        if kind not in {"keyword", "community"}:
            raise MutationError("kind must be 'keyword' or 'community'")
        if not value:
            raise MutationError("mute value is required")
        if len(value) > 100:
            raise MutationError("mute value must be 100 characters or fewer")
        if pack is not None:
            self._pack(pack)
        async with self.session_factory() as session:
            created = await add_mute(session, kind, value, pack)
        return MuteResult(kind=kind, value=value, pack=pack, created=created)
