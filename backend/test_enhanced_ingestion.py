"""
Test script for the enhanced news ingestion system.

Tests:
1. News source configuration
2. Google News redirect resolution
3. Freshness filtering
4. Full ingestion cycle with new sources
"""

import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def test_news_sources_config():
    """Test the news source configuration."""
    print("\n=== Testing News Sources Configuration ===")
    
    from config.news_sources import (
        get_all_sources,
        get_enabled_sources,
        build_rss_feed_map,
        categorize_source,
    )
    
    all_sources = get_all_sources()
    enabled_sources = get_enabled_sources()
    
    print(f"Total sources configured: {len(all_sources)}")
    print(f"Enabled sources: {len(enabled_sources)}")
    
    # Show sources by type
    from config.news_sources import SourceType
    
    direct_rss = [s for s in enabled_sources if s.source_type == SourceType.DIRECT_RSS]
    google_news = [s for s in enabled_sources if s.source_type == SourceType.GOOGLE_NEWS]
    
    print(f"  - Direct RSS feeds: {len(direct_rss)}")
    print(f"  - Google News feeds: {len(google_news)}")
    
    # Show categories
    categories = {}
    for source in enabled_sources:
        cat = source.category
        categories[cat] = categories.get(cat, 0) + 1
    
    print("\nSources by category:")
    for cat, count in sorted(categories.items()):
        print(f"  - {cat}: {count} sources")
    
    # Test feed map generation
    feed_map = build_rss_feed_map(enabled_sources)
    print(f"\nGenerated feed map with {len(feed_map)} entries")
    
    # Test URL categorization
    test_urls = [
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://news.google.com/rss/search?q=markets+when:1h",
    ]
    
    print("\nURL categorization:")
    for url in test_urls:
        cat = categorize_source(url)
        print(f"  - {url[:60]}... -> {cat}")


async def test_google_news_resolver():
    """Test the Google News redirect resolver."""
    print("\n=== Testing Google News Resolver ===")
    
    from services.data_ingestion.google_news_resolver import GoogleNewsResolver
    
    resolver = GoogleNewsResolver(timeout=5)
    
    # Test with a sample Google News URL
    test_url = "https://news.google.com/rss/articles/CBMia2h0dHBzOi8vd3d3LmJsb29tYmVyZy5jb20vbmV3cy9hcnRpY2xlcy9tYXJrZXQtYW5hbHlzaXM"
    
    print(f"Testing resolver with URL: {test_url[:80]}...")
    
    try:
        resolved = await resolver.resolve_url(test_url)
        print(f"Resolved URL: {resolved[:80]}...")
        
        if resolved != test_url:
            print("[PASS] URL was resolved successfully")
        else:
            print("[INFO] URL was not resolved (may be expected for test URL)")
    except Exception as exc:
        print(f"[FAIL] Resolver error: {exc}")


async def test_freshness_filter():
    """Test the freshness filter."""
    print("\n=== Testing Freshness Filter ===")
    
    from services.data_ingestion.freshness_filter import FreshnessFilter
    from services.data_ingestion.parser import NewsArticle
    from datetime import datetime, timezone, timedelta
    
    now = datetime.now(timezone.utc)
    
    # Create test articles
    test_articles = [
        NewsArticle(
            title="Fresh article (30 min old)",
            link="https://example.com/1",
            source="test",
            author=None,
            published_date=now - timedelta(minutes=30),
            summary="Test summary",
            content="Test content",
            keywords=[],
        ),
        NewsArticle(
            title="Stale article (2 hours old)",
            link="https://example.com/2",
            source="test",
            author=None,
            published_date=now - timedelta(hours=2),
            summary="Test summary",
            content="Test content",
            keywords=[],
        ),
        NewsArticle(
            title="Recent article (45 min old)",
            link="https://example.com/3",
            source="test",
            author=None,
            published_date=now - timedelta(minutes=45),
            summary="Test summary",
            content="Test content",
            keywords=[],
        ),
    ]
    
    # Test with 1-hour filter
    freshness_filter = FreshnessFilter(max_age_hours=1.0)
    
    print("Testing with 1-hour max age:")
    for article in test_articles:
        is_fresh = freshness_filter.is_fresh(article)
        age_min = (now - article.published_date).total_seconds() / 60 if article.published_date else "N/A"
        print(f"  {'[PASS]' if is_fresh else '[FAIL]'} {article.title} (age: {age_min} min)")
    
    # Test filter_articles
    fresh_articles = freshness_filter.filter_articles(test_articles)
    print(f"\nFilter kept {len(fresh_articles)}/{len(test_articles)} articles")


async def test_ingestion_worker():
    """Test the full ingestion cycle with new sources."""
    print("\n=== Testing Full Ingestion Cycle ===")
    
    from services.data_ingestion.worker import run_ingestion_cycle
    
    try:
        result = await run_ingestion_cycle()
        
        print("Ingestion cycle completed successfully!")
        print(f"Results:")
        for key, value in result.items():
            print(f"  - {key}: {value}")
        
    except Exception as exc:
        print(f"[FAIL] Ingestion cycle failed: {exc}")
        import traceback
        traceback.print_exc()


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Enhanced News Ingestion System - Test Suite")
    print("=" * 60)
    
    # Run tests
    await test_news_sources_config()
    await test_google_news_resolver()
    await test_freshness_filter()
    
    # Ask user if they want to test full ingestion
    print("\n" + "=" * 60)
    response = input("Run full ingestion cycle test? (y/n): ")
    
    if response.lower() == 'y':
        await test_ingestion_worker()
    
    print("\n" + "=" * 60)
    print("Test suite completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
