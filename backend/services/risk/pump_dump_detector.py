"""
Pump-and-Dump Detection System
Ported from SignalScope's pnd-filter.ts

Detects potential pump-and-dump schemes using 11+ flags.
Filters out scam stocks before they can be recommended.
"""

from typing import List, Dict, Optional, Set
from dataclasses import dataclass
from enum import Enum

# Import RawSignal for type hints
from services.data_ingestion.reddit_client import RawSignal
# Import the canonical AggregatedSignal so P&D detection, aggregation, and
# scoring all share one type (avoids a structurally-duplicated definition).
from services.analysis.signal_aggregator import AggregatedSignal


class PndFlag(Enum):
    """Pump-and-dump flags."""
    PENNY_PRICE = "penny_price"
    SUB_DIME_52WK_FLOOR = "sub_dime_52wk_floor"
    UPVOTE_PUMP = "upvote_pump"
    OTC_LISTING = "otc_listing"
    MICRO_CAP_NO_CATALYST = "micro_cap_no_catalyst"
    ONLY_PENNY_SUBS = "only_penny_subs"
    SINGLE_SOURCE = "single_source"
    HYPERBOLIC_LANGUAGE = "hyperbolic_language"
    COORDINATED_POSTS = "coordinated_posts"
    NO_NEWS_CATALYST = "no_news_catalyst"
    SUDDEN_SPIKE = "sudden_spike"
    TWITTER_BOT_PROMOTERS = "twitter_bot_promoters"
    TWITTER_COORDINATED_PUMP = "twitter_coordinated_pump"


# Flags that are informational only (not counted toward threshold)
INFORMATIONAL_FLAGS: Set[str] = {
    "penny_price",  # +1.4% avg 7d return - bullish per ML
    "otc_listing",  # +0.5% avg 7d return - bullish per ML
    "twitter_coordinated_pump",  # Not significant in high-vol dataset
    "coordinated_posts",  # Not significant in high-vol dataset
    "single_source",  # Not significant in high-vol dataset
    "no_news_catalyst",  # Not significant in high-vol dataset (moved from effective)
}

# PnD threshold (number of effective flags required to flag)
PND_THRESHOLD = 3

# Penny stock subreddits (red flag if ONLY mentioned here)
PENNY_ONLY_SUBREDDITS: Set[str] = {
    "pennystocks",
    "smallstreetbets",
}

# Reputable subreddits (positive signal)
REPUTABLE_SUBREDDITS: Set[str] = {
    "stocks",
    "investing",
    "wallstreetbets",
}

# Hype phrases that indicate pump attempts
HYPE_PHRASES = [
    "guaranteed", "can't lose", "cant lose", "load up now", "load up",
    "this will 10x", "10x", "100x", "1000x", "1000%",
    "send it", "moon", "rocket", "to the moon",
    "next gme", "next gamestop", "buy now", "get in before",
    "this will explode", "life changing", "easy money",
    "once in a lifetime", "free money", "trust me",
    "not financial advice but buy", "yolo into this",
]


@dataclass
class PndResult:
    """Result of pump-and-dump analysis."""
    flagged: bool
    flags: List[str]
    effective_flag_count: int
    score: int  # Total flag count


@dataclass
class FundamentalData:
    """Fundamental data for a ticker."""
    price: Optional[float] = None
    market_cap: Optional[float] = None
    short_float: Optional[float] = None
    exchange: Optional[str] = None
    wk52_lo: Optional[float] = None
    wk52_hi: Optional[float] = None
    fifty_two_week_range: Optional[str] = None


