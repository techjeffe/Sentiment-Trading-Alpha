"""
News source configuration for 1-hour freshness requirement.

PREFER DIRECT RSS FEEDS over Google News RSS whenever possible.
Direct RSS feeds avoid redirect issues and are more reliable.

UPDATE (2026-07-24): Google News RSS feeds are returning 503 Service Unavailable errors.
All Google News RSS sources have been disabled (enabled=False) until alternatives are found.

Working alternatives added:
- Financial Times: https://www.ft.com/rss/world
- BBC Business: http://feeds.bbci.co.uk/news/business/rss.xml
- Al Jazeera Business: https://www.aljazeera.com/xml/rss/all.xml
- The Guardian Business: https://www.theguardian.com/business/rss

Strategy:
1. Use direct RSS feeds from publishers (CNBC, Reuters, Bloomberg, etc.)
2. Google News RSS is DEPRECATED - all sources disabled due to 503 errors
3. All feeds polled frequently (every 15-30 minutes)
"""

from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum


class SourceType(Enum):
    """Type of news source."""
    DIRECT_RSS = "direct_rss"
    GOOGLE_NEWS = "google_news"
    YAHOO_FINANCE = "yahoo_finance"


@dataclass
class NewsSource:
    """Configuration for a single news source."""
    name: str
    url: str
    source_type: SourceType
    category: str
    priority: int = 1  # 1=highest, 5=lowest
    enabled: bool = True
    fetch_interval_minutes: int = 30  # How often to poll this source
    freshness_hours: float = 1.0  # Max age of articles to accept


# ─── Source Configurations (Prefer Direct RSS) ───────────────────────────

