"""
Management script to configure news sources for 1-hour freshness.

This script helps you:
1. View current news source configuration
2. Enable/disable source categories
3. Set polling intervals
4. Test the ingestion system
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def show_current_config():
    """Show current news source configuration."""
    print("\n=== Current News Source Configuration ===")
    
    from config.news_sources import get_all_sources, NEWS_SOURCES
    
    for category, sources in NEWS_SOURCES.items():
        print(f"\n{category.upper()} ({len(sources)} sources):")
        for source in sources:
            status = "[ENABLED]" if source.enabled else "[DISABLED]"
            print(f"  {status} {source.name}")
            print(f"         URL: {source.url[:80]}...")
            print(f"         Type: {source.source_type.value}")
            print(f"         Priority: {source.priority}, Interval: {source.fetch_interval_minutes}min")


async def test_ingestion():
    """Test the ingestion cycle with new sources."""
    print("\n=== Testing Ingestion Cycle ===")
    
    from services.data_ingestion.worker import run_ingestion_cycle
    
    try:
        result = await run_ingestion_cycle()
        print("\n[PASS] Ingestion cycle completed successfully!")
        print("\nResults:")
        for key, value in result.items():
            print(f"  {key}: {value}")
        return True
    except Exception as exc:
        print(f"\n[FAIL] Ingestion cycle failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Main menu for news source management."""
    print("=" * 60)
    print("News Source Configuration Manager")
    print("=" * 60)
    
    while True:
        print("\nOptions:")
        print("  1. Show current configuration")
        print("  2. Test ingestion cycle")
        print("  3. Exit")
        
        choice = input("\nSelect option (1-3): ").strip()
        
        if choice == "1":
            await show_current_config()
        elif choice == "2":
            await test_ingestion()
        elif choice == "3":
            print("\nExiting...")
            break
        else:
            print("\nInvalid option. Please try again.")


if __name__ == "__main__":
    asyncio.run(main())
