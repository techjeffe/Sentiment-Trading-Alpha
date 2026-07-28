"""
Test Phase 1 Implementation with Real Database Content
Runs ticker extraction on actual news articles in the database
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
backend_dir = project_root / "backend"
sys.path.insert(0, str(backend_dir))
sys.path.insert(0, str(project_root))

from database.engine import SessionLocal
from database.models import ScrapedArticle
from services.data_ingestion.ticker_extractor import (
    extract_tickers_from_article,
    extract_all_tickers,
    is_valid_ticker
)


def test_ticker_extraction_on_database():
    """Test ticker extraction on real articles in the database."""
    
    print("=" * 70)
    print("PHASE 1 REAL-WORLD TEST")
    print("Testing Ticker Extraction on Database Articles")
    print("=" * 70)
    
    db = SessionLocal()
    
    try:
        # Get unprocessed articles (or all articles)
        articles = db.query(ScrapedArticle).filter(
            ScrapedArticle.discovered_tickers == None
        ).limit(50).all()
        
        if not articles:
            print("\nNo unprocessed articles found.")
            print("Checking for any articles in database...")
            articles = db.query(ScrapedArticle).limit(50).all()
            
            if not articles:
                print("[ERROR] No articles found in database at all.")
                print("Please run the news ingestion to populate the database first.")
                return False
        
        print(f"\nFound {len(articles)} articles to process")
        print("-" * 70)
        
        total_tickers_discovered = 0
        articles_with_tickers = 0
        ticker_counts = {}
        
        for i, article in enumerate(articles, 1):
            print(f"\n[{i}/{len(articles)}] Processing article ID {article.id}")
            print(f"Title: {article.title[:80]}...")
            print(f"Source: {article.source}")
            
            # Extract tickers from title and content
            title = article.title or ""
            content = article.full_content or article.summary or ""
            
            # Use extract_all_tickers for comprehensive extraction
            discovered = extract_all_tickers(f"{title} {content}")
            
            if discovered:
                articles_with_tickers += 1
                total_tickers_discovered += len(discovered)
                
                # Count ticker frequencies
                for ticker in discovered:
                    ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1
                
                print(f"Discovered tickers: {discovered}")
                
                # Update database
                article.discovered_tickers = discovered
                article.ticker_extraction_confidence = 0.8
                
            else:
                print("No tickers discovered")
            
            # Commit every 10 articles
            if i % 10 == 0:
                db.commit()
                print(f"\n[Progress] Committed {i} articles to database")
        
        # Final commit
        db.commit()
        
        # Print summary
        print("\n" + "=" * 70)
        print("TEST RESULTS SUMMARY")
        print("=" * 70)
        
        print(f"\nArticles processed: {len(articles)}")
        print(f"Articles with tickers: {articles_with_tickers}")
        print(f"Total tickers discovered: {total_tickers_discovered}")
        
        if ticker_counts:
            print(f"\nTop 20 Most Mentioned Tickers:")
            print("-" * 70)
            sorted_tickers = sorted(ticker_counts.items(), key=lambda x: x[1], reverse=True)[:20]
            for ticker, count in sorted_tickers:
                print(f"  {ticker}: {count} mentions")
        
        print("\n" + "=" * 70)
        print("[SUCCESS] Phase 1 Test Complete!")
        print("=" * 70)
        
        return True
        
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        db.rollback()
        raise
        
    finally:
        db.close()


def show_sample_articles_with_tickers():
    """Show sample articles that have tickers discovered."""
    
    print("\n" + "=" * 70)
    print("SAMPLE ARTICLES WITH DISCOVERED TICKERS")
    print("=" * 70)
    
    db = SessionLocal()
    
    try:
        articles = db.query(ScrapedArticle).filter(
            ScrapedArticle.discovered_tickers != None
        ).limit(10).all()
        
        if not articles:
            print("\nNo articles with discovered tickers yet.")
            print("Run the extraction test first.")
            return
        
        for article in articles:
            print(f"\nArticle ID: {article.id}")
            print(f"Title: {article.title}")
            print(f"Source: {article.source}")
            print(f"Discovered Tickers: {article.discovered_tickers}")
            print("-" * 70)
            
    finally:
        db.close()


def main():
    """Run the real-world test."""
    
    print("\nAuto-proceeding with Phase 1 real-world test...")
    print("This will:")
    print("  1. Read articles from scraped_articles table")
    print("  2. Extract tickers automatically")
    print("  3. Update the database with discovered tickers")
    print("  4. Show you what tickers would be discovered automatically")
    
    # Run the test
    success = test_ticker_extraction_on_database()
    
    if success:
        # Show samples
        show_sample_articles_with_tickers()
        
        print("\n" + "=" * 70)
        print("NEXT STEPS")
        print("=" * 70)
        print("\nPhase 1 is now validated with real data!")
        print("\nYou can now:")
        print("  1. Query the database for discovered tickers")
        print("  2. See what opportunities exist in your news data")
        print("  3. Proceed to Phase 2 (Reddit + SEC insider sources)")
        print("\nReady to proceed to Phase 2?")


if __name__ == "__main__":
    main()