NEWS_SOURCES = {
    # Markets - DIRECT RSS FEEDS (preferred!)
    "markets": [
        NewsSource(
            name="CNBC",
            url="https://www.cnbc.com/id/100003114/device/rss/rss.html",
            source_type=SourceType.DIRECT_RSS,
            category="markets",
            priority=1,
            fetch_interval_minutes=15,
        ),
        NewsSource(
            name="Yahoo Finance",
            url="https://finance.yahoo.com/rss/topstories",
            source_type=SourceType.DIRECT_RSS,
            category="markets",
            priority=1,
            fetch_interval_minutes=15,
        ),
        NewsSource(
            name="Seeking Alpha",
            url="https://seekingalpha.com/market_currents.xml",
            source_type=SourceType.DIRECT_RSS,
            category="markets",
            priority=2,
            fetch_interval_minutes=30,
        ),
        # Reuters - direct RSS failed (401), use Financial Times as alternative
        NewsSource(
            name="Financial Times Markets",
            url="https://www.ft.com/rss/world",
            source_type=SourceType.DIRECT_RSS,
            category="markets",
            priority=1,
            fetch_interval_minutes=15,
        ),
        # Bloomberg doesn't offer free RSS, use BBC Business as alternative
        NewsSource(
            name="BBC Business",
            url="http://feeds.bbci.co.uk/news/business/rss.xml",
            source_type=SourceType.DIRECT_RSS,
            category="markets",
            priority=2,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="MarketWatch",
            url="https://feeds.marketwatch.com/marketwatch/topstories/",
            source_type=SourceType.DIRECT_RSS,
            category="markets",
            priority=2,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Investing.com",
            url="https://www.investing.com/rss/news_301.rss",
            source_type=SourceType.DIRECT_RSS,
            category="markets",
            priority=3,
            fetch_interval_minutes=30,
        ),
    ],
    
    # Forex & Currencies
    "forex": [
        # Forex Factory RSS failed (403), use Al Jazeera Business as alternative
        NewsSource(
            name="Al Jazeera Business",
            url="https://www.aljazeera.com/xml/rss/all.xml",
            source_type=SourceType.DIRECT_RSS,
            category="forex",
            priority=2,
            fetch_interval_minutes=30,
        ),
    ],
    
    # Crypto - DIRECT RSS FEEDS (most crypto sites have RSS!)
    "crypto": [
        NewsSource(
            name="CoinDesk",
            url="https://www.coindesk.com/arc/outboundfeeds/rss/",
            source_type=SourceType.DIRECT_RSS,
            category="crypto",
            priority=1,
            fetch_interval_minutes=15,
        ),
        NewsSource(
            name="Cointelegraph",
            url="https://cointelegraph.com/rss",
            source_type=SourceType.DIRECT_RSS,
            category="crypto",
            priority=1,
            fetch_interval_minutes=15,
        ),
        NewsSource(
            name="Bitcoin Magazine",
            url="https://bitcoinmagazine.com/feed",
            source_type=SourceType.DIRECT_RSS,
            category="crypto",
            priority=2,
            fetch_interval_minutes=30,
        ),
    ],
    
    # Central Banks - DIRECT RSS for official sources!
    "central_banks": [
        NewsSource(
            name="Federal Reserve",
            url="https://www.federalreserve.gov/feeds/press_all.xml",
            source_type=SourceType.DIRECT_RSS,
            category="central_banks",
            priority=1,
            fetch_interval_minutes=30,
        ),
        # ECB RSS failed (404), use Financial Times as alternative
        NewsSource(
            name="Financial Times (ECB Coverage)",
            url="https://www.ft.com/rss/world",
            source_type=SourceType.DIRECT_RSS,
            category="central_banks",
            priority=1,
            fetch_interval_minutes=30,
        ),
    ],
    
    # Regulation - DIRECT RSS for official sources
    "regulation": [
        NewsSource(
            name="SEC",
            url="https://www.sec.gov/news/pressreleases.rss",
            source_type=SourceType.DIRECT_RSS,
            category="regulation",
            priority=1,
            fetch_interval_minutes=30,
        ),
    ],
    
    # Fixed Income & Bonds
    "bonds": [
        # Google News RSS deprecated - 503 errors
        # TODO: Find direct RSS alternatives for bond market news
        NewsSource(
            name="Treasury Watch (Disabled - Google News 503)",
            url='https://news.google.com/rss/search?q=("US+Treasury"+OR+"10-year+yield"+OR+"2-year+yield")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="bonds",
            priority=1,
            fetch_interval_minutes=30,
            enabled=False,  # Disabled due to 503 errors
        ),
    ],
    
    # Commodities & Futures
    "commodities": [
        # Google News RSS deprecated - 503 errors
        # TODO: Find direct RSS alternatives for commodities news
        NewsSource(
            name="Commodities (Disabled - Google News 503)",
            url='https://news.google.com/rss/search?q=(oil+price+OR+OPEC+OR+"crude+oil"+OR+WTI+OR+Brent)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="commodities",
            priority=1,
            fetch_interval_minutes=30,
            enabled=False,  # Disabled due to 503 errors
        ),
    ],
    
    # Economic Data & Indicators
    "economic": [
        # Google News RSS deprecated - 503 errors
        # TODO: Find direct RSS alternatives for economic data news
        NewsSource(
            name="Economic Data (Disabled - Google News 503)",
            url='https://news.google.com/rss/search?q=(CPI+OR+inflation+OR+GDP+OR+"jobs+report")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="economic",
            priority=1,
            fetch_interval_minutes=30,
            enabled=False,  # Disabled due to 503 errors
        ),
    ],
    
    # IPOs & Earnings
    "ipo": [
        # Google News RSS deprecated - 503 errors
        NewsSource(
            name="IPO News (Disabled - Google News 503)",
            url='https://news.google.com/rss/search?q=(IPO+OR+"initial+public+offering"+OR+SPAC)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="ipo",
            priority=2,
            fetch_interval_minutes=30,
            enabled=False,  # Disabled due to 503 errors
        ),
    ],
    
    # Derivatives & Options
    "derivatives": [
        # Google News RSS deprecated - 503 errors
        NewsSource(
            name="Options Market (Disabled - Google News 503)",
            url='https://news.google.com/rss/search?q=("options+market"+OR+"put+call+ratio"+OR+VIX)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="derivatives",
            priority=2,
            fetch_interval_minutes=30,
            enabled=False,  # Disabled due to 503 errors
        ),
    ],
    
    # Fintech & Trading Technology
    "fintech": [
        # Google News RSS deprecated - 503 errors
        NewsSource(
            name="Fintech News (Disabled - Google News 503)",
            url='https://news.google.com/rss/search?q=(fintech+OR+"payment+technology"+OR+neobank)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="fintech",
            priority=3,
            fetch_interval_minutes=30,
            enabled=False,  # Disabled due to 503 errors
        ),
    ],
    
    # Institutional Investors
    "institutional": [
        # Google News RSS deprecated - 503 errors
        NewsSource(
            name="Hedge Fund News (Disabled - Google News 503)",
            url='https://news.google.com/rss/search?q=("hedge+fund"+OR+Bridgewater+OR+Citadel)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="institutional",
            priority=3,
            fetch_interval_minutes=30,
            enabled=False,  # Disabled due to 503 errors
        ),
    ],
    
    # GCC Business & Investment News
    "gcc": [
        # Google News RSS deprecated - 503 errors
        NewsSource(
            name="Gulf Business (Disabled - Google News 503)",
            url='https://news.google.com/rss/search?q=site:arabianbusiness.com+(Saudi+Arabia+OR+UAE)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="gcc",
            priority=3,
            fetch_interval_minutes=30,
            enabled=False,  # Disabled due to 503 errors
        ),
    ],
    
    # Market Analysis & Outlook
    "analysis": [
        # Google News RSS deprecated - 503 errors
        NewsSource(
            name="Market Outlook (Disabled - Google News 503)",
            url='https://news.google.com/rss/search?q=("market+outlook"+OR+"stock+market+forecast")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="analysis",
            priority=2,
            fetch_interval_minutes=30,
            enabled=False,  # Disabled due to 503 errors
        ),
    ],
}


def get_all_sources() -> List[NewsSource]:
    """Get all news sources as a flat list."""
    all_sources = []
    for category_sources in NEWS_SOURCES.values():
        all_sources.extend(category_sources)
    return all_sources


def get_enabled_sources() -> List[NewsSource]:
    """Get all enabled sources."""
    return [s for s in get_all_sources() if s.enabled]


def build_rss_feed_map(sources: Optional[List[NewsSource]] = None) -> Dict[str, str]:
    """Build a feed map compatible with existing RSSFeedParser."""
    if sources is None:
        sources = get_enabled_sources()
    
    return {
        source.name: source.url
        for source in sources
    }
