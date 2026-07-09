"""Classify + score — tier fast / Haiku (DESIGN §3.3).

Input: a keyword-prefiltered post. Output: a validated LeadScore, or None on
any failure (the pipeline then surfaces the post UNSCORED rather than losing
it). Few-shots live in packs/fewshots/<pack>.yaml so the owner can replace the
starter set with his real positives/near-misses without touching code.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.packs import OfferPack

log = logging.getLogger(__name__)

_FEWSHOTS_DIR = Path(__file__).resolve().parents[1] / "packs" / "fewshots"


class LeadScore(BaseModel):
    """DESIGN §3.3 output schema, verbatim."""

    is_demand_post: bool
    offer_pack: str
    intent: Literal["explicit_request", "problem_statement", "recommendation_ask"]
    buyer_type: Literal["business_owner", "founder", "student", "individual", "unclear"]
    budget_signal: Literal["stated", "implied", "none"]
    urgency: Literal["now", "soon", "exploring"]
    disqualifiers: list[str] = []
    fit_score: int = Field(ge=0, le=100)
    one_line_summary: str

    @field_validator("fit_score", mode="before")
    @classmethod
    def _clamp(cls, v):
        try:
            return max(0, min(100, int(v)))
        except (TypeError, ValueError):
            return v  # let the field validation raise properly


def load_fewshots(pack_name: str) -> list[dict]:
    """[{text, label ('positive'|'near_miss'), score: {...}}, ...] or []."""
    path = _FEWSHOTS_DIR / f"{pack_name}.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("examples", [])


def build_prompts(pack: OfferPack, fewshots: list[dict], post_row: dict) -> tuple[str, str]:
    """(system_prompt, user_prompt) for the fast-tier classify call."""
    schema_desc = json.dumps(
        {
            "is_demand_post": "bool — is the author asking for / needing this service?",
            "offer_pack": pack.name,
            "intent": "explicit_request | problem_statement | recommendation_ask",
            "buyer_type": "business_owner | founder | student | individual | unclear",
            "budget_signal": "stated | implied | none",
            "urgency": "now | soon | exploring",
            "disqualifiers": ["wants_free", "full_time_job", "agency_seeking_leads"],
            "fit_score": "int 0-100 — how good a lead this is for the offer",
            "one_line_summary": "one sentence, who wants what",
        },
        indent=2,
    )
    shots = "\n\n".join(
        f"EXAMPLE ({s.get('label', 'example')}):\nPOST: {s.get('text', '')}\n"
        f"CORRECT OUTPUT: {json.dumps(s.get('score', {}))}"
        for s in fewshots
    )
    system = f"""You are a lead classifier for the offer pack "{pack.name}": {pack.description}

You read one public social post and decide whether the AUTHOR is a potential buyer
of this service, then score the fit. Sellers advertising themselves, job seekers,
people wanting things for free, and agencies hunting for clients are NOT leads.

Respond with ONLY a JSON object matching this schema — no prose, no markdown fences:
{schema_desc}

Scoring guide: 80+ explicit request with budget/urgency; 60-79 clear need, details
missing; 40-59 problem statement that the offer could solve; <40 weak or off-target.

{shots}"""
    user = json.dumps(
        {
            "community": post_row.get("community"),
            "author": post_row.get("author_handle"),
            "title": post_row.get("title"),
            "text": (post_row.get("text") or "")[:2000],
        },
        ensure_ascii=False,
    )
    return system, user


async def classify_post(
    runner,
    session: AsyncSession,
    pack: OfferPack,
    post_row: dict,
    raw_post_id: int | None = None,
) -> LeadScore | None:
    """Run the fast-tier classifier. None on any failure (logged + event row)."""
    system, user = build_prompts(pack, load_fewshots(pack.name), post_row)
    payload = await runner.run_json(
        purpose="classify",
        system_prompt=system,
        user_prompt=user,
        tier="fast",
        session=session,
        raw_post_id=raw_post_id,
    )
    if payload is None:
        session.add(
            Event(kind="classify_failed", payload={"raw_post_id": raw_post_id, "reason": "llm_call_failed"})
        )
        return None
    try:
        return LeadScore.model_validate(payload)
    except ValidationError as exc:
        log.warning("classifier payload failed validation raw_post_id=%s: %s", raw_post_id, exc)
        session.add(
            Event(
                kind="classify_failed",
                payload={"raw_post_id": raw_post_id, "reason": "validation", "payload": payload},
            )
        )
        return None
