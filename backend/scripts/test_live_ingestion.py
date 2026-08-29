"""
Live test script for news ingestion system.

This script:
1. Initializes the database
2. Runs the ingestion cycle
3. Shows detailed results
4. Verifies articles were stored
"""

import asyncio
import sys
import os
from datetime import datetime, timezone

# Add backend to path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set DATABASE_URL to a local file for testing
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'test_trading_system.db')


async def init_database():
    """Initialize the database tables."""
    print("Initializing database...")
    
    from database.engine import engine
    from database.models import Base
    
    # Create all tables
    Base.metadata.create_all(bind=engine)
    print("[OK] Database initialized successfully")


async def test_live_ingestion():
    """Test the ingestion cycle live."""
    print("\n" + "=" * 60)
    print("LIVE INGESTION TEST - ENHANCED NEWS SYSTEM")
    print("=" * 60)
    
    from services.data_ingestion.worker import run_ingestion_cycle
    from database.engine import SessionLocal
    from database.models import ScrapedArticle
    
    print(f"\nStarting ingestion cycle at {datetime.now(timezone.utc).isoformat()}...")
    
    try:
        # Run ingestion
        result = await run_ingestion_cycle()
        
        print("\n" + "=" * 60)
        print("INGESTION RESULTS")
        print("=" * 60)
        
        # Pretty print results
        for key, value in result.items():
            print(f"  {key}: {value}")
        
        print("\n" + "=" * 60)
        print("DETAILED ANALYSIS")
        print("=" * 60)
        
        total = result.get('total_feed_articles', 0)
        fresh = result.get('stage0_matches', 0)
        stored = result.get('stored_count', 0)
        dups = result.get('duplicate_count', 0)
        fast_lane = result.get('fast_lane_article_ids', [])
        
        print(f"\nTotal articles fetched from RSS feeds: {total}")
        print(f"Articles passing 1-hour freshness filter: {fresh}")
        print(f"New articles stored in database: {stored}")
        print(f"Duplicate articles skipped: {dups}")
        print(f"Fast-lane articles detected: {len(fast_lane)}")
        
        if total > 0:
            freshness_rate = (fresh / total * 100) if total > 0 else 0
            print(f"\nFreshness rate: {freshness_rate:.1f}% (within 1 hour)")
        
        # Verify database storage
        if stored > 0:
            print(f"\n[SUCCESS] {stored} new articles were stored!")
            
            # Show recent articles
            db = SessionLocal()
            try:
                recent = db.query(ScrapedArticle).order_by(
                    ScrapedArticle.discovered_at.desc()
                ).limit(5).all()
                
                print("\nMost recent articles stored:")
                for article in recent:
                    pub_time = article.published_at.strftime('%H:%M UTC') if article.published_at else 'Unknown'
                    print(f"  - [{pub_time}] {article.source}: {article.title[:60]}...")
            finally:
                db.close()
        elif dups > 0:
            print(f"\n[INFO] No new articles - {dups} duplicates found (already processed)")
        else:
            print(f"\n[WARNING] No articles were processed.")
            print("  Possible causes:")
            print("  - No articles in the last hour (try relaxing freshness filter)")
            print("  - Articles don't match tracked symbols (check Stage 0 filter)")
            print("  - RSS feeds are empty or blocked")
        
        return result
        
    except Exception as exc:
        print(f"\n[ERROR] Ingestion failed: {exc}")
        import traceback
        traceback.print_exc()
        return None


async def main():
    """Run the complete test."""
    print("=" * 60)
    print("NEWS INGESTION LIVE TEST")
    print("=" * 60)
    
    # Initialize database
    await init_database()
    
    # Run ingestion test
    result = await test_live_ingestion()
    
    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)
    
    if result and result.get('stored_count', 0) > 0:
        print("\n[OVERALL SUCCESS] New articles were ingested!")
        print("The enhanced news system is working correctly.")
    else:
        print("\n[OVERALL STATUS] Check the analysis above for details.")
    
    # Cleanup test database
    test_db = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'test_trading_system.db')
    if os.path.exists(test_db):
        print(f"\nTest database created at: {test_db}")
        print("You can delete it or keep it for further testing.")


if __name__ == "__main__":
    asyncio.run(main())
