"""
Lightweight web research helpers for symbol-specific prompt grounding.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Dict, List, Tuple
from urllib.parse import quote_plus
import re
import xml.etree.ElementTree as ET

import requests


GOOGLE_NEWS_SEARCH_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
WEB_RESEARCH_TTL_MINUTES = 30
DEFAULT_WEB_RESEARCH_MAX_ITEMS = 3
DEFAULT_WEB_RESEARCH_MAX_AGE_DAYS = 30
MAX_ITEMS_PER_SOURCE = 2
TRUSTED_SOURCE_LABELS = {
    "Reuters",
    "CNBC",
    "Bloomberg",
    "Financial Times",
    "The Wall Street Journal",
    "MarketWatch",
    "Associated Press",
    "AP News",
    "Barron's",
    "Investopedia",
    "NVIDIA",
    "Microsoft",
    "Apple",
    "Amazon",
    "Alphabet",
    "Meta",
    "SEC",
    "Oracle",
    "ServiceNow",
}
SOURCE_WEIGHT = {
    "Reuters": 1.0,
    "Bloomberg": 0.95,
    "Financial Times": 0.9,
    "The Wall Street Journal": 0.9,
    "CNBC": 0.85,
    "MarketWatch": 0.8,
    "Associated Press": 0.8,
    "AP News": 0.8,
    "SEC": 1.0,
}
SYMBOL_RESEARCH_PROFILES: Dict[str, Dict[str, List[str] | str]] = {
    "NVDA": {
        "company": "NVIDIA",
        "queries": [
            '"NVDA" earnings guidance AI data center',
            '"NVIDIA" Blackwell export controls data center',
            '"NVIDIA" site:reuters.com OR site:cnbc.com OR site:sec.gov',
        ],
        "keywords": ["nvidia", "blackwell", "gpu", "ai", "data center", "export controls", "cuda", "h200", "gb200"],
    },
    "ORCL": {
        "company": "Oracle",
        "queries": [
            '"ORCL" earnings guidance OCI cloud',
            '"Oracle" OCI cloud AI database',
            '"Oracle" site:reuters.com OR site:cnbc.com OR site:sec.gov',
        ],
        "keywords": ["oracle", "oci", "cloud", "database", "enterprise software", "ai", "cerner", "contract"],
    },
    "NOW": {
        "company": "ServiceNow",
        "queries": [
            '"NOW" earnings guidance workflow software',
            '"ServiceNow" AI enterprise workflow',
            '"ServiceNow" site:reuters.com OR site:cnbc.com OR site:sec.gov',
        ],
        "keywords": ["servicenow", "workflow", "enterprise", "subscription", "ai", "automation", "it service"],
    },
    "AAPL": {
        "company": "Apple",
        "queries": [
            '"AAPL" earnings guidance iphone services',
            '"Apple" AI china iphone services',
            '"Apple" site:reuters.com OR site:cnbc.com OR site:sec.gov',
        ],
        "keywords": ["apple", "iphone", "services", "mac", "china", "ai", "app store"],
    },
    "QQQ": {
        "company": "Nasdaq 100",
        "queries": [
            '"QQQ" nasdaq rates semiconductors',
            '"Nasdaq 100" megacap tech rates',
            '"QQQ" site:reuters.com OR site:marketwatch.com',
        ],
        "keywords": ["nasdaq", "qqq", "rates", "semiconductors", "megacap", "tech", "yield"],
    },
    "SPY": {
        "company": "S&P 500",
        "queries": [
            '"SPY" fed inflation earnings',
            '"S&P 500" rates inflation credit spreads',
            '"SPY" site:reuters.com OR site:marketwatch.com',
        ],
        "keywords": ["s&p 500", "spy", "fed", "inflation", "credit spreads", "earnings", "economy"],
    },
    "USO": {
        "company": "United States Oil Fund",
        "queries": [
            '"USO" crude opec refinery',
            '"oil" opec refinery wti brent',
            '"USO" site:reuters.com OR site:marketwatch.com',
        ],
        "keywords": ["oil", "crude", "opec", "refinery", "wti", "brent", "supply"],
    },
    "IBIT": {
        "company": "iShares Bitcoin Trust",
        "queries": [
            '"IBIT" bitcoin etf regulation',
            '"bitcoin" etf regulation liquidity',
            '"IBIT" site:reuters.com OR site:cnbc.com',
        ],
        "keywords": ["bitcoin", "btc", "ibit", "etf", "crypto", "regulation", "liquidity", "mining"],
    },
}
_cache: Dict[str, Tuple[datetime, Dict[str, Any]]] = {}


def _normalize_symbol(symbol: str) -> str:
    normalized = str(symbol or "").upper().strip()
    return "IBIT" if normalized == "BITO" else normalized


def _parse_pubdate(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat()
    except Exception:
        return raw


def _parse_iso_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _normalize_title_key(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _extract_text(node: ET.Element | None, tag: str) -> str:
    if node is None:
        return ""
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _strip_html(value: str) -> str:
    text = unescape(str(value or ""))
    return re.sub(r"<[^>]+>", "", text).strip()


def _research_profile(symbol: str, company_alias: str = "") -> Dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    if normalized in SYMBOL_RESEARCH_PROFILES:
        profile = dict(SYMBOL_RESEARCH_PROFILES[normalized])
        alias = str(company_alias or "").strip()
        if alias:
            profile["company"] = alias
            queries = [str(query).strip() for query in profile.get("queries", []) if str(query).strip()]
            alias_queries = [
                f'"{alias}" earnings guidance',
                f'"{alias}" site:reuters.com OR site:cnbc.com OR site:sec.gov',
            ]
            profile["queries"] = list(dict.fromkeys(alias_queries + queries))
            keywords = [str(keyword).strip() for keyword in profile.get("keywords", []) if str(keyword).strip()]
            alias_keywords = [part.strip().lower() for part in re.split(r"[/,()]+|\s{2,}", alias) if part.strip()]
            if alias.lower() not in [keyword.lower() for keyword in keywords]:
                keywords.append(alias)
            for keyword in alias_keywords:
                if keyword not in [item.lower() for item in keywords]:
                    keywords.append(keyword)
            profile["keywords"] = keywords
        return profile
    alias = str(company_alias or "").strip()
    return {
        "company": alias or normalized,
        "queries": [
            *( [f'"{alias}" earnings guidance company', f'"{alias}" site:reuters.com OR site:cnbc.com OR site:sec.gov'] if alias else [] ),
            f'"{normalized}" earnings guidance company',
            f'"{normalized}" stock outlook results',
            f'"{normalized}" site:reuters.com OR site:cnbc.com OR site:sec.gov',
        ],
        "keywords": [normalized.lower(), *( [alias.lower()] if alias else [] ), "earnings", "guidance", "outlook", "results"],
    }


def _query_list(symbol: str, company_alias: str = "") -> List[str]:
    return [str(query).strip() for query in _research_profile(symbol, company_alias).get("queries", []) if str(query).strip()]


def _keyword_list(symbol: str, company_alias: str = "") -> List[str]:
    profile = _research_profile(symbol, company_alias)
    company = str(profile.get("company") or "").strip().lower()
    base = [str(keyword).strip().lower() for keyword in profile.get("keywords", []) if str(keyword).strip()]
    if company and company not in base:
        base.append(company)
    normalized = _normalize_symbol(symbol).lower()
    if normalized and normalized not in base:
        base.append(normalized)
    return list(dict.fromkeys(base))


def _score_item(symbol: str, item: Dict[str, str], max_age_days: int, company_alias: str = "") -> Tuple[float, List[str], float]:
    title = str(item.get("title") or "").lower()
    summary = str(item.get("summary") or "").lower()
    source = str(item.get("source") or "")
    published_dt = _parse_iso_datetime(item.get("published_at", ""))
    age_days = float(max_age_days)
    freshness_score = 0.0
    if published_dt is not None:
        age_days = max(0.0, (datetime.now(timezone.utc) - published_dt).total_seconds() / 86400.0)
        freshness_score = max(0.0, 1.0 - (age_days / max(1, max_age_days)))

    text_blob = f"{title}\n{summary}"
    matched_keywords = [keyword for keyword in _keyword_list(symbol, company_alias) if keyword in text_blob]
    exact_symbol = _normalize_symbol(symbol).lower() in title
    keyword_score = min(1.0, len(matched_keywords) / 4.0)
    source_score = SOURCE_WEIGHT.get(source, 0.65 if source else 0.4)
    title_bonus = 0.3 if exact_symbol else 0.0
    score = (freshness_score * 0.45) + (keyword_score * 0.35) + (source_score * 0.2) + title_bonus
    return round(score, 4), matched_keywords[:6], round(age_days, 1)


def _fetch_query_results(query: str, timeout: int) -> List[Dict[str, str]]:
    url = GOOGLE_NEWS_SEARCH_URL.format(query=quote_plus(query))
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "SentimentTradingAlpha/1.0"},
        )
        # Handle 503 Service Unavailable gracefully - Google News RSS is deprecating
        if response.status_code == 503:
            print(f"[SKIPPED] Google News RSS query '{query[:50]}...' - 503 Service Unavailable")
            return []
        response.raise_for_status()
    except requests.RequestException as e:
        # Log error but don't crash - return empty results
        print(f"[ERROR] Google News RSS fetch failed for query '{query[:50]}...': {e}")
        return []
    
    root = ET.fromstring(response.text)
    channel = root.find("channel")
    results: List[Dict[str, str]] = []
    if channel is None:
        return results
    for item in channel.findall("item"):
        source_node = item.find("source")
        source = (source_node.text or "").strip() if source_node is not None and source_node.text else ""
        if source and source not in TRUSTED_SOURCE_LABELS:
            continue
        title = _extract_text(item, "title")
        if " - " in title and not source:
            title = title.rsplit(" - ", 1)[0].strip()
        if not title:
            continue
        results.append(
            {
                "source": source or "Google News",
                "title": title,
                "url": _extract_text(item, "link"),
                "published_at": _parse_pubdate(_extract_text(item, "pubDate")),
                "summary": _strip_html(_extract_text(item, "description")),
                "query": query,
            }
        )
    return results


def fetch_recent_symbol_web_context(
    symbol: str,
    company_alias: str = "",
    timeout: int = 6,
    max_items: int = DEFAULT_WEB_RESEARCH_MAX_ITEMS,
    max_age_days: int = DEFAULT_WEB_RESEARCH_MAX_AGE_DAYS,
) -> Dict[str, Any]:
    normalized_symbol = _normalize_symbol(symbol)
    normalized_alias = str(company_alias or "").strip()
    if not normalized_symbol:
        return {"summary": "", "items": []}

    bounded_max_items = max(1, min(8, int(max_items)))
    bounded_max_age_days = max(1, min(60, int(max_age_days)))
    cache_key = f"{normalized_symbol}:{normalized_alias.lower()}:{bounded_max_items}:{bounded_max_age_days}"
    cached = _cache.get(cache_key)
    if cached and (datetime.now(timezone.utc) - cached[0]) < timedelta(minutes=WEB_RESEARCH_TTL_MINUTES):
        return cached[1]

    cutoff = datetime.now(timezone.utc) - timedelta(days=bounded_max_age_days)
    raw_items: List[Dict[str, Any]] = []

    for query in _query_list(normalized_symbol, normalized_alias):
        for item in _fetch_query_results(query, timeout):
            published_dt = _parse_iso_datetime(item.get("published_at", ""))
            if published_dt and published_dt < cutoff:
                continue
            score, matched_keywords, age_days = _score_item(normalized_symbol, item, bounded_max_age_days, normalized_alias)
            if score < 0.35:
                continue
            enriched = dict(item)
            enriched["relevance_score"] = score
            enriched["matched_keywords"] = matched_keywords
            enriched["age_days"] = age_days
            raw_items.append(enriched)

    deduped: List[Dict[str, Any]] = []
    seen_titles = set()
    items_per_source: Dict[str, int] = defaultdict(int)
    raw_items.sort(
        key=lambda item: (
            float(item.get("relevance_score", 0.0)),
            -float(item.get("age_days", 9999.0)),
        ),
        reverse=True,
    )
    for item in raw_items:
        title_key = _normalize_title_key(item.get("title", ""))
        source = str(item.get("source") or "Unknown")
        if not title_key or title_key in seen_titles:
            continue
        if items_per_source[source] >= MAX_ITEMS_PER_SOURCE:
            continue
        seen_titles.add(title_key)
        items_per_source[source] += 1
        deduped.append(item)
        if len(deduped) >= bounded_max_items:
            break

    summary_lines = []
    for item in deduped:
        source = item.get("source") or "Source"
        title = item.get("title") or ""
        published = item.get("published_at") or ""
        score = item.get("relevance_score")
        matched = ", ".join(item.get("matched_keywords") or [])
        if published and matched:
            summary_lines.append(f"- {source} ({published}) [score {score:.2f}] {title} | matched: {matched}")
        elif published:
            summary_lines.append(f"- {source} ({published}) [score {score:.2f}] {title}")
        else:
            summary_lines.append(f"- {source} [score {score:.2f}] {title}")

    payload = {
        "summary": "\n".join(summary_lines),
        "items": deduped,
    }
    _cache[cache_key] = (datetime.now(timezone.utc), payload)
    return payload
