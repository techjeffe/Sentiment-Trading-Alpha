"""
RSS Feed Parser using BeautifulSoup4
Parses geopolitical news feeds for sentiment analysis
"""

import feedparser
import requests
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from bs4 import BeautifulSoup
import re
from urllib.parse import urlparse

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    yf = None

try:
    from services.app_config import DEFAULT_RSS_FEEDS
    APP_CONFIG_AVAILABLE = True
except ImportError:
    APP_CONFIG_AVAILABLE = False
    DEFAULT_RSS_FEEDS = []


@dataclass
class NewsArticle:
    """Data class representing a parsed news article."""
    title: str
    link: str
    source: str
    author: Optional[str]
    published_date: datetime
    summary: str
    content: str
    keywords: List[str]


class RSSFeedParser:
    """
    Parser for geopolitical news RSS feeds using BeautifulSoup.
    
    Supports:
    - Multiple feed sources (Reuters, AP News, NYT Business, etc.)
    - HTML content extraction
    - Keyword-based filtering
    - Date-based filtering
    """
    
    # Canonical built-in feeds come from app_config so the parser and settings stay aligned.
    GEOPOLITICAL_FEEDS = {
        feed["key"]: feed["url"]
        for feed in (DEFAULT_RSS_FEEDS if APP_CONFIG_AVAILABLE else [])
    }
    
    # Keywords used to tag articles with chips in the UI (not used for routing)
    KEYWORDS = [
        "war", "conflict", "sanctions", "geopolitical",
        "oil", "energy", "crude", "opec",
        "crypto", "bitcoin", "blockchain",
        "fed", "federal reserve", "inflation", "rates",
        "trump", "tariff", "trade",
        "policy", "regulation",
        "market", "stocks", "economy", "recession",
    ]
    
    def __init__(
        self,
        timeout: int = 10,
        feeds: Optional[Dict[str, str]] = None,
        feed_labels: Optional[Dict[str, str]] = None,
    ):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        })
        self.feeds = dict(feeds or self.GEOPOLITICAL_FEEDS)
        self.feed_labels = {str(key): str(value).strip() for key, value in (feed_labels or {}).items() if str(value).strip()}
        
    def parse_feeds(
        self,
        feed_names: Optional[List[str]] = None,
        date_from: Optional[datetime] = None
    ) -> List[NewsArticle]:
        """
        Parse all configured RSS feeds.
        
        Args:
            feed_names: Optional list of specific feeds to parse
            date_from: Only include articles published after this date
            
        Returns:
            List of parsed news articles
        """
        articles = []
        
        # Select feeds to parse
        feeds_to_parse = feed_names or list(self.feeds.keys())

        for feed_name in feeds_to_parse:
            try:
                feed_url = self.feeds[feed_name]
                articles.extend(self._parse_single_feed(feed_url, date_from))
            except Exception as e:
                print(f"Error parsing {feed_name}: {e}")
        
        return articles
    
    def _parse_single_feed(
        self,
        feed_url: str,
        date_from: Optional[datetime] = None
    ) -> List[NewsArticle]:
        """Parse a single RSS feed."""
        articles = []
        
        try:
            # Fetch feed using requests (more reliable than feedparser for some feeds)
            response = self.session.get(feed_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Parse with feedparser first to get structured data
            feed = feedparser.parse(response.text)
            
            for entry in feed.entries:
                article = self._extract_article(entry, feed_url)
                
                # Filter by date if specified
                if date_from and article.published_date < date_from:
                    continue
                
                articles.append(article)
                
        except requests.RequestException as e:
            # Handle 503 Service Unavailable gracefully - Google News RSS is deprecating
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 503:
                print(f"[SKIPPED] {feed_url} - 503 Service Unavailable (Google News RSS likely deprecated)")
            else:
                print(f"Request error for {feed_url}: {e}")
        except Exception as e:
            print(f"Parse error for {feed_url}: {e}")
        
        return articles
    
    def _extract_article(
        self,
        entry: Dict[str, Any],
        feed_url: str
    ) -> NewsArticle:
        """Extract article data from feed entry."""
        # Extract title
        title = getattr(entry, 'title', '') or ''
        
        # Extract link
        link = getattr(entry, 'link', '') or ''
        
        # Extract source (from feed URL)
        source = self._get_source_name(feed_url)
        
        # Extract author
        author = getattr(entry, 'author', None) or getattr(entry, 'get_author', None)
        
        # Extract published date
        published_date = self._parse_date(getattr(entry, 'published_parsed', None))
        
        # Extract summary/description
        summary = getattr(entry, 'summary', '') or ''
        if not summary:
            summary = getattr(entry, 'description', '') or ''
        
        # Try to get full content from HTML
        content = self._extract_content_from_html(summary)
        
        # Extract keywords
        keywords = self._extract_keywords(title + " " + summary)
        
        return NewsArticle(
            title=title,
            link=link,
            source=source,
            author=author,
            published_date=published_date,
            summary=summary,
            content=content,
            keywords=keywords
        )
    
    def _get_source_name(self, feed_url: str) -> str:
        """Get human-readable source name from URL."""
        for name, url in self.feeds.items():
            if url == feed_url:
                return self.feed_labels.get(name) or name.replace("_", " ").title()
        host = (urlparse(feed_url).netloc or "unknown").replace("www.", "")
        return host.split(":")[0].title()
    
    def _parse_date(self, parsed_date: Optional[tuple]) -> datetime:
        """Parse date tuple to datetime object."""
        if parsed_date is None:
            return datetime.now(timezone.utc)
        
        try:
            dt = datetime(*parsed_date[:6])
            return dt
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)
    
    def _extract_content_from_html(self, html: str) -> str:
        """Extract clean text from HTML content."""
        if not html:
            return ""
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Remove scripts and styles
        for tag in soup(['script', 'style']):
            tag.decompose()
        
        # Get text content
        text = soup.get_text(separator=' ', strip=True)
        
        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text)
        
        return text[:5000]
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Extract matching geopolitical keywords from text."""
        text_lower = text.lower()
        matched = []
        
        for keyword in self.KEYWORDS:
            if keyword in text_lower:
                matched.append(keyword)
        
        return list(set(matched))  # Remove duplicates
    
    def fetch_yahoo_finance_news(
        self,
        symbols: List[str],
        date_from: Optional[datetime] = None
    ) -> List[NewsArticle]:
        """
        Fetch news articles for symbols using yfinance.
        
        Args:
            symbols: List of stock symbols to fetch news for
            date_from: Only include articles published after this date
            
        Returns:
            List of parsed news articles from Yahoo Finance
        """
        if not YFINANCE_AVAILABLE:
            print("yfinance not available, skipping Yahoo Finance news fetch")
            return []
        
        articles = []
        
        for symbol in symbols:
            try:
                ticker = yf.Ticker(symbol.upper())
                news_data = ticker.news or []

                for news_item in news_data:
                    if not isinstance(news_item, dict):
                        print(f"Skipping invalid Yahoo Finance news item for {symbol}: {type(news_item).__name__}")
                        continue

                    try:
                        article = self._extract_yahoo_article(news_item, symbol)
                        
                        # Filter by date if specified
                        if date_from and article.published_date < date_from:
                            continue
                        
                        articles.append(article)
                    except Exception as e:
                        print(f"Error extracting article for {symbol}: {e}")
                        
            except Exception as e:
                print(f"Error fetching Yahoo Finance news for {symbol}: {e}")
        
        return articles
    
    def _extract_yahoo_article(
        self,
        news_item: Dict[str, Any],
        symbol: str
    ) -> NewsArticle:
        """Extract NewsArticle from yfinance news item."""
        if news_item is None:
            raise ValueError("Yahoo Finance news item is None")

        content = {}
        if isinstance(news_item.get('content'), dict):
            content = news_item.get('content') or {}
        elif isinstance(news_item.get('content'), list) and news_item['content']:
            first_content = news_item['content'][0]
            if isinstance(first_content, dict):
                content = first_content

        # Fallback to using the item itself if content is missing
        if not content:
            content = news_item

        # Extract title
        title = content.get('title', '') or news_item.get('title', '')

        # Extract link - prefer canonical URL, fallback to clickThroughUrl, then raw link
        canonical_url = ''
        if isinstance(content.get('canonicalUrl'), dict):
            canonical_url = content.get('canonicalUrl', {}).get('url', '')
        click_url = ''
        if isinstance(content.get('clickThroughUrl'), dict):
            click_url = content.get('clickThroughUrl', {}).get('url', '')
        link = canonical_url or click_url or content.get('link', '') or news_item.get('link', '') or ''
        
        # Source is Yahoo Finance
        source = "Yahoo Finance"
        
        # Extract author/provider
        provider = content.get('provider', {})
        author = provider.get('displayName', None)
        if author is None and isinstance(news_item.get('provider'), dict):
            author = news_item.get('provider', {}).get('displayName')
        
        # Extract published date
        pub_date_str = content.get('pubDate', '') or news_item.get('pubDate', '')
        published_date = self._parse_yahoo_date(pub_date_str)
        
        # Extract summary
        summary = content.get('summary', '') or news_item.get('summary', '')
        
        # For content, use summary since we don't have full article text
        content_text = summary
        
        # Extract keywords (use the same method as RSS)
        keywords = self._extract_keywords(title + " " + summary)
        
        return NewsArticle(
            title=title,
            link=link,
            source=source,
            author=author,
            published_date=published_date,
            summary=summary,
            content=content_text,
            keywords=keywords
        )
    
    def _parse_yahoo_date(self, date_str: str) -> datetime:
        """Parse ISO date string from Yahoo Finance."""
        if not date_str:
            return datetime.now(timezone.utc)
        
        try:
            # Remove 'Z' if present and parse
            date_str = date_str.rstrip('Z')
            dt = datetime.fromisoformat(date_str)
            return dt
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)
    
    def filter_by_keywords(
        self,
        articles: List[NewsArticle],
        min_keywords: int = 1
    ) -> List[NewsArticle]:
        """
        Filter articles by minimum keyword matches.
        
        Args:
            articles: All parsed articles
            min_keywords: Minimum number of keywords to match
            
        Returns:
            Filtered list of relevant articles
        """
        return [
            article for article in articles
            if len(article.keywords) >= min_keywords
        ]
    
    def get_latest_articles(
        self,
        limit: int = 50,
        date_from: Optional[datetime] = None
    ) -> List[NewsArticle]:
        """
        Get the most recent articles from all feeds.
        
        Args:
            limit: Maximum number of articles to return
            date_from: Only include articles after this date
            
        Returns:
            Sorted list of latest articles
        """
        all_articles = self.parse_feeds(date_from=date_from)
        
        # Sort by published date (newest first)
        all_articles.sort(
            key=lambda a: a.published_date,
            reverse=True
        )
        
        return all_articles[:limit]