def check_pnd_flags(agg: AggregatedSignal, 
                    fundamentals: Optional[FundamentalData] = None) -> PndResult:
    """
    Check for pump-and-dump flags.
    Ported from SignalScope's checkPndFlags() function.
    
    Args:
        agg: AggregatedSignal for the ticker
        fundamentals: Optional fundamental data
        
    Returns:
        PndResult with flags and assessment
    """
    flags = []
    
    # Pre-compute catalyst presence
    texts = " ".join([
        f"{s.title or ''} {s.body or ''}" 
        for s in agg.signals
    ]).lower()
    
    news_keywords = [
        "earnings", "fda", "approval", "acquisition", "merger", "contract",
        "revenue", "partnership", "clinical", "patent", "guidance",
        "buyout", "trial results", "sec filing", "10-k", "10-q", "8-k",
        "buyback", "dividend", "spinoff", "spin-off", "restructuring",
        "analyst", "price target", "beat estimates", "guidance raised",
        "upgraded", "downgrade", "stock split", "offering", "ipo",
        "catalyst", "breakthrough", "settlement", "regulatory",
    ]
    
    signal_sources = set(s.source for s in agg.signals)
    has_news_catalyst = (
        any(kw in texts for kw in news_keywords) or
        "SEC_INSIDER" in signal_sources or
        "OPTIONS_FLOW" in signal_sources or
        "CONGRESS" in signal_sources
    )
    
    # Flag 1: Price < $0.50 without catalyst
    if (fundamentals and fundamentals.price is not None and 
        fundamentals.price < 0.50 and not has_news_catalyst):
        flags.append(PndFlag.PENNY_PRICE.value)
    
    # Flag 1b: 52-week floor < $0.09 (sub-dime)
    if (fundamentals and fundamentals.wk52_lo is not None and 
        fundamentals.wk52_lo < 0.09 and not has_news_catalyst):
        flags.append(PndFlag.SUB_DIME_52WK_FLOOR.value)
    
    # Flag 1c: Disproportionately high upvotes vs posts
    reddit_post_count = sum(1 for s in agg.signals if s.source == 'REDDIT')
    if (not has_news_catalyst and 
        agg.total_upvotes > 2000 and 
        reddit_post_count <= 3 and 
        agg.total_comments < 30):
        flags.append(PndFlag.UPVOTE_PUMP.value)
    
    # Flag 2: OTC/Pink sheet listing
    if fundamentals and fundamentals.exchange:
        ex = fundamentals.exchange.upper()
        is_reputable = any(x in ex for x in ["NYSE", "NASDAQ", "AMEX", "ARCA"])
        if not is_reputable:
            flags.append(PndFlag.OTC_LISTING.value)
    
    # Flag 3: Market cap < $40M without catalyst
    if (fundamentals and fundamentals.market_cap is not None and 
        fundamentals.market_cap < 40_000_000 and 
        not has_news_catalyst and 
        agg.total_upvotes < 500 and 
        agg.subreddit_count < 3):
        flags.append(PndFlag.MICRO_CAP_NO_CATALYST.value)
    
    # Flag 4: Only in penny stock subreddits
    subreddits = [
        (s.subreddit or '').lower() 
        for s in agg.signals 
        if s.source == 'REDDIT' and s.subreddit
    ]
    in_reputable = any(sub in REPUTABLE_SUBREDDITS for sub in subreddits)
    in_penny_only = len(subreddits) > 0 and all(sub in PENNY_ONLY_SUBREDDITS for sub in subreddits)
    if in_penny_only and not in_reputable:
        flags.append(PndFlag.ONLY_PENNY_SUBS.value)
    
    # Flag 5: Single source only
    if agg.source_count <= 1 and len(agg.signals) <= 2 and agg.total_upvotes < 20:
        flags.append(PndFlag.SINGLE_SOURCE.value)
    
    # Flag 6: Hyperbolic language
    hype_count = sum(1 for phrase in HYPE_PHRASES if phrase in texts)
    if hype_count >= 3:
        flags.append(PndFlag.HYPERBOLIC_LANGUAGE.value)
    
    # Check for identical/near-identical phrasing
    titles = [
        (s.title or '').lower().strip() 
        for s in agg.signals 
        if s.title
    ]
    if len(titles) >= 2:
        title_set = set(titles)
        duplicate_ratio = 1 - len(title_set) / len(titles)
        if duplicate_ratio >= 0.5:
            flags.append(PndFlag.COORDINATED_POSTS.value)
    
    # Flag 7: No real news catalyst
    if not has_news_catalyst and len(agg.signals) >= 5:
        flags.append(PndFlag.NO_NEWS_CATALYST.value)
    
    # Flag 8: Sudden spike (all posts < 3 hours)
    reddit_signals = [s for s in agg.signals if s.source == 'REDDIT']
    if len(reddit_signals) >= 3:
        all_very_recent = all(
            s.post_age is not None and s.post_age < 3 
            for s in reddit_signals
        )
        avg_upvotes = sum(s.upvotes or 0 for s in reddit_signals) / len(reddit_signals)
        if all_very_recent and avg_upvotes < 10:
            flags.append(PndFlag.SUDDEN_SPIKE.value)
    
    # Calculate effective flags (excluding informational)
    effective_flags = [f for f in flags if f not in INFORMATIONAL_FLAGS]
    
    return PndResult(
        flagged=len(effective_flags) >= PND_THRESHOLD,
        flags=flags,
        effective_flag_count=len(effective_flags),
        score=len(flags)
    )


def is_pump_and_dump(agg: AggregatedSignal, 
                     fundamentals: Optional[FundamentalData] = None) -> bool:
    """
    Simple check if a ticker is a pump-and-dump.
    
    Returns:
        True if flagged as P&D
    """
    result = check_pnd_flags(agg, fundamentals)
    return result.flagged


if __name__ == "__main__":
    # Test the P&D detector
    print("Testing Pump-and-Dump Detector...")
    
    # Import RawSignal for test
    from services.data_ingestion.reddit_client import RawSignal
    
    # Create a test case: obvious pump using RawSignal objects
    test_agg = AggregatedSignal(
        symbol="SCAM",
        signals=[
            RawSignal(
                symbol="SCAM",
                source="REDDIT",
                title="GUARANTEED 1000x MOON",
                subreddit="pennystocks",
                upvotes=10
            ),
            RawSignal(
                symbol="SCAM",
                source="REDDIT", 
                title="GUARANTEED 1000x MOON",
                subreddit="pennystocks",
                upvotes=5
            ),
        ],
        source_count=1,
        weighted_source_score=1,
        subreddit_count=1,
        total_upvotes=15,
        total_comments=2,
        avg_velocity=2.5,
        momentum={"risingCount": 2, "freshCount": 0, "recentCount": 0, "commentDerivedCount": 0, "staleCount": 0},
        median_signal_age_hrs=1,
        net_premium=None,
        call_premium_ratio=None
    )
    
    result = check_pnd_flags(test_agg)
    print(f"\nTest case: Obvious pump")
    print(f"Flags: {result.flags}")
    print(f"Flagged: {result.flagged}")
    print(f"Effective flag count: {result.effective_flag_count}")
