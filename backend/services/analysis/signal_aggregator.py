"""
Signal Aggregation Service
Ported from SignalScope's index.ts (aggregateSignals function)

Aggregates raw signals by ticker symbol, calculates metrics,
and prepares data for scoring and analysis.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set
from collections import defaultdict
import time

# Import RawSignal from reddit_client (single source of truth)
from services.data_ingestion.reddit_client import RawSignal


@dataclass
class AggregatedSignal:
    """Represents aggregated signals for a single ticker."""
    symbol: str
    signals: List[RawSignal]
    source_count: int = 0
    weighted_source_score: float = 0.0
    subreddit_count: int = 0
    total_upvotes: int = 0
    total_comments: int = 0
    avg_velocity: float = 0.0
    momentum: Dict[str, int] = field(default_factory=lambda: {
        "risingCount": 0,
        "freshCount": 0,
        "recentCount": 0,
        "commentDerivedCount": 0,
        "staleCount": 0
    })
    median_signal_age_hrs: Optional[float] = None
    net_premium: Optional[float] = None
    call_premium_ratio: Optional[float] = None


# Source weights (from SignalScope)
SOURCE_WEIGHTS = {
    "SEC_INSIDER": 3,
    "OPTIONS_FLOW": 2.5,
    "VOLUME_SPIKE": 2.5,
    "CONGRESS": 2.5,
    "POLYMARKET": 2.0,
    "TWITTER": 1.2,
    "SEC_FILING": 1,
    "REDDIT": 1,
    "STOCKTWITS": 1,
}


def aggregate_signals(raw_signals: List[RawSignal]) -> List[AggregatedSignal]:
    """
    Aggregate raw signals by ticker symbol.
    Ported from SignalScope's aggregateSignals() function.
    
    Args:
        raw_signals: List of RawSignal objects from various sources
        
    Returns:
        List of AggregatedSignal objects sorted by source count and signal count
    """
    # Group signals by symbol
    by_symbol = defaultdict(list)
    for signal in raw_signals:
        by_symbol[signal.symbol].append(signal)
    
    aggregated = []
    
    for symbol, signals in by_symbol.items():
        # Calculate source count (unique sources)
        unique_sources = set(s.source for s in signals)
        source_count = len(unique_sources)
        
        # Calculate weighted source score
        weighted_score = sum(SOURCE_WEIGHTS.get(s.source, 1) for s in signals)
        
        # Calculate subreddit count (for Reddit signals)
        subreddits = set(s.subreddit for s in signals if s.subreddit)
        subreddit_count = len(subreddits)
        
        # Calculate total upvotes and comments
        total_upvotes = sum(s.upvotes or 0 for s in signals)
        total_comments = sum(s.comment_count or 0 for s in signals)
        
        # Calculate velocity and momentum
        velocity_data = calculate_velocity_and_momentum(signals)
        
        # Calculate median signal age
        median_age = calculate_median_signal_age(signals)
        
        # Extract options flow data if present
        net_premium = None
        call_premium_ratio = None
        for s in signals:
            if s.net_premium is not None:
                net_premium = s.net_premium
            if s.call_premium_ratio is not None:
                call_premium_ratio = s.call_premium_ratio
        
        # Create AggregatedSignal
        agg = AggregatedSignal(
            symbol=symbol,
            signals=signals,
            source_count=source_count,
            weighted_source_score=weighted_score,
            subreddit_count=subreddit_count,
            total_upvotes=total_upvotes,
            total_comments=total_comments,
            avg_velocity=velocity_data["avg_velocity"],
            momentum=velocity_data["momentum"],
            median_signal_age_hrs=median_age,
            net_premium=net_premium,
            call_premium_ratio=call_premium_ratio
        )
        aggregated.append(agg)
    
    # Sort by source count (descending), then by signal count (descending)
    aggregated.sort(key=lambda x: (-x.source_count, -len(x.signals)))
    
    return aggregated


def calculate_velocity_and_momentum(signals: List[RawSignal]) -> Dict:
    """
    Calculate velocity and momentum metrics for a set of signals.
    
    Args:
        signals: List of signals for a single ticker
        
    Returns:
        Dict with avg_velocity and momentum breakdown
    """
    momentum = {
        "risingCount": 0,
        "freshCount": 0,
        "recentCount": 0,
        "commentDerivedCount": 0,
        "staleCount": 0
    }
    
    velocity_sum = 0
    social_signals = 0
    
    for signal in signals:
        if signal.post_age is not None and signal.sort_type:
            social_signals += 1
            
            # Calculate base velocity
            if signal.sort_type == "rising":
                base = 3
                momentum["risingCount"] += 1
            elif signal.sort_type == "hot":
                base = 2
                momentum["risingCount"] += 1
            elif signal.sort_type == "comment":
                base = 1.5
                momentum["commentDerivedCount"] += 1
            elif signal.post_age < 3:
                base = 2
                momentum["freshCount"] += 1
            elif signal.post_age < 12:
                base = 1
                momentum["recentCount"] += 1
            else:
                base = 0.5
                momentum["staleCount"] += 1
            
            velocity_sum += base
    
    avg_velocity = velocity_sum / social_signals if social_signals > 0 else 0
    
    return {
        "avg_velocity": avg_velocity,
        "momentum": momentum
    }


def calculate_median_signal_age(signals: List[RawSignal]) -> Optional[float]:
    """
    Calculate median signal age in hours.
    
    Args:
        signals: List of signals
        
    Returns:
        Median age in hours or None if no signals have age
    """
    ages = [s.post_age for s in signals if s.post_age is not None]
    
    if not ages:
        return None
    
    ages.sort()
    mid = len(ages) // 2
    
    if len(ages) % 2 == 0:
        return (ages[mid - 1] + ages[mid]) / 2
    else:
        return ages[mid]


def fetch_and_aggregate_signals() -> List[AggregatedSignal]:
    """
    Fetch signals from all sources and aggregate them.
    
    Returns:
        List of AggregatedSignal objects
    """
    all_signals = []
    
    # Fetch from Reddit (if credentials available)
    try:
        from services.data_ingestion.reddit_client import fetch_reddit_signals
        reddit_signals = fetch_reddit_signals()
        all_signals.extend(reddit_signals)
        print(f"Fetched {len(reddit_signals)} signals from Reddit")
    except Exception as e:
        print(f"Reddit fetch error: {e}")
    
    # Fetch from SEC insider
    try:
        from services.data_ingestion.sec_insider_client import fetch_sec_insider_signals
        sec_signals = fetch_sec_insider_signals()
        all_signals.extend(sec_signals)
        print(f"Fetched {len(sec_signals)} signals from SEC insider")
    except Exception as e:
        print(f"SEC insider fetch error: {e}")
    
    # Aggregate signals
    aggregated = aggregate_signals(all_signals)
    print(f"\nTotal aggregated signals: {len(aggregated)}")
    
    return aggregated


if __name__ == "__main__":
    # Test the aggregation service
    print("Testing Signal Aggregation Service...")
    print("This will fetch from all available sources and aggregate by ticker\n")
    
    aggregated = fetch_and_aggregate_signals()
    
    if aggregated:
        print("\nTop 10 aggregated signals:")
        for i, agg in enumerate(aggregated[:10], 1):
            print(f"\n{i}. {agg.symbol}")
            print(f"   Sources: {agg.source_count} | Signals: {len(agg.signals)}")
            print(f"   Upvotes: {agg.total_upvotes} | Comments: {agg.total_comments}")
            print(f"   Avg Velocity: {agg.avg_velocity:.2f}")
            print(f"   Momentum: {agg.momentum}")
    else:
        print("\nNo aggregated signals found.")
        print("Make sure signal sources are properly configured.")
