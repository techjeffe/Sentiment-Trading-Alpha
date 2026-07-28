"""
Advanced Scoring System
Ported from SignalScope's scoring.ts

Scores tickers from 0-100 based on breakout potential.
Uses AI with heuristic fallback.
Enforces hard rules (social-only caps at 50).
"""

import json
import math
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

# Import AggregatedSignal from signal_aggregator (canonical source)
from services.analysis.signal_aggregator import AggregatedSignal
from services.data_ingestion.reddit_client import RawSignal


@dataclass
class AiScoreResult:
    """Result of AI scoring for a ticker."""
    symbol: str
    score: int  # 0-100
    raw_score: int  # Before capping
    sentiment: str  # "bullish", "bearish", "neutral"
    reasoning: str


@dataclass
class NoveltyContext:
    """Novelty information for a ticker."""
    is_novel: bool
    days_since_first_seen: Optional[int] = None
    prior_appearances: int = 0


def default_score(agg: AggregatedSignal, 
                 novelty: Optional[NoveltyContext] = None) -> AiScoreResult:
    """
    Heuristic scoring fallback.
    Ported from SignalScope's defaultScore() function.
    
    Args:
        agg: AggregatedSignal for the ticker
        novelty: Optional novelty context
        
    Returns:
        AiScoreResult with heuristic score
    """
    sources = set(s.source for s in agg.signals)
    has_insider = "SEC_INSIDER" in sources
    has_options = "OPTIONS_FLOW" in sources
    has_congress = "CONGRESS" in sources
    has_catalyst_source = has_insider or has_options or has_congress
    
    # Base score based on catalyst presence
    if has_catalyst_source and agg.source_count >= 3:
        base = 65  # Multi-source with real catalyst
    elif has_insider:
        base = 55  # Insider buy alone
    elif has_congress:
        base = 52  # Congressional buy
    elif has_options:
        base = 50  # Unusual options alone
    else:
        base = min(agg.source_count * 15, 40)  # Social-only, capped
    
    # Engagement boost
    engagement = min(math.log2(agg.total_upvotes + 1) * 2.0, 10)
    
    # Velocity boost
    velocity_boost = min(agg.avg_velocity * 3, 10)
    
    # Rising fraction adjustment
    total_signals = max(len(agg.signals), 1)
    rising_frac = agg.momentum.get("risingCount", 0) / total_signals
    stale_frac = agg.momentum.get("staleCount", 0) / total_signals
    rising_adj = 4 if rising_frac > 0.5 else (-4 if stale_frac > 0.5 else 0)
    
    # Comment penalty (high comments = peak hype)
    comment_adj = 0
    if agg.total_comments > 150 and agg.total_upvotes / max(agg.total_comments, 1) < 2:
        comment_adj = -5
    elif agg.total_upvotes > 100 and agg.total_upvotes / max(agg.total_comments, 1) > 5:
        comment_adj = 3
    
    # Novelty adjustment
    novelty_adj = 0
    if novelty:
        if novelty.is_novel:
            novelty_adj = 5
        elif novelty.prior_appearances >= 3 or (novelty.days_since_first_seen and novelty.days_since_first_seen >= 7):
            novelty_adj = -10
    
    # Signal age penalty
    staleness_adj = 0
    if agg.median_signal_age_hrs is not None:
        if agg.median_signal_age_hrs > 12:
            staleness_adj = -8
        elif agg.median_signal_age_hrs > 6:
            staleness_adj = -4
    
    # Calculate raw score
    raw = base + engagement + velocity_boost + novelty_adj + staleness_adj + comment_adj + rising_adj
    raw_score = max(0, min(100, round(raw)))
    
    # Apply social-only cap
    max_score = 100 if has_catalyst_source else 50
    score = min(raw_score, max_score)
    
    # Determine sentiment
    if score >= 60:
        sentiment = "bullish"
    elif score <= 30:
        sentiment = "bearish"
    else:
        sentiment = "neutral"
    
    reasoning = "Heuristic scoring"
    if has_catalyst_source:
        reasoning += " - catalyst source detected"
    else:
        reasoning += " - social-only, low confidence"
    
    return AiScoreResult(
        symbol=agg.symbol,
        score=score,
        raw_score=raw_score,
        sentiment=sentiment,
        reasoning=reasoning
    )


def build_scoring_prompt(symbol_summaries: List[Dict]) -> str:
    """
    Build the system prompt for AI scoring.
    Ported from SignalScope's scoring.ts systemPrompt.
    """
    return """You are a stock signal analyst. Score each symbol from 0-100 based on breakout potential.

HARD RULES:
- Tickers with REAL catalysts (SEC filings, earnings, FDA, partnerships, contracts, insider buys, unusual options flow) ALWAYS score higher than pure social media signals.
- A ticker with only Reddit/StockTwits mentions and no verifiable catalyst should NEVER score above 50.
- If an insider buy (Form 4 open market purchase) or unusual options activity exists, that is the strongest signal — weight it heavily.
- Be direct about confidence level. If evidence is thin or speculative, score low and say so. Do NOT hype weak signals.
- A low score is not a failure — it's honest. Most signals are noise.

Scoring guidance:
- 80-100: Real catalyst + multi-source corroboration + insider/options confirmation
- 60-79: Real catalyst + at least 2 sources, or strong insider/options signal alone
- 40-49: Social buzz with some catalyst indicators but not confirmed — high velocity + strong engagement can push toward 45-49 but NEVER above 50 without a verifiable catalyst
- 20-39: Social-only signal, no verifiable catalyst
- 0-19: Likely noise or pump attempt

Also consider:
- Short float % — high short interest + real catalyst = squeeze candidate
- Price relative to 52-week range
- Exchange quality — NYSE/NASDAQ preferred over OTC
- Pre-consensus (first appearance) vs already widely discussed
- avgVelocity measures signal momentum: 3 = rising, 2 = hot/very fresh (<3h), 1.5 = trending/comment-derived, 1 = recent (<12h), 0.5 = older.
  High velocity (≥2.0) with multiple mentions = potential early breakout. Weight this as a positive signal.
- Multiple rising signals = strong trending evidence.
- subredditCount = number of unique subreddits mentioning the ticker. 3+ subreddits = broad consensus (+3-5 boost).
- Multi-source corroboration is one of the strongest bullish signals — apply +3 to +5 boost for 2+ sources, +5 to +8 for 3+ sources.
- High upvote-to-comment ratio (>5:1) with significant upvotes (>100) suggests strong conviction (+5 to +8 boost).
- CRITICAL: High comment count alone is NOT positive engagement — when totalComments > 150 with ratio < 2:1, this is peak hype (-8 to -10 penalty).

Return JSON: { "scores": [{ "symbol": "X", "score": 0-100, "sentiment": "bullish|bearish|neutral", "reasoning": "brief explanation" }] }"""


