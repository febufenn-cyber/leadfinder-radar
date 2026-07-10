"""Offer-pack loading: YAML -> validated OfferPack models."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.packs import OfferPack, load_packs

PACKS_DIR = Path(__file__).resolve().parent.parent / "packs"


def test_loads_only_enabled_packs_by_default():
    packs = load_packs(PACKS_DIR)
    names = [p.name for p in packs]
    assert "robofox_web" in names
    assert "zervvo_abroad" in names  # enabled at M3
    assert "robofox_ai" not in names  # shipped disabled


def test_include_disabled_returns_all_three():
    packs = load_packs(PACKS_DIR, include_disabled=True)
    assert {p.name for p in packs} == {"robofox_web", "robofox_ai", "zervvo_abroad"}


def test_robofox_web_pack_shape():
    (pack,) = [p for p in load_packs(PACKS_DIR) if p.name == "robofox_web"]
    assert pack.reddit.subreddits  # non-empty
    assert "smallbusiness" in pack.reddit.subreddits
    assert pack.reddit.search_queries
    assert pack.keywords.include
    assert pack.max_age_minutes == 180  # DESIGN §3.1 default


def test_invalid_pack_raises(tmp_path: Path):
    (tmp_path / "broken.yaml").write_text("name: broken\nkeywords: 42\n")
    with pytest.raises(ValidationError):
        load_packs(tmp_path, include_disabled=True)


def test_pack_defaults():
    pack = OfferPack(name="x", keywords={"include": ["need a thing"]})
    assert pack.enabled is True
    assert pack.max_age_minutes == 180
    assert pack.keywords.exclude == []
    assert pack.reddit.subreddits == []
    assert pack.hn.search_queries == []
    assert pack.threads.search_queries == []


def test_zervvo_pack_sources():
    (pack,) = [p for p in load_packs(PACKS_DIR) if p.name == "zervvo_abroad"]
    assert pack.threads.search_queries  # primary channel per the owner's examples
    assert "studyAbroad" in pack.reddit.subreddits
    assert pack.community_rules  # per-sub promo rules present
