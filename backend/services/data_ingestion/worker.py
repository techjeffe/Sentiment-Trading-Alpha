"""
Background ingestion worker for the article producer/consumer pipeline.

Enhanced with:
- Google News redirect resolution
- 1-hour freshness filtering
- Improved deduplication
- Support for new source configuration
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests
import trafilatura
from sqlalchemy.orm import Session

from database.engine import SessionLocal
from database.models import ScrapedArticle
from services.app_config import build_enabled_rss_feed_labels, build_enabled_rss_feed_map, get_or_create_app_config
from services.data_ingestion.parser import NewsArticle, RSSFeedParser
from services.sentiment.prompts import expand_proxy_terms_for_matching, normalize_text_for_matching
from services.data_ingestion.google_news_resolver import resolve_google_news_url, resolve_google_news_urls
from services.data_ingestion.freshness_filter import FreshnessFilter, filter_fresh_articles
from config.news_sources import get_enabled_sources, build_rss_feed_map as build_news_source_map

logger = logging.getLogger(__name__)


MAJOR_POLICY_SHIFT_TERMS = [
    "federal reserve",
    "fed",
    "rate cut",
    "rate hike",
    "fomc",
    "cpi",
    "inflation",
    "jobs report",
    "payrolls",
    "tariff",
    "trade war",
    "sanctions",
    "opec",
    "production cut",
    "export controls",
    "emergency order",
    "market halt",
    "trading halt",
]

FAST_LANE_TERMS = [
    "cpi",
    "federal reserve",
    "fed rate",
    "rate cut",
    "rate hike",
    "fomc",
    "emergency",
    "halt",
    "market halt",
    "trading halt",
    "opec",
    "sanctions",
    "tariff",
    "surprise decision",
    "intervention",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _load_symbol_relevance_terms() -> Dict[str, List[str]]:
    from routers.analysis import SYMBOL_RELEVANCE_TERMS

    return {str(symbol).upper(): list(terms or []) for symbol, terms in SYMBOL_RELEVANCE_TERMS.items()}


def _iter_stage0_terms(
    symbols: Iterable[str],
    company_aliases: Optional[Dict[str, str]] = None,
) -> List[str]:
    relevance_terms = _load_symbol_relevance_terms()
    aliases = company_aliases or {}
    selected_terms: List[str] = []
    for symbol in symbols:
        sym_upper = str(symbol).upper()
        terms = relevance_terms.get(sym_upper, [])
        if terms:
            selected_terms.extend(terms)
        else:
            # Custom symbol: at minimum match the ticker itself and each word of
            # the company alias (e.g. "gopro" from "GoPro", "nvidia" from "NVIDIA").
            selected_terms.append(sym_upper.lower())
            alias = aliases.get(sym_upper, "")
            for word in alias.lower().split():
                if len(word) > 3:  # skip short noise like "inc", "llc", "the"
                    selected_terms.append(word)
    selected_terms.extend(MAJOR_POLICY_SHIFT_TERMS)
    return expand_proxy_terms_for_matching(selected_terms)


def _matches_stage0_filter(
    article: NewsArticle,
    tracked_symbols: Iterable[str],
    company_aliases: Optional[Dict[str, str]] = None,
) -> bool:
    text = normalize_text_for_matching(" ".join([article.title or "", article.summary or ""]))
    if not text:
        return False
    return any(term in text for term in _iter_stage0_terms(tracked_symbols, company_aliases))


def check_fast_lane(article_summary: str) -> bool:
    text = normalize_text_for_matching(article_summary or "")
    return any(term in text for term in FAST_LANE_TERMS)


def _resolve_fast_lane_symbols(text: str, tracked_symbols: List[str]) -> List[str]:
    normalized = normalize_text_for_matching(text)
    relevance_terms = _load_symbol_relevance_terms()
    matched: List[str] = []
    for symbol in tracked_symbols:
        terms = expand_proxy_terms_for_matching(relevance_terms.get(symbol.upper(), []))
        if any(term in normalized for term in terms):
            matched.append(symbol.upper())
    return matched or [str(symbol).upper() for symbol in tracked_symbols]


def _clean_extracted_text(text: str, fallback: str) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    if cleaned:
        return cleaned[:20000]
    return " ".join(str(fallback or "").split()).strip()[:20000]


# ── Domain cookie injection ──────────────────────────────────────────────────
# Drop a domain_cookies.json file in the backend/ directory to inject
# authentication cookies for paywalled sites (e.g. NYT). Two formats:
#
#   Array format  — paste a Cookie-Editor / EditThisCookie browser export directly
#   Dict format   — {"nytimes.com": [{"name": "NYT-S", "value": "..."}, ...]}
#
# Cookies are matched by URL hostname suffix. Re-read on every ingestion cycle
# so updates take effect without restarting the server.

_COOKIE_FILE = Path(__file__).parent.parent.parent / "domain_cookies.json"


def _load_domain_cookies() -> Dict[str, List[Dict]]:
    if not _COOKIE_FILE.exists():
        return {}
    try:
        with open(_COOKIE_FILE) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            grouped: Dict[str, List[Dict]] = {}
            for cookie in data:
                domain = str(cookie.get("domain", "")).lstrip(".")
                if domain:
                    grouped.setdefault(domain, []).append(cookie)
            return grouped
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("domain_cookies.json load failed: %s", exc)
    return {}


def _cookies_for_url(url: str, domain_cookies: Dict[str, List[Dict]]) -> Dict[str, str]:
    """Return name→value dict of cookies whose domain suffix matches the URL."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return {}
    result: Dict[str, str] = {}
    for domain, cookies in domain_cookies.items():
        if host == domain or host.endswith("." + domain):
            for c in cookies:
                name = c.get("name", "")
                value = c.get("value", "")
                if name:
                    result[name] = value
    return result


