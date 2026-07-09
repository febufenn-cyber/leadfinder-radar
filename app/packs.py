"""Offer packs: config-driven definitions of what counts as a lead (DESIGN §1).

Each YAML in the packs dir is one pack owning its communities, search queries,
include/exclude keywords, and freshness window.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class PackKeywords(BaseModel):
    include: list[str] = Field(min_length=1)
    exclude: list[str] = []


class RedditConfig(BaseModel):
    subreddits: list[str] = []
    search_queries: list[str] = []


class OfferPack(BaseModel):
    name: str
    enabled: bool = True
    description: str = ""
    reddit: RedditConfig = RedditConfig()
    keywords: PackKeywords
    max_age_minutes: int = 180  # DESIGN §3.1: stale threads convert poorly


def load_packs(packs_dir: Path, include_disabled: bool = False) -> list[OfferPack]:
    """Load and validate every *.yaml pack; invalid YAML fails loudly."""
    packs: list[OfferPack] = []
    for path in sorted(Path(packs_dir).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        pack = OfferPack.model_validate(data)
        if pack.enabled or include_disabled:
            packs.append(pack)
    return packs
