from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database.models import Base, Post, ScrapedArticle, SecFiling, TradingOpportunity
from routers.news import get_unified_news
from routers.trade_list import get_opportunity_sources


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _seed_articles(db, sources, mention_symbol, discovered, base=None, symbol="NVDA"):
    """Insert `sources` list to control source distribution."""
    if base is None:
        base = datetime.now(timezone.utc)
    rows = []
    for i, src in enumerate(sources):
        content = f"{symbol} is trending" if mention_symbol else "just some filler text"
        rows.append(ScrapedArticle(
            source=src,
            url=f"http://example.com/{src}/{i}",
            title=content,
            summary=content,
            full_content=content,
            discovered_at=base - timedelta(hours=discovered) + timedelta(seconds=i),
        ))
    db.add_all(rows)
    db.commit()
    return rows


# ── Fix 1: apply source predicate before the symbol-search window limit ──


def test_news_symbol_plus_truth_social_applies_source_before_window(db):
    base = datetime.now(timezone.utc)
    # 1000 RSS rows discovered NOWER than the Truth Social rows → if the
    # window were taken globally first, Truth Social rows would be dropped.
    _seed_articles(db, ["RSS-1"] * 120, True, 1, symbol="TRUMP")
    _seed_articles(db, ["Truth Social"] * 5, True, 10, symbol="TRUMP")

    res = asyncio.run(get_unified_news(
        symbol="TRUMP", start_date=None, end_date=None,
        source="truth_social", limit=50, offset=0, db=db,
    ))
    # The 5 Truth Social rows live OUTSIDE the newest-3000-RSS window.
    # Before the fix the source filter ran after the limit → total 0.
    assert res["total"] == 5
    assert all(i["source"] == "truth_social" for i in res["items"])


def test_symbol_search_still_windows_to_limit(db):
    base = datetime.now(timezone.utc)
    _seed_articles(db, ["RSS"] * 5000, True, 1, symbol="NVDA")
    res = asyncio.run(get_unified_news(
        symbol="NVDA", start_date=None, end_date=None,
        source="rss", limit=50, offset=0, db=db,
    ))
    # Window is bounded — can't return all 5000.
    assert 0 < res["total"] <= 3000


# ── Fix 2: NEWS evidence anchored to opportunity.added_at, not current top-100 ──

def test_sources_news_evidence_anchored_to_added_at(db):
    base = datetime.now(timezone.utc)
    # An opportunity is created when the article set at discovery is these.
    # 60 evidence articles discovered just before the opportunity is added.
    _seed_articles(db, ["Feed"] * 60, True, 1, symbol="NVDA")  # discovered at base-1h
    opp_added_at = datetime.now(timezone.utc)
    opp = TradingOpportunity(
        symbol="NVDA", score=55, sentiment="bullish", reasoning="r",
        source_count=1, signal_count=10, status="watchlist",
        added_at=opp_added_at, sources=["NEWS"],
    )
    db.add(opp)
    db.commit()

    res = asyncio.run(get_opportunity_sources(opp.id, db=db))
    news = next(s for s in res["sources"] if s["name"] == "NEWS")
    assert news["items_found"] == 60

    # Now embed a flood of NEWER articles AFTER the opportunity was added, none
    # of which mention AI. The anchored window must STILL find the original 60.
    _seed_articles(db, ["Other"] * 150, False, -1)  # discovered after opp
    res = asyncio.run(get_opportunity_sources(opp.id, db=db))
    news = next(s for s in res["sources"] if s["name"] == "NEWS")
    assert news["items_found"] == 60


def test_news_evidence_zero_when_no_article_mentions_symbol(db):
    opp = TradingOpportunity(
        symbol="ZZZ", score=50, sentiment="neutral", reasoning="r",
        source_count=1, signal_count=1, status="watchlist",
        added_at=datetime.now(timezone.utc), sources=["NEWS"],
    )
    db.add(opp)
    db.commit()
    _seed_articles(db, ["Feed"] * 10, False, 1, symbol="ZZZ")
    res = asyncio.run(get_opportunity_sources(opp.id, db=db))
    news = next(s for s in res["sources"] if s["name"] == "NEWS")
    assert news["items_found"] == 0