def _fetch_with_requests(url: str, timeout: int = 15, extra_cookies: Optional[Dict[str, str]] = None) -> str:
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
        cookies=extra_cookies or {},
    )
    response.raise_for_status()
    return response.text


async def fetch_article_text(url: str, fallback_text: str = "") -> str:
    """Fetch and extract article text using trafilatura for extraction.

    Downloads HTML via requests (with optional auth cookies), then extracts
    the article body with trafilatura.extract().
    """
    domain_cookies = _load_domain_cookies()
    req_cookies = _cookies_for_url(url, domain_cookies)

    # ── Primary: requests (download) + trafilatura.extract (parse) ──
    extracted = ""
    try:
        html = await asyncio.to_thread(
            _fetch_with_requests, url, extra_cookies=req_cookies or None,
        )
        if html:
            extracted = trafilatura.extract(
                html,
                favor_recall=True,
                include_comments=False,
                include_tables=False,
                url=url,
            ) or ""
    except Exception:
        extracted = ""

    if len(extracted.strip()) >= 400:
        return _clean_extracted_text(extracted, fallback_text)

    return _clean_extracted_text(extracted or fallback_text, fallback_text)


def _compute_content_hash(title: str, content: str) -> str:
    """SHA256 of normalized title + first 500 chars of content for dedup."""
    import hashlib
    normalized = " ".join((title or "").lower().split()) + "|" + " ".join((content or "")[:500].lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _upsert_scraped_article(
    db: Session,
    article: NewsArticle,
    full_content: str,
    fast_lane_triggered: bool,
) -> Tuple[ScrapedArticle, bool]:
    content_hash = _compute_content_hash(article.title or "", full_content or article.summary or "")

    # Check URL first (exact duplicate)
    existing = db.query(ScrapedArticle).filter(ScrapedArticle.url == article.link).first()
    if existing:
        if full_content and (not existing.full_content or len(full_content) > len(existing.full_content)):
            existing.full_content = full_content
        if article.summary and not existing.summary:
            existing.summary = article.summary
        if article.title and not existing.title:
            existing.title = article.title
        if article.source and not existing.source:
            existing.source = article.source
        if article.published_date and existing.published_at is None:
            existing.published_at = _coerce_utc(article.published_date)
        existing.fast_lane_triggered = bool(existing.fast_lane_triggered or fast_lane_triggered)
        if not existing.content_hash:
            existing.content_hash = content_hash
        # Update discovered_at to keep article fresh in the 12-hour window
        existing.discovered_at = _utc_now()
        db.add(existing)
        db.flush()
        return existing, False

    # Check content hash for near-duplicate (same story, different URL)
    if content_hash:
        hash_match = db.query(ScrapedArticle).filter(
            ScrapedArticle.content_hash == content_hash,
            ScrapedArticle.processed == False,
        ).first()
        if hash_match:
            # Same story already in queue — skip
            return hash_match, False

    row = ScrapedArticle(
        source=str(article.source or "unknown"),
        url=str(article.link or "").strip(),
        title=str(article.title or "").strip(),
        summary=str(article.summary or "").strip(),
        full_content=_clean_extracted_text(full_content, article.summary or article.content or article.title or ""),
        published_at=_coerce_utc(article.published_date),
        discovered_at=_utc_now(),
        processed=False,
        fast_lane_triggered=bool(fast_lane_triggered),
        content_hash=content_hash,
    )
    db.add(row)
    db.flush()
    return row, True


async def trigger_fast_lane(article_ids: List[int], symbols: List[str]) -> None:
    if not article_ids:
        return

    from routers.analysis import run_analysis_for_pending_articles

    db = SessionLocal()
    try:
        try:
            await run_analysis_for_pending_articles(
                db=db,
                symbols=symbols,
                article_ids=article_ids,
                trigger_source="fast_lane",
            )
        except Exception:
            logger.exception(
                "Fast-lane analysis failed for article_ids=%s symbols=%s",
                article_ids,
                symbols,
            )
    finally:
        db.close()


async def run_ingestion_cycle(db: Optional[Session] = None) -> Dict[str, Any]:
    owns_db = db is None
    session = db or SessionLocal()
    try:
        config = get_or_create_app_config(session)
        tracked_symbols = [str(symbol).upper().strip() for symbol in (config.tracked_symbols or []) if str(symbol).strip()]
        company_aliases = {
            str(k).upper(): str(v).strip()
            for k, v in (getattr(config, "symbol_company_aliases", None) or {}).items()
            if str(v).strip()
        }

        # ── Enhanced Source Configuration ──
        # Use new source config + legacy config
        enabled_sources = get_enabled_sources()
        news_source_feeds = build_news_source_map(enabled_sources)
        
        # Fall back to legacy config if no new sources configured
        if not news_source_feeds:
            yahoo_symbols = [sym for sym in tracked_symbols if sym]
            merged_feeds = build_enabled_rss_feed_map(config)
            merged_labels = build_enabled_rss_feed_labels(config)
        else:
            yahoo_symbols = [sym for sym in tracked_symbols if sym]
            merged_feeds = news_source_feeds
            merged_labels = {source.name: source.name for source in enabled_sources}
        
        parser = RSSFeedParser(
            feeds=merged_feeds,
            feed_labels=merged_labels,
        )
        
        # Parse RSS feeds
        articles = await asyncio.to_thread(parser.parse_feeds)
        
        # Fetch Yahoo Finance news
        if yahoo_symbols:
            yahoo_articles = await asyncio.to_thread(parser.fetch_yahoo_finance_news, yahoo_symbols)
            articles.extend(yahoo_articles)
        
        print(f"Ingestion: {len(articles)} raw articles "
              f"({len(articles) - len(yahoo_articles) if yahoo_symbols else len(articles)} RSS + {len(yahoo_articles) if yahoo_symbols else 0} Yahoo Finance)")
        
        # ── Step 1: Freshness Filter (1-hour) ──
        freshness_filter = FreshnessFilter(max_age_hours=1.0)
        articles_before_freshness = len(articles)
        articles = freshness_filter.filter_articles(articles)
        print(f"Freshness filter: {len(articles)}/{articles_before_freshness} articles passed (within 1 hour)")

        # ── Step 2: Resolve Google News Redirects ──
        google_news_articles = [
            article for article in articles
            if article.link and "news.google.com/rss" in article.link
        ]
        
        if google_news_articles:
            print(f"Resolving {len(google_news_articles)} Google News redirects...")
            urls_to_resolve = [article.link for article in google_news_articles]
            resolved_urls = await resolve_google_news_urls(urls_to_resolve)
            
            # Update articles with resolved URLs
            for article in google_news_articles:
                if article.link in resolved_urls:
                    new_url = resolved_urls[article.link]
                    if new_url != article.link:
                        print(f"  Resolved: {article.link[:80]}... -> {new_url[:80]}...")
                        article.link = new_url
                        # Update source to reflect actual publisher
                        if "bloomberg.com" in new_url:
                            article.source = "Bloomberg"
                        elif "reuters.com" in new_url:
                            article.source = "Reuters"
                        elif "marketwatch.com" in new_url:
                            article.source = "MarketWatch"
        
        # ── Step 3: Stage 0 Filter (Relevance) ──
        # Articles from Yahoo Finance are relevant by definition —
        # bypass Stage 0 for them so "Alphabet Reports Earnings" doesn't get dropped
        # because it doesn't contain the ticker "goog".
        yahoo_source_labels: set = {"Yahoo Finance"}
        
        kept_articles = [
            article for article in articles
            if article.link and (
                article.source in yahoo_source_labels
                or _matches_stage0_filter(article, tracked_symbols, company_aliases)
            )
        ]
        print(f"Stage 0 filter: {len(kept_articles)}/{len(articles)} articles passed "
              f"(symbols: {', '.join(tracked_symbols)})")
        kept_articles.sort(
            key=lambda item: _coerce_utc(getattr(item, "published_date", None)) or _utc_now(),
            reverse=True,
        )
        
        # ── Step 4: Deduplication Check ──
        # Check against recent articles in DB to avoid reprocessing
        recent_cutoff = _utc_now() - timedelta(hours=2)  # Check last 2 hours
        recent_urls = set()
        if session:
            recent_articles = session.query(ScrapedArticle).filter(
                ScrapedArticle.discovered_at >= recent_cutoff
            ).all()
            recent_urls = {article.url for article in recent_articles}
        
        new_articles = [
            article for article in kept_articles
            if article.link not in recent_urls
        ]
        
        print(f"Deduplication: {len(new_articles)}/{len(kept_articles)} articles are new")
        
        stored_count = 0
        duplicate_count = 0
        fast_lane_article_ids: List[int] = []
        fast_lane_symbols: List[str] = []
        
        for article in new_articles:
            fallback_text = " ".join(
                part for part in [article.summary or "", article.content or "", article.title or ""] if part
            )
            try:
                full_content = await fetch_article_text(article.link, fallback_text=fallback_text)
            except Exception as exc:
                full_content = _clean_extracted_text(f"{fallback_text} Extraction error: {exc}", fallback_text)
            
            summary_blob = " ".join([article.title or "", article.summary or "", full_content or ""])
            fast_lane_hit = check_fast_lane(summary_blob)
            row, is_new = _upsert_scraped_article(session, article, full_content, fast_lane_hit)
            session.commit()
            if is_new:
                stored_count += 1
            else:
                duplicate_count += 1
            
            if fast_lane_hit:
                fast_lane_article_ids.append(int(row.id))
                fast_lane_symbols.extend(_resolve_fast_lane_symbols(summary_blob, tracked_symbols))

        # Fast-lane analysis is disabled from the scheduler to prevent
        # it from marking articles as processed before the stream endpoint runs.
        # Analysis should only be triggered by the user clicking "Analyze".
        if fast_lane_article_ids:
            deduped_symbols = sorted({symbol.upper() for symbol in fast_lane_symbols if symbol})
            print(f"Fast-lane articles detected ({len(fast_lane_article_ids)}), but analysis deferred to manual trigger")

        return {
            "total_feed_articles": len(articles),
            "stage0_matches": len(kept_articles),
            "stored_count": stored_count,
            "duplicate_count": duplicate_count,
            "fast_lane_article_ids": sorted(set(fast_lane_article_ids)),
            "fast_lane_symbol_count": len(sorted({symbol.upper() for symbol in fast_lane_symbols if symbol})),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_db:
            session.close()


def build_analysis_posts(rows: List[ScrapedArticle]) -> List[Any]:
    parser_keywords = RSSFeedParser.KEYWORDS
    posts: List[Any] = []
    for row in rows:
        blob = normalize_text_for_matching(" ".join([row.title or "", row.summary or "", row.full_content or ""]))
        keywords = [keyword for keyword in parser_keywords if keyword in blob][:8]
        posts.append(
            SimpleNamespace(
                id=row.id,
                source=row.source,
                feed_name=row.source,
                author=None,
                title=row.title or "",
                summary=row.summary or "",
                content=row.full_content or row.summary or row.title or "",
                keywords=keywords,
                published_date=_coerce_utc(row.published_at),
                discovered_at=_coerce_utc(row.discovered_at),
                url=row.url,
                fast_lane_triggered=bool(row.fast_lane_triggered),
            )
        )
    return posts
