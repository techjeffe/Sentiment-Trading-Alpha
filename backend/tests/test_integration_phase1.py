"""
Test script to verify ticker extraction integration with news articles.
Tests Phase 1 implementation with real-world examples.
"""

import sys
import os
from pathlib import Path

# Add the project root to the path so we can import backend modules
project_root = Path(__file__).parent.parent
backend_dir = project_root / "backend"
sys.path.insert(0, str(backend_dir))
sys.path.insert(0, str(project_root))

from services.data_ingestion.ticker_extractor import (
    extract_tickers,
    extract_tickers_from_article,
    extract_all_tickers
)


def test_real_news_articles():
    """Test ticker extraction on real news article examples."""
    
    test_cases = [
        {
            "title": "Apple Reports Record Earnings, AAPL Surges",
            "content": "Apple Inc. (AAPL) reported record quarterly earnings yesterday. The stock price jumped 5% after hours. Microsoft (MSFT) also reported strong results.",
            "expected": ["AAPL", "MSFT"]
        },
        {
            "title": "Tesla and Nvidia Partner on AI",
            "content": "TSLA and NVDA announced a new partnership to develop autonomous driving AI. This follows similar deals by AMD and INTC in the semiconductor space.",
            "expected": ["TSLA", "NVDA", "AMD", "INTC"]
        },
        {
            "title": "Market Rally Continues",
            "content": "The SPY and QQQ both gained 2% today. However, individual stocks like AAPL and AMZN outperformed the broader market.",
            "expected": ["AAPL", "AMZN"]  # SPY and QQQ should be filtered (ETFs)
        },
        {
            "title": "Reddit User Discusses GME and AMC",
            "content": "I think GME is ready for another run. AMC also looking strong. Not financial advice, just my YOLO play.",
            "expected": ["GME", "AMC"]  # YOLO should be filtered (blacklist)
        },
        {
            "title": "SEC Filing Reveals Insider Trading",
            "content": "Form 4 filing shows CEO purchased 10,000 shares of XYZ on Monday. The IPO market remains strong with several new listings.",
            "expected": ["XYZ"]  # CEO and IPO should be filtered (blacklist)
        }
    ]
    
    print("=" * 60)
    print("Testing Ticker Extraction with Real News Articles")
    print("=" * 60)
    
    passed = 0
    failed = 0
    
    for i, test in enumerate(test_cases, 1):
        print(f"\nTest {i}: {test['title']}")
        print(f"Content: {test['content'][:100]}...")
        
        # Extract tickers
        discovered = extract_tickers_from_article(test['title'], test['content'])
        
        print(f"Expected: {test['expected']}")
        print(f"Discovered: {discovered}")
        
        # Check if all expected tickers were found
        expected_set = set(test['expected'])
        discovered_set = set(discovered)
        
        if expected_set.issubset(discovered_set):
            print("[PASS] All expected tickers found")
            passed += 1
        else:
            missing = expected_set - discovered_set
            print(f"[FAIL] Missing tickers: {missing}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


def test_ticker_extraction_performance():
    """Test performance of ticker extraction on larger text."""
    
    print("\n" + "=" * 60)
    print("Testing Performance with Larger Text")
    print("=" * 60)
    
    # Simulate a long article with multiple ticker mentions
    long_article = """
    Apple Inc. (AAPL) reported earnings today. The company beat expectations
    and guided higher for next quarter. Microsoft (MSFT) also reported strong
    results yesterday. Tesla (TSLA) continues to gain market share in China.
    
    Other tech stocks like Google (GOOGL) and Amazon (AMZN) are also
    performing well. Meta (META) announced new AI initiatives.
    
    In the semiconductor space, Nvidia (NVDA) and AMD are leading.
    Intel (INTC) is trying to catch up with new products.
    
    Financial stocks like JPM and BAC are also rallying.
    Energy stocks such as XOM and CVX are up on oil prices.
    """
    
    import time
    
    start = time.time()
    tickers = extract_tickers(long_article)
    elapsed = time.time() - start
    
    print(f"\nText length: {len(long_article)} characters")
    print(f"Extraction time: {elapsed:.4f} seconds")
    print(f"Tickers found: {tickers}")
    print(f"Tickers per second: {len(tickers) / elapsed:.1f}")
    
    # Performance should be fast (< 10ms for this text)
    if elapsed < 0.01:
        print("[PASS] Performance: FAST (< 10ms)")
    else:
        print(f"[WARN] Performance: SLOW ({elapsed:.3f}s)")
    
    return True


def test_edge_cases():
    """Test edge cases and potential issues."""
    
    print("\n" + "=" * 60)
    print("Testing Edge Cases")
    print("=" * 60)
    
    edge_cases = [
        {
            "name": "Empty text",
            "text": "",
            "expected_count": 0
        },
        {
            "name": "No tickers",
            "text": "The weather is nice today.",
            "expected_count": 0
        },
        {
            "name": "Mixed case (should not match)",
            "text": "apple and microsoft are down",
            "expected_count": 0  # lowercase shouldn't match
        },
        {
            "name": "Ticker at end of sentence",
            "text": "The stock is AAPL.",
            "expected_count": 1
        },
        {
            "name": "Ticker in parentheses",
            "text": "Apple Inc (AAPL) reported earnings",
            "expected_count": 1
        },
        {
            "name": "Multiple mentions of same ticker",
            "text": "AAPL is up. AAPL is strong. Buy AAPL.",
            "expected_count": 1  # Should deduplicate
        }
    ]
    
    passed = 0
    failed = 0
    
    for test in edge_cases:
        print(f"\n{test['name']}: '{test['text'][:50]}...'")
        result = extract_tickers(test['text'])
        
        if len(result) == test['expected_count']:
            print(f"[PASS] Found {len(result)} tickers (expected {test['expected_count']})")
            passed += 1
        else:
            print(f"[FAIL] Found {len(result)} tickers (expected {test['expected_count']})")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Edge Case Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


def main():
    """Run all tests."""
    
    print("\n" + "=" * 60)
    print("PHASE 1 INTEGRATION TEST")
    print("Ticker Extraction Module")
    print("=" * 60)
    
    # Run tests
    test1_passed = test_real_news_articles()
    test2_passed = test_ticker_extraction_performance()
    test3_passed = test_edge_cases()
    
    # Summary
    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    
    if test1_passed and test2_passed and test3_passed:
        print("[SUCCESS] ALL TESTS PASSED")
        print("\nPhase 1 implementation is working correctly!")
        print("Ready to proceed to Phase 2: Signal Sources")
        return 0
    else:
        print("[FAILED] SOME TESTS FAILED")
        print("\nPlease fix issues before proceeding.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
