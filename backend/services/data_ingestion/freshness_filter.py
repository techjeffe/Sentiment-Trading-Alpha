"""
Freshness filter for news articles.

Ensures only articles published within the configured time window
(default 1 hour) are processed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from services.data_ingestion.parser import NewsArticle

logger = logging.getLogger(__name__)


class FreshnessFilter:
    """
    Filters news articles based on publish date freshness.
    
    Default: Only accept articles published within the last 1 hour.
    """
    
    def __init__(self, max_age_hours: float = 1.0):
        """
        Initialize freshness filter.
        
        Args:
            max_age_hours: Maximum age of articles to accept (default: 1 hour)
        """
        self.max_age_hours = max_age_hours
        self.max_age_delta = timedelta(hours=max_age_hours)
    
    def is_fresh(self, article: NewsArticle, reference_time: Optional[datetime] = None) -> bool:
        """
        Check if an article is fresh (published within the time window).
        
        Args:
            article: News article to check
            reference_time: Reference time for comparison (default: now UTC)
            
        Returns:
            True if article is fresh, False otherwise
        """
        if not article.published_date:
            # If no publish date, assume it's fresh (will be caught by other filters)
            logger.debug(f"No publish date for article: {article.title}")
            return True
        
        if reference_time is None:
            reference_time = datetime.now(timezone.utc)
        
        # Ensure both datetimes are timezone-aware
        pub_date = article.published_date
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=timezone.utc)
        
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        
        age = reference_time - pub_date
        
        is_fresh = age <= self.max_age_delta
        
        if not is_fresh:
            logger.debug(
                f"Article too old: {article.title} "
                f"(age: {age.total_seconds()/3600:.1f} hours, "
                f"max: {self.max_age_hours} hours)"
            )
        
        return is_fresh
    
    def filter_articles(
        self,
        articles: List[NewsArticle],
        reference_time: Optional[datetime] = None,
    ) -> List[NewsArticle]:
        """
        Filter a list of articles, keeping only fresh ones.
        
        Args:
            articles: List of news articles
            reference_time: Reference time for comparison (default: now UTC)
            
        Returns:
            List of fresh articles
        """
        if reference_time is None:
            reference_time = datetime.now(timezone.utc)
        
        fresh_articles = []
        stale_count = 0
        
        for article in articles:
            if self.is_fresh(article, reference_time):
                fresh_articles.append(article)
            else:
                stale_count += 1
        
        if stale_count > 0:
            logger.info(
                f"Freshness filter: {stale_count} articles removed "
                f"(older than {self.max_age_hours} hours), "
                f"{len(fresh_articles)} articles kept"
            )
        
        return fresh_articles
    
    def update_max_age(self, max_age_hours: float):
        """Update the maximum age setting."""
        self.max_age_hours = max_age_hours
        self.max_age_delta = timedelta(hours=max_age_hours)


def filter_fresh_articles(
    articles: List[NewsArticle],
    max_age_hours: float = 1.0,
    reference_time: Optional[datetime] = None,
) -> List[NewsArticle]:
    """
    Convenience function to filter articles by freshness.
    
    Args:
        articles: List of news articles
        max_age_hours: Maximum age in hours (default: 1.0)
        reference_time: Reference time (default: now UTC)
        
    Returns:
        List of fresh articles
    """
    freshness_filter = FreshnessFilter(max_age_hours=max_age_hours)
    return freshness_filter.filter_articles(articles, reference_time)


# ─── Testing ─────────────────────────────────────────────────────────────────

def test_freshness_filter():
    """Test the freshness filter with sample articles."""
    from datetime import datetime, timezone, timedelta
    from services.data_ingestion.parser import NewsArticle
    
    now = datetime.now(timezone.utc)
    
    # Create test articles with different ages
    test_articles = [
        NewsArticle(
            title="Fresh article (10 min old)",
            link="https://example.com/1",
            published_date=now - timedelta(minutes=10),
            source="test",
        ),
        NewsArticle(
            title="Stale article (2 hours old)",
            link="https://example.com/2",
            published_date=now - timedelta(hours=2),
            source="test",
        ),
        NewsArticle(
            title="Borderline article (1 hour old)",
            link="https://example.com/3",
            published_date=now - timedelta(hours=1),
            source="test",
        ),
        NewsArticle(
            title="No publish date",
            link="https://example.com/4",
            published_date=None,
            source="test",
        ),
    ]
    
    print("\n=== Testing Freshness Filter (1 hour max age) ===")
    
    freshness_filter = FreshnessFilter(max_age_hours=1.0)
    
    for article in test_articles:
        is_fresh = freshness_filter.is_fresh(article)
        age_str = ""
        if article.published_date:
            age = now - article.published_date
            age_str = f" (age: {age.total_seconds()/60:.0f} min)"
        print(f"{'[PASS]' if is_fresh else '[FAIL]'} {article.title}{age_str}")
    
    # Test filtering
    print("\n=== Testing Filter Articles ===")
    fresh_articles = freshness_filter.filter_articles(test_articles)
    print(f"Kept {len(fresh_articles)} out of {len(test_articles)} articles")
    
    for article in fresh_articles:
        print(f"  - {article.title}")


if __name__ == "__main__":
    test_freshness_filter()
