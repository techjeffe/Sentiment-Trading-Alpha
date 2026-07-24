"""
Google News RSS redirect resolver.

Google News RSS feeds return redirect URLs like:
  news.google.com/rss/articles/...
  
This module resolves them to the actual publisher URLs.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, Optional
from urllib.parse import urlparse, parse_qs

import aiohttp

logger = logging.getLogger(__name__)


class GoogleNewsResolver:
    """Resolves Google News redirect URLs to actual publisher URLs."""
    
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self._cache: Dict[str, str] = {}  # Cache resolved URLs
    
    async def resolve_url(self, google_url: str) -> str:
        """
        Resolve a Google News redirect URL to the actual publisher URL.
        
        Args:
            google_url: Google News RSS URL (news.google.com/rss/articles/...)
            
        Returns:
            Actual publisher URL, or original URL if resolution fails
        """
        # Check cache first
        if google_url in self._cache:
            return self._cache[google_url]
        
        # Only process Google News URLs
        if not self._is_google_news_url(google_url):
            return google_url
        
        try:
            actual_url = await self._extract_actual_url(google_url)
            if actual_url:
                self._cache[google_url] = actual_url
                return actual_url
        except Exception as exc:
            logger.warning(f"Failed to resolve Google News URL {google_url}: {exc}")
        
        return google_url
    
    def _is_google_news_url(self, url: str) -> bool:
        """Check if URL is a Google News redirect URL."""
        return "news.google.com/rss/articles" in url or "news.google.com/rss/search" in url
    
    async def _extract_actual_url(self, google_url: str) -> Optional[str]:
        """
        Extract the actual publisher URL from a Google News URL.
        
        Google News RSS <link> tags often point to redirect URLs.
        The actual URL is sometimes embedded in the RSS item's <source> tag
        or can be extracted by following the redirect.
        """
        # Method 1: Try to extract from URL parameters
        actual_url = self._extract_from_url_params(google_url)
        if actual_url:
            return actual_url
        
        # Method 2: Follow the redirect (with timeout)
        actual_url = await self._follow_redirect(google_url)
        if actual_url:
            return actual_url
        
        return None
    
    def _extract_from_url_params(self, url: str) -> Optional[str]:
        """
        Try to extract actual URL from Google News URL parameters.
        
        Some Google News URLs have the actual URL embedded in parameters.
        """
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            
            # Check for 'url' parameter
            if 'url' in params:
                return params['url'][0]
            
            # Check for 'article_url' parameter
            if 'article_url' in params:
                return params['article_url'][0]
            
        except Exception:
            pass
        
        return None
    
    async def _follow_redirect(self, url: str) -> Optional[str]:
        """
        Follow HTTP redirects to get the final URL.
        
        Uses HEAD request to avoid downloading full page.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    allow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; NewsResolver/1.0)"}
                ) as response:
                    final_url = str(response.url)
                    
                    # Check if we got a real publisher URL (not Google)
                    if not self._is_google_news_url(final_url):
                        return final_url
                    
                    # If still on Google, try to extract from response headers
                    if 'Location' in response.headers:
                        location = response.headers['Location']
                        if not self._is_google_news_url(location):
                            return location
        
        except asyncio.TimeoutError:
            logger.warning(f"Timeout resolving {url}")
        except Exception as exc:
            logger.warning(f"Error following redirect for {url}: {exc}")
        
        return None
    
    async def resolve_multiple(self, urls: list[str]) -> Dict[str, str]:
        """
        Resolve multiple Google News URLs concurrently.
        
        Returns:
            Dict mapping original URL -> resolved URL
        """
        tasks = [self.resolve_url(url) for url in urls]
        resolved = await asyncio.gather(*tasks)
        return dict(zip(urls, resolved))


async def resolve_google_news_url(url: str) -> str:
    """
    Convenience function to resolve a single Google News URL.
    
    Usage:
        actual_url = await resolve_google_news_url(google_rss_url)
    """
    resolver = GoogleNewsResolver()
    return await resolver.resolve_url(url)


async def resolve_google_news_urls(urls: list[str]) -> Dict[str, str]:
    """
    Convenience function to resolve multiple Google News URLs.
    
    Usage:
        resolved_map = await resolve_google_news_urls([url1, url2, url3])
    """
    resolver = GoogleNewsResolver()
    return await resolver.resolve_multiple(urls)


# ─── Testing ─────────────────────────────────────────────────────────────────

async def test_resolver():
    """Test the Google News resolver with sample URLs."""
    test_urls = [
        "https://news.google.com/rss/articles/CBMia2h0dHBzOi8vd3d3LmJsb29tYmVyZy5jb20vbmV3cy9hcnRpY2xlcy9tYXJrZXQtYW5hbHlzaXM",
        "https://news.google.com/rss/search?q=markets+when:1h&hl=en-US&gl=US&ceid=US:en",
    ]
    
    resolver = GoogleNewsResolver()
    
    for url in test_urls:
        print(f"\nTesting: {url}")
        resolved = await resolver.resolve_url(url)
        print(f"Resolved: {resolved}")


if __name__ == "__main__":
    asyncio.run(test_resolver())
