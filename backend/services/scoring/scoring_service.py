"""
Full Scoring Service
Integrates with Ollama for AI scoring.
Uses Advanced Scorer with heuristic fallback.
"""

import json
from typing import List, Dict, Optional

# Import the advanced scorer
from services.scoring.advanced_scorer import (
    default_score,
    parse_ai_scoring_response,
    enforce_social_only_cap,
    AiScoreResult,
    AggregatedSignal,
    NoveltyContext
)

# Import Ollama client
from services.scoring.ollama_client import score_with_ollama


def score_symbols_with_ai(aggregated_signals: List[AggregatedSignal],
                         fundamentals_map: Optional[Dict] = None,
                         novelty_map: Optional[Dict] = None) -> Dict[str, AiScoreResult]:
    """
    Score symbols using AI (Ollama integration).
    
    Args:
        aggregated_signals: List of AggregatedSignal objects
        fundamentals_map: Optional map of symbol -> fundamental data
        novelty_map: Optional map of symbol -> novelty context
        
    Returns:
        Dict mapping symbol to AiScoreResult
    """
    if not aggregated_signals:
        return {}
    
    # Build symbol summaries for AI
    symbol_summaries = []
    for agg in aggregated_signals:
        fundamentals = fundamentals_map.get(agg.symbol) if fundamentals_map else None
        novelty = novelty_map.get(agg.symbol) if novelty_map else None
        
        summary = {
            "symbol": agg.symbol,
            "sourceCount": agg.source_count,
            "subredditCount": agg.subreddit_count,
            "signalCount": len(agg.signals),
            "totalUpvotes": agg.total_upvotes,
            "totalComments": agg.total_comments,
            "avgVelocity": agg.avg_velocity,
            "momentum": agg.momentum,
            "sampleTitles": [(s.title or "") for s in agg.signals[:3] if s.title],
            "sources": list(set(s.source for s in agg.signals)),
            "medianSignalAgeHrs": agg.median_signal_age_hrs,
        }
        
        if fundamentals:
            summary["price"] = fundamentals.get("price")
            summary["marketCap"] = fundamentals.get("market_cap")
            summary["shortFloat"] = fundamentals.get("short_float")
            summary["exchange"] = fundamentals.get("exchange")
        
        if novelty:
            summary["isNovel"] = novelty.is_novel
            summary["daysSinceFirstSeen"] = novelty.days_since_first_seen
            summary["priorAppearances"] = novelty.prior_appearances
        
        symbol_summaries.append(summary)
    
    # Try Ollama first
    ai_response = score_with_ollama(symbol_summaries)
    
    if ai_response and "scores" in ai_response:
        # Parse AI response
        parsed_scores = parse_ai_scoring_response(
            json.dumps(ai_response), 
            [agg.symbol for agg in aggregated_signals]
        )
        
        if parsed_scores:
            print(f"AI scoring successful: {len(parsed_scores)} symbols scored")
            
            # Enforce social-only cap
            for agg in aggregated_signals:
                if agg.symbol in parsed_scores:
                    parsed_scores[agg.symbol] = enforce_social_only_cap(
                        agg, 
                        parsed_scores[agg.symbol]
                    )
            
            return parsed_scores
    
    # Fallback to heuristic scoring
    print("AI scoring failed, using heuristic fallback...")
    results = {}
    for agg in aggregated_signals:
        novelty = novelty_map.get(agg.symbol) if novelty_map else None
        result = default_score(agg, novelty)
        results[agg.symbol] = result
    
    return results


def score_and_rank_opportunities(aggregated_signals: List[AggregatedSignal]) -> List[AiScoreResult]:
    """
    Score and rank opportunities.
    Combines P&D detection + scoring.
    
    Args:
        aggregated_signals: List of AggregatedSignal objects
        
    Returns:
        List of AiScoreResult objects sorted by score (descending)
    """
    from services.risk.pump_dump_detector import check_pnd_flags
    
    results = []
    
    for agg in aggregated_signals:
        # Check P&D first
        pnd_result = check_pnd_flags(agg)
        
        # Skip if flagged as pump-and-dump
        if pnd_result.flagged:
            print(f"  {agg.symbol}: SKIPPED (P&D flagged: {pnd_result.flags})")
            continue
        
        # Score the ticker (use AI if available, else heuristic)
        score_result = score_symbols_with_ai([agg])
        if agg.symbol in score_result:
            results.append(score_result[agg.symbol])
    
    # Sort by score (descending)
    results.sort(key=lambda x: x.score, reverse=True)
    
    return results


if __name__ == "__main__":
    # Test the full scoring service
    print("Testing Full Scoring Service with Ollama...")
    print("This will integrate P&D detection + AI scoring\n")
    
    # Import RawSignal
    from services.data_ingestion.reddit_client import RawSignal
    
    # Create test data with RawSignal objects
    test_signals = [
        AggregatedSignal(
            symbol="TSM",
            signals=[
                RawSignal(
                    symbol="TSM",
                    source="SEC_INSIDER",
                    title="Insider Purchase",
                    insider_title="VP",
                    purchase_value=301350
                ),
                RawSignal(
                    symbol="TSM",
                    source="REDDIT",
                    title="TSM looking strong",
                    upvotes=50,
                    subreddit="wallstreetbets"
                )
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
    ]
    
    print("Scoring and ranking opportunities...")
    results = score_and_rank_opportunities(test_signals)
    
    print(f"\nResults: {len(results)} opportunities")
    for result in results:
        print(f"  {result.symbol}: {result.score}/100 - {result.sentiment}")
        print(f"    {result.reasoning}")
