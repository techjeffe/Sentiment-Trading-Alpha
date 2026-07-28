"""
Test Full Pipeline with Real Database
Runs the complete Phase 1-3 pipeline on real news articles.
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

# Add paths
project_root = Path(__file__).parent.parent
backend_dir = project_root / "backend"
sys.path.insert(0, str(backend_dir))
sys.path.insert(0, str(project_root))

# Import database and services
try:
    from backend.database.engine import SessionLocal
    from backend.database.models import ScrapedArticle
except ImportError:
    # Try alternative import
    from database.engine import SessionLocal
    from database.models import ScrapedArticle

from services.data_ingestion.ticker_extractor import extract_tickers_from_article
from services.data_ingestion.sec_insider_client import fetch_sec_insider_signals
from services.analysis.signal_aggregator import aggregate_signals
from services.risk.pump_dump_detector import check_pnd_flags
from services.scoring.advanced_scorer import AggregatedSignal
from services.scoring.scoring_service import score_symbols_with_ai


def fetch_recent_articles(days_back: int = 7, limit: int = 100):
    """
    Fetch recent articles from database.
    
    Args:
        days_back: Number of days to look back
        limit: Maximum number of articles to fetch
        
    Returns:
        List of ScrapedArticle objects
    """
    db = SessionLocal()
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days_back)
        
        articles = db.query(ScrapedArticle).filter(
            ScrapedArticle.discovered_at >= cutoff_date
        ).limit(limit).all()
        
        print(f"Fetched {len(articles)} articles from last {days_back} days")
        return articles
    finally:
        db.close()


def extract_tickers_from_articles(articles):
    """
    Extract tickers from articles using Phase 1 ticker extractor.
    
    Returns:
        Dict mapping article ID to list of tickers
    """
    article_tickers = {}
    
    for article in articles:
        title = article.title or ""
        content = article.full_content or article.summary or ""
        
        # Extract tickers using Phase 1 module
        tickers = extract_tickers_from_article(title, content)
        
        if tickers:
            article_tickers[article.id] = {
                "title": title[:80],
                "source": article.source,
                "tickers": tickers
            }
    
    return article_tickers


def build_signals_from_articles(article_tickers):
    """
    Build RawSignal objects from article ticker extractions.
    
    Returns:
        List of RawSignal-like dicts
    """
    from services.data_ingestion.reddit_client import RawSignal
    
    signals = []
    
    for article_id, data in article_tickers.items():
        for ticker in data["tickers"]:
            signal = RawSignal(
                symbol=ticker,
                source="NEWS",
                title=data["title"],
                body=f"Source: {data['source']}",
                url="",
                author=None,
                upvotes=1,  # News articles get base upvote
                comment_count=0,
                subreddit=None,
                post_age=24,  # Assume 24 hours old
                sort_type="new",
                flair=None
            )
            signals.append(signal)
    
    return signals


def run_full_pipeline():
    """Run the full Phase 1-3 pipeline on real data."""
    print("=" * 70)
    print("FULL PIPELINE TEST WITH REAL DATABASE")
    print("Phase 1: Ticker Extraction")
    print("Phase 2: Signal Aggregation")
    print("Phase 3: Scoring + P&D Detection")
    print("=" * 70)
    
    # Step 1: Fetch recent articles
    print("\n[Step 1] Fetching recent articles from database...")
    articles = fetch_recent_articles(days_back=30, limit=50)
    
    if not articles:
        print("No articles found. Run news ingestion first.")
        return
    
    # Step 2: Extract tickers (Phase 1)
    print("\n[Step 2] Extracting tickers from articles (Phase 1)...")
    article_tickers = extract_tickers_from_articles(articles)
    
    all_discovered_tickers = set()
    for data in article_tickers.values():
        all_discovered_tickers.update(data["tickers"])
    
    print(f"Discovered {len(all_discovered_tickers)} unique tickers from news:")
    print(f"  {', '.join(sorted(all_discovered_tickers)[:20])}")
    if len(all_discovered_tickers) > 20:
        print(f"  ... and {len(all_discovered_tickers) - 20} more")
    
    # Step 3: Build signals from articles
    print("\n[Step 3] Building signals from articles...")
    news_signals = build_signals_from_articles(article_tickers)
    print(f"Created {len(news_signals)} signals from news articles")
    
    # Step 4: Fetch SEC insider signals (Phase 2)
    print("\n[Step 4] Fetching SEC insider signals (Phase 2)...")
    try:
        sec_signals = fetch_sec_insider_signals()
        print(f"Fetched {len(sec_signals)} SEC insider signals")
    except Exception as e:
        print(f"SEC insider fetch error: {e}")
        sec_signals = []
    
    # Step 5: Aggregate all signals (Phase 2)
    print("\n[Step 5] Aggregating signals by ticker (Phase 2)...")
    all_signals = news_signals + sec_signals
    aggregated = aggregate_signals(all_signals)
    print(f"Aggregated into {len(aggregated)} tickers")
    
    if not aggregated:
        print("No aggregated signals. Exiting.")
        return
    
    # Show top aggregated tickers
    print("\nTop 10 aggregated tickers:")
    for i, agg in enumerate(aggregated[:10], 1):
        print(f"  {i}. {agg.symbol}: {len(agg.signals)} signals from {agg.source_count} sources")
        print(f"     Sources: {', '.join(set(s.source for s in agg.signals))}")
    
    # Step 6: Score and rank opportunities (Phase 3)
    print("\n[Step 6] Scoring and ranking opportunities (Phase 3)...")
    try:
        scored_results = score_symbols_with_ai(aggregated)
        
        if scored_results:
            print(f"\nScored {len(scored_results)} opportunities:")
            for symbol, result in sorted(scored_results.items(), key=lambda x: x[1].score, reverse=True)[:10]:
                print(f"  {symbol}: {result.score}/100 - {result.sentiment}")
                print(f"    {result.reasoning}")
        else:
            print("No scoring results returned")
    except Exception as e:
        print(f"Scoring error: {e}")
        import traceback
        traceback.print_exc()
    
    # Step 7: Check for pump-and-dump
    print("\n[Step 7] Checking for pump-and-dump schemes...")
    pnd_flagged = []
    for agg in aggregated:
        pnd_result = check_pnd_flags(agg)
        if pnd_result.flagged:
            pnd_flagged.append((agg.symbol, pnd_result.flags))
    
    if pnd_flagged:
        print(f"\nWARNING: {len(pnd_flagged)} tickers flagged as potential P&D:")
        for symbol, flags in pnd_flagged:
            print(f"  {symbol}: {', '.join(flags)}")
    else:
        print("No pump-and-dump schemes detected ✓")
    
    print("\n" + "=" * 70)
    print("FULL PIPELINE TEST COMPLETE")
    print("=" * 70)
    print(f"Summary:")
    print(f"  - Articles processed: {len(articles)}")
    print(f"  - Tickers discovered: {len(all_discovered_tickers)}")
    print(f"  - Signals aggregated: {len(aggregated)}")
    print(f"  - Opportunities scored: {len(scored_results) if scored_results else 0}")
    print(f"  - P&D flagged: {len(pnd_flagged)}")


if __name__ == "__main__":
    run_full_pipeline()