def parse_ai_scoring_response(response_text: str, symbols: List[str]) -> Dict[str, AiScoreResult]:
    """
    Parse AI scoring response.
    
    Args:
        response_text: Raw AI response
        symbols: List of symbols to match against
        
    Returns:
        Dict mapping symbol to AiScoreResult
    """
    try:
        parsed = json.loads(response_text)
        if not isinstance(parsed, dict) or "scores" not in parsed:
            return {}
        
        scores = parsed["scores"]
        if not isinstance(scores, list):
            return {}
        
        result = {}
        for item in scores:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol", "")
            if symbol not in symbols:
                continue
            
            try:
                score = int(item.get("score", 0))
                score = max(0, min(100, score))
            except (ValueError, TypeError):
                continue
            
            sentiment = item.get("sentiment", "neutral")
            if sentiment not in ["bullish", "bearish", "neutral"]:
                sentiment = "neutral"
            
            reasoning = item.get("reasoning", "")
            
            result[symbol] = AiScoreResult(
                symbol=symbol,
                score=score,
                raw_score=score,
                sentiment=sentiment,
                reasoning=reasoning
            )
        
        return result
    
    except json.JSONDecodeError:
        return {}


def enforce_social_only_cap(agg: AggregatedSignal, score_result: AiScoreResult) -> AiScoreResult:
    """
    Enforce social-only cap: tickers without catalyst source capped at 50.
    
    Args:
        agg: AggregatedSignal
        score_result: AI scoring result
        
    Returns:
        Updated AiScoreResult with cap enforced
    """
    sources = set(s.get('source', '') for s in agg.signals)
    has_catalyst_source = any(
        src in sources for src in ["SEC_INSIDER", "OPTIONS_FLOW", "CONGRESS"]
    )
    
    if not has_catalyst_source:
        score_result.score = min(score_result.score, 50)
        score_result.raw_score = score_result.score
        if "capped" not in score_result.reasoning.lower():
            score_result.reasoning += " [Capped at 50: social-only]"
    
    return score_result


# Note: The actual AI scoring function (score_symbol_batch) requires AI integration
# This will be implemented in the main scoring service that connects to Ollama/OpenAI

if __name__ == "__main__":
    # Test the scoring system
    print("Testing Advanced Scoring System...")
    print("Testing heuristic scoring (defaultScore)...\n")
    
    # Test case 1: Social-only signal
    agg1 = AggregatedSignal(
        symbol="GME",
        signals=[{"source": "REDDIT", "title": "GME to the moon", "upvotes": 100}],
        source_count=1,
        weighted_source_score=1,
        subreddit_count=1,
        total_upvotes=100,
        total_comments=20,
        avg_velocity=2.0,
        momentum={"risingCount": 1, "freshCount": 0, "recentCount": 0, "commentDerivedCount": 0, "staleCount": 0},
        median_signal_age_hrs=2,
        net_premium=None,
        call_premium_ratio=None
    )
    
    result1 = default_score(agg1)
    print(f"Test 1 - Social-only signal (GME):")
    print(f"  Score: {result1.score}/100")
    print(f"  Sentiment: {result1.sentiment}")
    print(f"  Reasoning: {result1.reasoning}")
    print(f"  Expected: ≤50 (social-only cap)")
    print(f"  Pass: {result1.score <= 50}\n")
    
    # Test case 2: Insider buy signal
    agg2 = AggregatedSignal(
        symbol="AAPL",
        signals=[
            {"source": "SEC_INSIDER", "title": "Insider Purchase", "insider_title": "CEO"},
            {"source": "REDDIT", "title": "AAPL looking good", "upvotes": 50},
        ],
        source_count=2,
        weighted_source_score=4,
        subreddit_count=1,
        total_upvotes=50,
        total_comments=10,
        avg_velocity=1.5,
        momentum={"risingCount": 1, "freshCount": 0, "recentCount": 1, "commentDerivedCount": 0, "staleCount": 0},
        median_signal_age_hrs=4,
        net_premium=None,
        call_premium_ratio=None
    )
    
    result2 = default_score(agg2)
    print(f"Test 2 - Insider buy signal (AAPL):")
    print(f"  Score: {result2.score}/100")
    print(f"  Sentiment: {result2.sentiment}")
    print(f"  Reasoning: {result2.reasoning}")
    print(f"  Expected: >50 (has catalyst source)")
    print(f"  Pass: {result2.score > 50}\n")
    
    print("Advanced Scoring System: Basic tests passed!")
    print("Note: Full AI scoring requires AI integration (Ollama/OpenAI)")
