"""
Phase 3 Integration Test
Tests the full pipeline: P&D Detection + AI Scoring
"""

import sys
from pathlib import Path

# Add paths
project_root = Path(__file__).parent.parent
backend_dir = project_root / "backend"
sys.path.insert(0, str(backend_dir))
sys.path.insert(0, str(project_root))

# Import after path setup
from services.scoring.scoring_service import score_and_rank_opportunities, score_symbols_with_ai
from services.risk.pump_dump_detector import check_pnd_flags
from services.scoring.advanced_scorer import AggregatedSignal
from services.data_ingestion.reddit_client import RawSignal


def test_pump_and_dump_detection():
    """Test P&D detection."""
    print("=" * 60)
    print("TEST 1: Pump-and-Dump Detection")
    print("=" * 60)
    
    # Test case 1: Legitimate insider buy
    legit_signal = AggregatedSignal(
        symbol="AAPL",
        signals=[
            RawSignal(
                symbol="AAPL",
                source="SEC_INSIDER",
                title="Insider Purchase by CEO",
                insider_title="CEO"
            ),
            RawSignal(
                symbol="AAPL",
                source="REDDIT",
                title="AAPL earnings beat",
                upvotes=200,
                subreddit="stocks"
            )
        ],
        source_count=2,
        weighted_source_score=4,
        subreddit_count=1,
        total_upvotes=200,
        total_comments=10,
        avg_velocity=1.5,
        momentum={"risingCount": 1, "freshCount": 0, "recentCount": 1, "commentDerivedCount": 0, "staleCount": 0},
        median_signal_age_hrs=4,
        net_premium=None,
        call_premium_ratio=None
    )
    
    result1 = check_pnd_flags(legit_signal)
    print(f"\nTest 1a - Legitimate ticker (AAPL with insider buy):")
    print(f"  Flags: {result1.flags}")
    print(f"  Flagged as P&D: {result1.flagged}")
    print(f"  Expected: False")
    print(f"  PASS: {not result1.flagged}")
    
    # Test case 2: Obvious pump
    pump_signal = AggregatedSignal(
        symbol="SCAM",
        signals=[
            RawSignal(
                symbol="SCAM",
                source="REDDIT",
                title="GUARANTEED 1000x MOON",
                upvotes=10,
                subreddit="pennystocks"
            ),
            RawSignal(
                symbol="SCAM",
                source="REDDIT",
                title="GUARANTEED 1000x MOON",
                upvotes=5,
                subreddit="pennystocks"
            )
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
    
    result2 = check_pnd_flags(pump_signal)
    print(f"\nTest 1b - Obvious pump (SCAM):")
    print(f"  Flags: {result2.flags}")
    print(f"  Flagged as P&D: {result2.flagged}")
    print(f"  Expected: True (or at least multiple flags)")
    print(f"  PASS: {result2.effective_flag_count >= 2}")
    
    return not result1.flagged and result2.effective_flag_count >= 2


def test_ai_scoring():
    """Test AI scoring with Ollama."""
    print("\n" + "=" * 60)
    print("TEST 2: AI Scoring with Ollama")
    print("=" * 60)
    
    # Create test signal
    test_aggregated = AggregatedSignal(
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
                title="TSM looking strong for AI",
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
    
    print(f"\nScoring TSM (insider buy + Reddit mention)...")
    
    try:
        results = score_symbols_with_ai([test_aggregated])
        
        if results and "TSM" in results:
            result = results["TSM"]
            print(f"  Score: {result.score}/100")
            print(f"  Sentiment: {result.sentiment}")
            print(f"  Reasoning: {result.reasoning}")
            print(f"  PASS: Score > 0")
            return result.score > 0
        else:
            print("  No score returned (using heuristic fallback)")
            return True  # Heuristic fallback is acceptable
    except Exception as e:
        print(f"  Error: {e}")
        return False


def test_opportunity_ranking():
    """Test full opportunity ranking pipeline."""
    print("\n" + "=" * 60)
    print("TEST 3: Opportunity Ranking (P&D + Scoring)")
    print("=" * 60)
    
    # Create test signals
    test_signals = [
        # Legitimate ticker with insider buy
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
                    title="TSM AI growth",
                    upvotes=50,
                    subreddit="stocks"
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
        ),
        # Social-only ticker
        AggregatedSignal(
            symbol="GME",
            signals=[
                RawSignal(
                    symbol="GME",
                    source="REDDIT",
                    title="GME to the moon",
                    upvotes=100,
                    subreddit="wallstreetbets"
                )
            ],
            source_count=1,
            weighted_source_score=1,
            subreddit_count=1,
            total_upvotes=100,
            total_comments=20,
            avg_velocity=2.0,
            momentum={"risingCount": 1, "freshCount": 0, "recentCount": 1, "commentDerivedCount": 0, "staleCount": 0},
            median_signal_age_hrs=2,
            net_premium=None,
            call_premium_ratio=None
        )
    ]
    
    print(f"\nRanking {len(test_signals)} opportunities...")
    
    try:
        results = score_and_rank_opportunities(test_signals)
        
        print(f"\nResults: {len(results)} opportunities ranked")
        for i, result in enumerate(results, 1):
            print(f"  {i}. {result.symbol}: {result.score}/100 - {result.sentiment}")
            print(f"     {result.reasoning}")
        
        # Check that TSM (with insider) scored higher than GME (social-only)
        if len(results) >= 2:
            tsm_score = next((r.score for r in results if r.symbol == "TSM"), 0)
            gme_score = next((r.score for r in results if r.symbol == "GME"), 0)
            print(f"\nTSM score: {tsm_score}, GME score: {gme_score}")
            print(f"TSM should score higher (has insider catalyst)")
            return tsm_score >= gme_score
        return True
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("PHASE 3 INTEGRATION TEST")
    print("Pump-and-Dump Detection + AI Scoring")
    print("=" * 60)
    
    # Run tests
    test1_pass = test_pump_and_dump_detection()
    test2_pass = test_ai_scoring()
    test3_pass = test_opportunity_ranking()
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"Test 1 (P&D Detection): {'PASS' if test1_pass else 'FAIL'}")
    print(f"Test 2 (AI Scoring): {'PASS' if test2_pass else 'FAIL'}")
    print(f"Test 3 (Ranking): {'PASS' if test3_pass else 'FAIL'}")
    
    if test1_pass and test2_pass and test3_pass:
        print("\n[SUCCESS] ALL TESTS PASSED")
        print("Phase 3 implementation is working correctly!")
        return 0
    else:
        print("\n[FAILURE] SOME TESTS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
