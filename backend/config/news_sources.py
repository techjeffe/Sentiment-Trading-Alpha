"""
News source configuration for 1-hour freshness requirement.

PREFER DIRECT RSS FEEDS over Google News RSS whenever possible.
Direct RSS feeds avoid redirect issues and are more reliable.

Strategy:
1. Use direct RSS feeds from publishers (CNBC, Reuters, Bloomberg, etc.)
2. Only use Google News RSS as fallback for publishers without RSS
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
        # Reuters - direct RSS failed (401), use Google News as fallback
        NewsSource(
            name="Reuters Markets",
            url="https://news.google.com/rss/search?q=site:reuters.com+markets+stocks+when:1h&hl=en-US&gl=US&ceid=US:en",
            source_type=SourceType.GOOGLE_NEWS,
            category="markets",
            priority=1,
            fetch_interval_minutes=15,
        ),
        # Bloomberg doesn't offer free RSS, use Google News as fallback
        NewsSource(
            name="Bloomberg Markets",
            url="https://news.google.com/rss/search?q=site:bloomberg.com+markets+when:1h&hl=en-US&gl=US&ceid=US:en",
            source_type=SourceType.GOOGLE_NEWS,
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
        # Forex Factory RSS failed (403), use Google News instead
        NewsSource(
            name="Forex News (Google)",
            url='https://news.google.com/rss/search?q=("forex"+OR+"currency")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
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
        # ECB RSS failed (404), use Google News instead
        NewsSource(
            name="ECB Press Releases (Google)",
            url="https://news.google.com/rss/search?q=site:ecb.europa.eu+press+release+when:1h&hl=en-US&gl=US&ceid=US:en",
            source_type=SourceType.GOOGLE_NEWS,
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
        NewsSource(
            name="Bond Market (Google)",
            url='https://news.google.com/rss/search?q=("bond+market"+OR+"treasury+yields"+OR+"bond+yields")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="bonds",
            priority=2,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Treasury Watch (Google)",
            url='https://news.google.com/rss/search?q=("US+Treasury"+OR+"10-year+yield"+OR+"2-year+yield")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="bonds",
            priority=1,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Corporate Bonds (Google)",
            url='https://news.google.com/rss/search?q=("corporate+bond"+OR+"high+yield"+OR+"credit+spread")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="bonds",
            priority=3,
            fetch_interval_minutes=30,
        ),
    ],
    
    # Commodities & Futures
    "commodities": [
        # OilPrice.com RSS failed (404), use Google News instead
        NewsSource(
            name="Oil & Gas (Google)",
            url='https://news.google.com/rss/search?q=(oil+price+OR+OPEC+OR+"crude+oil"+OR+WTI+OR+Brent)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="commodities",
            priority=1,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Gold & Metals (Google)",
            url='https://news.google.com/rss/search?q=(gold+price+OR+silver+price+OR+copper+OR+"precious+metals")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="commodities",
            priority=1,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Agriculture (Google)",
            url='https://news.google.com/rss/search?q=(wheat+OR+corn+OR+soybeans+OR+coffee)+price+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="commodities",
            priority=2,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Commodity Trading (Google)",
            url='https://news.google.com/rss/search?q=("commodity+trading"+OR+CME+OR+NYMEX+OR+COMEX)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="commodities",
            priority=2,
            fetch_interval_minutes=30,
        ),
    ],
    
    # Economic Data & Indicators
    "economic": [
        NewsSource(
            name="Economic Data (Google)",
            url='https://news.google.com/rss/search?q=(CPI+OR+inflation+OR+GDP+OR+"jobs+report")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="economic",
            priority=1,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Trade & Tariffs (Google)",
            url='https://news.google.com/rss/search?q=(tariff+OR+"trade+war"+OR+"trade+deficit")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="economic",
            priority=1,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Housing Market (Google)",
            url='https://news.google.com/rss/search?q=("housing+market"+OR+"home+prices"+OR+"mortgage+rates")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="economic",
            priority=2,
            fetch_interval_minutes=30,
        ),
    ],
    
    # IPOs & Earnings
    "ipo": [
        NewsSource(
            name="IPO News (Google)",
            url='https://news.google.com/rss/search?q=(IPO+OR+"initial+public+offering"+OR+SPAC)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="ipo",
            priority=2,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Earnings Reports (Google)",
            url='https://news.google.com/rss/search?q=("earnings+report"+OR+"quarterly+earnings"+OR+"revenue")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="ipo",
            priority=1,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="M&A News (Google)",
            url='https://news.google.com/rss/search?q=("merger"+OR+"acquisition"+OR+"takeover")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="ipo",
            priority=2,
            fetch_interval_minutes=30,
        ),
    ],
    
    # Derivatives & Options
    "derivatives": [
        NewsSource(
            name="Options Market (Google)",
            url='https://news.google.com/rss/search?q=("options+market"+OR+"put+call+ratio"+OR+VIX)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="derivatives",
            priority=2,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Futures Trading (Google)",
            url='https://news.google.com/rss/search?q=("futures+trading"+OR+"S%26P+500+futures")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="derivatives",
            priority=2,
            fetch_interval_minutes=30,
        ),
    ],
    
    # Fintech & Trading Technology
    "fintech": [
        NewsSource(
            name="Fintech News (Google)",
            url='https://news.google.com/rss/search?q=(fintech+OR+"payment+technology"+OR+neobank)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="fintech",
            priority=3,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Trading Tech (Google)",
            url='https://news.google.com/rss/search?q=("algorithmic+trading"+OR+"trading+platform")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="fintech",
            priority=3,
            fetch_interval_minutes=30,
        ),
    ],
    
    # Institutional Investors
    "institutional": [
        NewsSource(
            name="Hedge Fund News (Google)",
            url='https://news.google.com/rss/search?q=("hedge+fund"+OR+Bridgewater+OR+Citadel)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="institutional",
            priority=3,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Private Equity (Google)",
            url='https://news.google.com/rss/search?q=("private+equity"+OR+Blackstone+OR+KKR)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="institutional",
            priority=3,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Sovereign Wealth (Google)",
            url='https://news.google.com/rss/search?q=("sovereign+wealth"+OR+"pension+fund")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="institutional",
            priority=3,
            fetch_interval_minutes=30,
        ),
    ],
    
    # GCC Business & Investment News
    "gcc": [
        NewsSource(
            name="Arabian Business (Google)",
            url='https://news.google.com/rss/search?q=site:arabianbusiness.com+(Saudi+Arabia+OR+UAE)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="gcc",
            priority=3,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Gulf FDI (Google)",
            url='https://news.google.com/rss/search?q=(PIF+OR+"DP+World"+OR+Mubadala+OR+ADNOC)+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="gcc",
            priority=3,
            fetch_interval_minutes=30,
        ),
    ],
    
    # Market Analysis & Outlook
    "analysis": [
        NewsSource(
            name="Market Outlook (Google)",
            url='https://news.google.com/rss/search?q=("market+outlook"+OR+"stock+market+forecast")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="analysis",
            priority=2,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Risk & Volatility (Google)",
            url='https://news.google.com/rss/search?q=(VIX+OR+"market+volatility"+OR+"market+correction")+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="analysis",
            priority=2,
            fetch_interval_minutes=30,
        ),
        NewsSource(
            name="Bank Research (Google)",
            url='https://news.google.com/rss/search?q=("Goldman+Sachs"+OR+"JPMorgan"+OR+"Morgan+Stanley")+forecast+when:1h&hl=en-US&gl=US&ceid=US:en',
            source_type=SourceType.GOOGLE_NEWS,
            category="analysis",
            priority=2,
            fetch_interval_minutes=30,
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
