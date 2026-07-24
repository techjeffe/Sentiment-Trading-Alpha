"""
Application configuration helpers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
import socket
import sqlite3
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy import or_
from sqlalchemy.orm import Session

from database.engine import LEGACY_BACKEND_DB_PATH, ROOT_DB_PATH
from database.models import AppConfig, AnalysisResult
from config.logic_loader import LOGIC as _L


# Full list of symbols the system supports (available for manual selection).
SUPPORTED_SYMBOLS = ["USO", "IBIT", "QQQ", "SPY"]

# Symbols pre-selected in the watchlist on fresh installs.
# IBIT is excluded here because trade data shows it underperforms (81% loss rate,
# -16.44% total PnL) but remains in SUPPORTED_SYMBOLS so users can re-add it.
DEFAULT_TRACKED_SYMBOLS = ["USO", "QQQ", "SPY"]
DEFAULT_RSS_ARTICLE_DETAIL_MODE = "normal"
DEFAULT_RSS_ARTICLE_LIMITS = {"light": 5, "normal": 10, "detailed": 20}
DEFAULT_WEB_RESEARCH_ITEMS = {"light": 3, "normal": 4, "detailed": 6}
DEFAULT_WEB_RESEARCH_RECENCY_DAYS = {"light": 14, "normal": 30, "detailed": 45}
LEGACY_DISABLED_RSS_FEED_URLS = {
    "https://www.reutersagency.com/feed/?best-topics=business&post-type=best": "Reuters Agency retired this feed endpoint.",
}
DEFAULT_RSS_FEEDS: List[Dict[str, str]] = [
    {"key": "calculated_risk_rss", "label": "Calculated Risk RSS", "url": "https://feeds.feedburner.com/CalculatedRisk"},
    {"key": "trump_truth", "label": "Trump Truth", "url": "https://trumpstruth.org/feed"},
    {"key": "bbc_world", "label": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"key": "npr_news", "label": "NPR News", "url": "https://feeds.npr.org/1017/rss.xml"},
    {"key": "techcrunch", "label": "TechCrunch", "url": "https://techcrunch.com/feed/"},
]
DEFAULT_EXTRACTION_MODEL = ""
DEFAULT_REASONING_MODEL = ""
DEFAULT_RISK_PROFILE = "standard"
VALID_RISK_PROFILES = {"conservative", "standard", "crazy", "custom"}
LEGACY_RISK_PROFILE_ALIASES = {
    "moderate": "standard",
    "aggressive": "standard",
}
DEFAULT_REMOTE_SNAPSHOT_MODE = "telegram"
VALID_REMOTE_SNAPSHOT_MODES = {"telegram"}
DEFAULT_ALPACA_EXECUTION_MODE = "paper"
VALID_ALPACA_EXECUTION_MODES = {"off", "paper", "live"}
DEFAULT_INFERENCE_BACKEND = "ollama"
VALID_INFERENCE_BACKENDS = {"ollama", "vllm", "openai", "omlx"}
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
MAX_CUSTOM_SYMBOLS = 50
MAX_CUSTOM_RSS_FEEDS = 3
MAX_TRACKED_SYMBOLS = len(SUPPORTED_SYMBOLS) + MAX_CUSTOM_SYMBOLS
DEFAULT_ESTIMATED_ANALYSIS_SECONDS = 82
DEFAULT_SNAPSHOT_RETENTION_LIMIT = 12
# Trade/TradeSnapshot rows are the only record of realized forward returns and
# are tiny, so they are kept far longer than analysis snapshots (which are large
# blobs). The floor exists because a trade pruned before its longest horizon
# (1w) matures can never produce a 1w return.
DEFAULT_TRADE_RETENTION_DAYS = 90
MIN_TRADE_RETENTION_DAYS = 14
MAX_TRADE_RETENTION_DAYS = 3650
DEFAULT_RSS_FEED_URLS = [feed["url"] for feed in DEFAULT_RSS_FEEDS]
DEFAULT_RISK_POLICY: Dict[str, Any] = {
    "crazy_ramp": {
        "threshold_source": "calibrated_bucket",
        "bucket_thresholds": {
            "high_liquidity": {"breakout_atr_fraction": 0.25, "volume_multiplier": 1.35, "retrace_guard": 0.35},
            "mid_liquidity": {"breakout_atr_fraction": 0.30, "volume_multiplier": 1.50, "retrace_guard": 0.30},
            "low_liquidity": {"breakout_atr_fraction": 0.40, "volume_multiplier": 1.80, "retrace_guard": 0.25},
        },
        "fallback": {"breakout_atr_fraction": 0.45, "volume_multiplier": 2.00, "retrace_guard": 0.20},
        "fetch_timeout_ms": 2500,
        "eval_timeout_ms": 15000,
        "stale_ms": 120000,
    }
}

# Snapshots of historical DEFAULT_RSS_FEED_URLS sets. Used to detect existing
# configs whose enabled_rss_feeds list still matches an older default set
# exactly — those users have never customized their feed selection and should
# be migrated forward to the current defaults. Anyone whose saved set differs
# from every snapshot has made a deliberate choice and is left alone.
_LEGACY_DEFAULT_RSS_FEED_URL_SETS: List[frozenset] = [
    frozenset([
        "https://feeds.feedburner.com/CalculatedRisk",
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    ]),
    frozenset([
        "https://feeds.feedburner.com/CalculatedRisk",
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "https://www.reutersagency.com/feed/?best-topics=business&post-type=best",
    ]),
]


def is_valid_symbol(symbol: str) -> bool:
    value = str(symbol or "").upper().strip()
    if not value or len(value) > 10:
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
    return value[0].isalpha() and all(char in allowed for char in value)


def _normalize_symbol(value: Any) -> str:
    normalized = str(value or "").upper().strip()
    return "IBIT" if normalized == "BITO" else normalized


def _normalize_display_timezone(value: Any) -> str:
    return str(value or "").strip()


def _normalize_alpaca_execution_mode(value: Any) -> str:
    normalized = str(value or DEFAULT_ALPACA_EXECUTION_MODE).strip().lower()
    return normalized if normalized in VALID_ALPACA_EXECUTION_MODES else DEFAULT_ALPACA_EXECUTION_MODE


def _normalize_inference_backend(value: Any) -> str:
    normalized = str(value or DEFAULT_INFERENCE_BACKEND).strip().lower()
    return normalized if normalized in VALID_INFERENCE_BACKENDS else DEFAULT_INFERENCE_BACKEND


def _normalize_symbols(symbols: Any, *, fallback: List[str] | None = None, max_items: int = MAX_TRACKED_SYMBOLS) -> List[str]:
    normalized: List[str] = []
    if not isinstance(symbols, list):
        return list(fallback or [])
    for symbol in symbols:
        value = _normalize_symbol(symbol)
        if value and is_valid_symbol(value) and value not in normalized:
            normalized.append(value)
        if len(normalized) >= max_items:
            break
    return normalized or list(fallback or [])


def _normalize_custom_symbols(symbols: Any) -> List[str]:
    custom = _normalize_symbols(symbols, fallback=[], max_items=MAX_CUSTOM_SYMBOLS)
    return [symbol for symbol in custom if symbol not in SUPPORTED_SYMBOLS][:MAX_CUSTOM_SYMBOLS]


def _infer_custom_symbols(tracked_symbols: Any, custom_symbols: Any) -> List[str]:
    explicit_custom = _normalize_custom_symbols(custom_symbols)
    tracked = _normalize_symbols(tracked_symbols, fallback=[], max_items=MAX_TRACKED_SYMBOLS)
    inferred = [
        symbol for symbol in tracked
        if symbol not in SUPPORTED_SYMBOLS and symbol not in explicit_custom
    ]
    return (explicit_custom + inferred)[:MAX_CUSTOM_SYMBOLS]


def _normalize_tracked_symbols(symbols: Any, custom_symbols: List[str]) -> List[str]:
    allowed = set(SUPPORTED_SYMBOLS) | set(custom_symbols)
    normalized = _normalize_symbols(symbols, fallback=[], max_items=MAX_TRACKED_SYMBOLS)
    return [symbol for symbol in normalized if symbol in allowed][:MAX_TRACKED_SYMBOLS]


_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_url(url: str) -> bool:
    """Check if a URL resolves to a private/reserved IP address range."""
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return False
    # Check common private hostnames
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return True
    # Try to resolve hostname
    try:
        addr = socket.getaddrinfo(host, 80, socket.AF_INET, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in addr:
            ip = ipaddress.ip_address(sockaddr[0])
            for net in _PRIVATE_NETWORKS:
                if ip in net:
                    return True
    except Exception:
        # If resolution fails, conservatively block
        return True
    return False


def _normalize_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if url in LEGACY_DISABLED_RSS_FEED_URLS:
        return ""
    if _is_private_url(url):
        return ""
    return url


def _normalize_custom_rss_feeds(feeds: Any) -> List[str]:
    normalized: List[str] = []
    if not isinstance(feeds, list):
        return []
    for feed in feeds:
        url = _normalize_url(feed)
        if url and url not in DEFAULT_RSS_FEED_URLS and url not in normalized:
            normalized.append(url)
        if len(normalized) >= MAX_CUSTOM_RSS_FEEDS:
            break
    return normalized


def _normalize_custom_rss_feed_labels(data: Any, custom_rss_feeds: List[str]) -> Dict[str, str]:
    if not isinstance(data, dict):
        return {}
    allowed = set(custom_rss_feeds)
    normalized: Dict[str, str] = {}
    for url, label in data.items():
        normalized_url = _normalize_url(url)
        normalized_label = str(label or "").strip()
        if normalized_url in allowed and normalized_label:
            normalized[normalized_url] = normalized_label[:60]
    return normalized


def _normalize_enabled_rss_feeds(feeds: Any, custom_rss_feeds: List[str]) -> List[str]:
    allowed = set(DEFAULT_RSS_FEED_URLS) | set(custom_rss_feeds)
    normalized: List[str] = []
    if not isinstance(feeds, list):
        return DEFAULT_RSS_FEED_URLS.copy()
    for feed in feeds:
        url = _normalize_url(feed)
        if url and url in allowed and url not in normalized:
            normalized.append(url)
    if not normalized:
        return DEFAULT_RSS_FEED_URLS.copy()
    # Migrate users who never customized their feed selection: if their saved
    # set matches any historical default snapshot exactly, top up to the
    # current defaults so newly-added defaults appear automatically.
    if frozenset(normalized) in _LEGACY_DEFAULT_RSS_FEED_URL_SETS:
        return DEFAULT_RSS_FEED_URLS.copy()
    return normalized


def _normalize_prompt_overrides(data: Any, allowed_symbols: List[str]) -> Dict[str, str]:
    if not isinstance(data, dict):
        return {}
    allowed = set(allowed_symbols)
    normalized: Dict[str, str] = {}
    for symbol, prompt in data.items():
        sym = _normalize_symbol(symbol)
        if sym in allowed:
            normalized[sym] = str(prompt or "").strip()
    return normalized


def _normalize_symbol_company_aliases(data: Any, allowed_symbols: List[str]) -> Dict[str, str]:
    if not isinstance(data, dict):
        return {}
    allowed = set(allowed_symbols)
    normalized: Dict[str, str] = {}
    for symbol, alias in data.items():
        sym = _normalize_symbol(symbol)
        value = str(alias or "").strip()
        if sym in allowed and value:
            normalized[sym] = value[:120]
    return normalized


def _normalize_symbol_proxy_terms(data: Any, allowed_symbols: List[str]) -> Dict[str, List[str]]:
    if not isinstance(data, dict):
        return {}
    allowed = set(allowed_symbols)
    normalized: Dict[str, List[str]] = {}
    for symbol, terms in data.items():
        sym = _normalize_symbol(symbol)
        if sym not in allowed:
            continue
        cleaned: List[str] = []
        if isinstance(terms, list):
            for term in terms:
                value = str(term or "").strip().lower()
                if value and value not in cleaned:
                    cleaned.append(value)
                if len(cleaned) >= 50:
                    break
        if cleaned:
            normalized[sym] = cleaned
    return normalized


def _normalize_symbol_proxy_terms_generated_at(data: Any, allowed_symbols: List[str]) -> Dict[str, str]:
    if not isinstance(data, dict):
        return {}
    allowed = set(allowed_symbols)
    normalized: Dict[str, str] = {}
    for symbol, timestamp in data.items():
        sym = _normalize_symbol(symbol)
        value = str(timestamp or "").strip()
        if sym in allowed and value:
            normalized[sym] = value
    return normalized


def _normalize_rss_article_limits(data: Any) -> Dict[str, int]:
    limits = dict(DEFAULT_RSS_ARTICLE_LIMITS)
    if isinstance(data, dict):
        for key in ("light", "normal", "detailed"):
            try:
                value = int(data.get(key, limits[key]))
            except (TypeError, ValueError):
                value = limits[key]
            limits[key] = max(1, min(50, value))
    return limits


def _coerce_int(value: Any, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        coerced = default
    if min_value is not None:
        coerced = max(min_value, coerced)
    if max_value is not None:
        coerced = min(max_value, coerced)
    return coerced


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
        return default
    return bool(value)


def _normalize_trading_logic_float(value: Any, min_val: float, max_val: float) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(min_val, min(max_val, v)), 4)


def _normalize_trading_logic_int(value: Any, min_val: int, max_val: int) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return max(min_val, min(max_val, v))


def _normalize_risk_profile(value: Any) -> str:
    profile = str(value or "").strip().lower()
    profile = LEGACY_RISK_PROFILE_ALIASES.get(profile, profile)
    return profile if profile in VALID_RISK_PROFILES else DEFAULT_RISK_PROFILE


def _normalize_risk_policy(value: Any) -> Dict[str, Any]:
    policy = dict(DEFAULT_RISK_POLICY)
    if not isinstance(value, dict):
        return policy
    crazy = value.get("crazy_ramp")
    if not isinstance(crazy, dict):
        return policy
    merged = dict(policy.get("crazy_ramp", {}))
    for key in ("threshold_source", "fetch_timeout_ms", "eval_timeout_ms", "stale_ms"):
        if key in crazy:
            merged[key] = crazy.get(key)
    if isinstance(crazy.get("bucket_thresholds"), dict):
        merged["bucket_thresholds"] = crazy.get("bucket_thresholds")
    if isinstance(crazy.get("fallback"), dict):
        merged["fallback"] = crazy.get("fallback")
    policy["crazy_ramp"] = merged
    return policy


def _normalize_remote_snapshot_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in VALID_REMOTE_SNAPSHOT_MODES else DEFAULT_REMOTE_SNAPSHOT_MODE


def _normalize_rss_article_detail_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"light", "normal", "detailed"} else DEFAULT_RSS_ARTICLE_DETAIL_MODE


def resolve_rss_articles_per_feed(config: AppConfig) -> int:
    limits = _normalize_rss_article_limits(getattr(config, "rss_article_limits", {}))
    mode = _normalize_rss_article_detail_mode(getattr(config, "rss_article_detail_mode", DEFAULT_RSS_ARTICLE_DETAIL_MODE))
    return limits[mode]


def resolve_web_research_items_per_symbol(config: AppConfig) -> int:
    mode = _normalize_rss_article_detail_mode(
        getattr(config, "rss_article_detail_mode", DEFAULT_RSS_ARTICLE_DETAIL_MODE)
    )
    return DEFAULT_WEB_RESEARCH_ITEMS[mode]


def resolve_web_research_recency_days(config: AppConfig) -> int:
    mode = _normalize_rss_article_detail_mode(
        getattr(config, "rss_article_detail_mode", DEFAULT_RSS_ARTICLE_DETAIL_MODE)
    )
    return DEFAULT_WEB_RESEARCH_RECENCY_DAYS[mode]


def _label_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or url).replace("www.", "")
    parts = [part for part in host.split(".") if part and part not in {"com", "org", "net", "io", "co", "uk"}]
    if not parts:
        parts = [host]
    return " ".join(part.capitalize() for part in parts[:2]) or url


def build_supported_symbols(custom_symbols: List[str]) -> List[str]:
    return SUPPORTED_SYMBOLS + [symbol for symbol in custom_symbols if symbol not in SUPPORTED_SYMBOLS]


def build_supported_rss_feeds(custom_rss_feeds: List[str], custom_rss_feed_labels: Dict[str, str] | None = None) -> List[Dict[str, str]]:
    feeds = list(DEFAULT_RSS_FEEDS)
    labels = _normalize_custom_rss_feed_labels(custom_rss_feed_labels or {}, custom_rss_feeds)
    for index, url in enumerate(custom_rss_feeds, start=1):
        feeds.append({
            "key": f"custom_{index}",
            "label": labels.get(url) or _label_from_url(url),
            "url": url,
        })
    return feeds


def build_enabled_rss_feed_map(config: AppConfig) -> Dict[str, str]:
    custom_rss_feeds = _normalize_custom_rss_feeds(getattr(config, "custom_rss_feeds", []))
    enabled_rss_feeds = _normalize_enabled_rss_feeds(getattr(config, "enabled_rss_feeds", []), custom_rss_feeds)
    custom_rss_feed_labels = _normalize_custom_rss_feed_labels(
        getattr(config, "custom_rss_feed_labels", {}),
        custom_rss_feeds,
    )
    supported = build_supported_rss_feeds(custom_rss_feeds, custom_rss_feed_labels)
    return {
        feed["key"]: feed["url"]
        for feed in supported
        if feed["url"] in enabled_rss_feeds
    }


def build_enabled_rss_feed_labels(config: AppConfig) -> Dict[str, str]:
    custom_rss_feeds = _normalize_custom_rss_feeds(getattr(config, "custom_rss_feeds", []))
    enabled_rss_feeds = _normalize_enabled_rss_feeds(getattr(config, "enabled_rss_feeds", []), custom_rss_feeds)
    custom_rss_feed_labels = _normalize_custom_rss_feed_labels(
        getattr(config, "custom_rss_feed_labels", {}),
        custom_rss_feeds,
    )
    supported = build_supported_rss_feeds(custom_rss_feeds, custom_rss_feed_labels)
    return {
        feed["key"]: feed["label"]
        for feed in supported
        if feed["url"] in enabled_rss_feeds
    }


def _maybe_import_legacy_app_config(db: Session) -> AppConfig | None:
    if not LEGACY_BACKEND_DB_PATH.exists() or LEGACY_BACKEND_DB_PATH.resolve() == ROOT_DB_PATH.resolve():
        return None

    try:
        conn = sqlite3.connect(str(LEGACY_BACKEND_DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM app_config WHERE id = 1").fetchone()
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not row:
        return None

    def parse_json(value: Any, fallback: Any) -> Any:
        if value in (None, ""):
            return fallback
        if isinstance(value, (list, dict)):
            return value
        try:
            return json.loads(value)
        except Exception:
            return fallback

    def row_value(column: str, fallback: Any = None) -> Any:
        return row[column] if column in row.keys() else fallback

    legacy_custom_symbols = _normalize_custom_symbols(parse_json(row_value("custom_symbols", []), []))
    legacy_custom_rss_feeds = _normalize_custom_rss_feeds(parse_json(row_value("custom_rss_feeds", []), []))

    config = AppConfig(
        id=1,
        auto_run_enabled=bool(row_value("auto_run_enabled", True)),
        auto_run_interval_minutes=int(row_value("auto_run_interval_minutes", 30) or 30),
        tracked_symbols=_normalize_symbols(parse_json(row_value("tracked_symbols", []), []), fallback=DEFAULT_TRACKED_SYMBOLS.copy()),
        custom_symbols=legacy_custom_symbols,
        max_posts=int(row_value("max_posts", 50) or 50),
        include_backtest=bool(row_value("include_backtest", True)),
        lookback_days=int(row_value("lookback_days", 14) or 14),
        symbol_prompt_overrides=parse_json(row_value("symbol_prompt_overrides", {}), {}),
        symbol_company_aliases=_normalize_symbol_company_aliases(
            parse_json(row_value("symbol_company_aliases", {}), {}),
            build_supported_symbols(legacy_custom_symbols),
        ),
        symbol_proxy_terms=_normalize_symbol_proxy_terms(
            parse_json(row_value("symbol_proxy_terms", {}), {}),
            build_supported_symbols(legacy_custom_symbols),
        ),
        symbol_proxy_terms_generated_at=_normalize_symbol_proxy_terms_generated_at(
            parse_json(row_value("symbol_proxy_terms_generated_at", {}), {}),
            build_supported_symbols(legacy_custom_symbols),
        ),
        display_timezone=_normalize_display_timezone(row_value("display_timezone", "")),
        enabled_rss_feeds=_normalize_enabled_rss_feeds(parse_json(row_value("enabled_rss_feeds", []), []), legacy_custom_rss_feeds),
        custom_rss_feeds=legacy_custom_rss_feeds,
        custom_rss_feed_labels=_normalize_custom_rss_feed_labels(
            parse_json(row_value("custom_rss_feed_labels", {}), {}),
            legacy_custom_rss_feeds,
        ),
        rss_article_detail_mode=_normalize_rss_article_detail_mode(row_value("rss_article_detail_mode", DEFAULT_RSS_ARTICLE_DETAIL_MODE)),
        rss_article_limits=_normalize_rss_article_limits(parse_json(row_value("rss_article_limits", {}), {})),
        data_ingestion_interval_seconds=int(row_value("data_ingestion_interval_seconds", 900) or 900),
        snapshot_retention_limit=int(row_value("snapshot_retention_limit", DEFAULT_SNAPSHOT_RETENTION_LIMIT) or DEFAULT_SNAPSHOT_RETENTION_LIMIT),
        trade_retention_days=int(row_value("trade_retention_days", DEFAULT_TRADE_RETENTION_DAYS) or DEFAULT_TRADE_RETENTION_DAYS),
        web_research_enabled=bool(row_value("web_research_enabled", True)),
        allow_extended_hours_trading=bool(row_value("allow_extended_hours_trading", True)),
        remote_snapshot_enabled=bool(row_value("remote_snapshot_enabled", False)),
        telegram_remote_control_enabled=bool(row_value("telegram_remote_control_enabled", False)),
        telegram_remote_control_banner_active=bool(row_value("telegram_remote_control_banner_active", False)),
        telegram_remote_control_banner_message=str(row_value("telegram_remote_control_banner_message", "") or "") or None,
        telegram_remote_control_banner_updated_at=datetime.fromisoformat(row_value("telegram_remote_control_banner_updated_at")) if row_value("telegram_remote_control_banner_updated_at") else None,
        remote_snapshot_mode=_normalize_remote_snapshot_mode(row_value("remote_snapshot_mode", DEFAULT_REMOTE_SNAPSHOT_MODE)),
        remote_snapshot_min_pnl_change_usd=float(row_value("remote_snapshot_min_pnl_change_usd", 5.0) or 5.0),
        remote_snapshot_heartbeat_minutes=int(row_value("remote_snapshot_heartbeat_minutes", 360) or 360),
        remote_snapshot_interval_minutes=int(row_value("remote_snapshot_interval_minutes", row_value("remote_snapshot_heartbeat_minutes", 360)) or 360),
        remote_snapshot_send_on_position_change=bool(row_value("remote_snapshot_send_on_position_change", True)),
        remote_snapshot_include_closed_trades=bool(row_value("remote_snapshot_include_closed_trades", False)),
        remote_snapshot_max_recommendations=int(row_value("remote_snapshot_max_recommendations", 4) or 4),
        risk_policy=_normalize_risk_policy(parse_json(row_value("risk_policy", {}), {})),
        last_analysis_started_at=datetime.fromisoformat(row_value("last_analysis_started_at")) if row_value("last_analysis_started_at") else None,
        last_analysis_completed_at=datetime.fromisoformat(row_value("last_analysis_completed_at")) if row_value("last_analysis_completed_at") else None,
        last_analysis_request_id=row_value("last_analysis_request_id"),
        last_remote_snapshot_sent_at=datetime.fromisoformat(row_value("last_remote_snapshot_sent_at")) if row_value("last_remote_snapshot_sent_at") else None,
        last_remote_snapshot_request_id=row_value("last_remote_snapshot_request_id"),
        last_remote_snapshot_net_pnl=float(row_value("last_remote_snapshot_net_pnl")) if row_value("last_remote_snapshot_net_pnl") is not None else None,
        last_remote_snapshot_recommendation_fingerprint=row_value("last_remote_snapshot_recommendation_fingerprint"),
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def _apply_env_overrides(config: AppConfig, db: Session) -> bool:
    """Apply environment variable overrides to config (Docker-friendly)."""
    changed = False
    
    
    # INFERENCE_BACKEND: "ollama" | "vllm" | "openai" | "omlx"
    env_backend = os.getenv("INFERENCE_BACKEND", "").strip().lower()
    if env_backend and env_backend in VALID_INFERENCE_BACKENDS:
        if getattr(config, "inference_backend", "") != env_backend:
            config.inference_backend = env_backend
            changed = True
    
    # OPENAI_API_KEY (already handled by secret_store fallback, but ensure backend is set)
    if env_backend == "openai" and os.getenv("OPENAI_API_KEY"):
        if getattr(config, "cloud_provider", "") != "openai":
            config.cloud_provider = "openai"
            changed = True
    
    # OPENAI_BASE_URL
    env_openai_url = os.getenv("OPENAI_BASE_URL", "").strip()
    if env_openai_url:
        from services.openai_client import _validate_base_url as _validate_openai_url
        try:
            normalized = _validate_openai_url(env_openai_url)
            if getattr(config, "openai_base_url", "") != normalized:
                config.openai_base_url = normalized
                changed = True
        except ValueError:
            pass
    
    # OPENAI_MODEL
    env_openai_model = os.getenv("OPENAI_MODEL", "").strip()
    if env_openai_model:
        if getattr(config, "openai_model", "") != env_openai_model:
            config.openai_model = env_openai_model
            changed = True
    
    # OLLAMA_URL (for Docker pointing to host)
    env_ollama_url = os.getenv("OLLAMA_URL", "").strip()
    if env_ollama_url:
        # Convert OLLAMA_URL to full API endpoint if needed
        if not env_ollama_url.endswith("/api/generate"):
            full_url = env_ollama_url.rstrip("/") + "/api/generate"
        else:
            full_url = env_ollama_url
        if getattr(config, "ollama_url", "") != full_url:
            config.ollama_url = full_url
            changed = True
    
    if changed:
        db.add(config)
        db.commit()
        db.refresh(config)
    return changed


def get_or_create_app_config(db: Session) -> AppConfig:
    config = db.query(AppConfig).filter(AppConfig.id == 1).first()
    if config:
        changed = False
        
        # Apply environment variable overrides (Docker-friendly)
        changed = _apply_env_overrides(config, db) or changed
        normalized_auto_run_enabled = _coerce_bool(getattr(config, "auto_run_enabled", True), True)
        if getattr(config, "auto_run_enabled", None) != normalized_auto_run_enabled:
            config.auto_run_enabled = normalized_auto_run_enabled
            changed = True
        normalized_auto_run_interval_minutes = _coerce_int(
            getattr(config, "auto_run_interval_minutes", 30),
            30,
            5,
            360,
        )
        if getattr(config, "auto_run_interval_minutes", None) != normalized_auto_run_interval_minutes:
            config.auto_run_interval_minutes = normalized_auto_run_interval_minutes
            changed = True
        custom_symbols = _infer_custom_symbols(
            getattr(config, "tracked_symbols", []),
            getattr(config, "custom_symbols", []),
        )
        if getattr(config, "custom_symbols", None) != custom_symbols:
            config.custom_symbols = custom_symbols
            changed = True
        tracked_symbols = _normalize_tracked_symbols(getattr(config, "tracked_symbols", []), custom_symbols)
        if not tracked_symbols:
            tracked_symbols = DEFAULT_TRACKED_SYMBOLS.copy()
        if getattr(config, "tracked_symbols", None) != tracked_symbols:
            config.tracked_symbols = tracked_symbols
            changed = True
        normalized_max_posts = _coerce_int(getattr(config, "max_posts", 50), 50, 1, 200)
        if getattr(config, "max_posts", None) != normalized_max_posts:
            config.max_posts = normalized_max_posts
            changed = True
        normalized_include_backtest = _coerce_bool(getattr(config, "include_backtest", True), True)
        if getattr(config, "include_backtest", None) != normalized_include_backtest:
            config.include_backtest = normalized_include_backtest
            changed = True
        normalized_lookback_days = _coerce_int(getattr(config, "lookback_days", 14), 14, 7, 30)
        if getattr(config, "lookback_days", None) != normalized_lookback_days:
            config.lookback_days = normalized_lookback_days
            changed = True
        if config.symbol_prompt_overrides is None:
            config.symbol_prompt_overrides = {}
            changed = True
        if getattr(config, "symbol_company_aliases", None) is None:
            config.symbol_company_aliases = {}
            changed = True
        supported_symbols = build_supported_symbols(custom_symbols)
        normalized_prompt_overrides = _normalize_prompt_overrides(
            config.symbol_prompt_overrides,
            supported_symbols,
        )
        if config.symbol_prompt_overrides != normalized_prompt_overrides:
            config.symbol_prompt_overrides = normalized_prompt_overrides
            changed = True
        normalized_symbol_company_aliases = _normalize_symbol_company_aliases(
            getattr(config, "symbol_company_aliases", {}),
            supported_symbols,
        )
        if getattr(config, "symbol_company_aliases", None) != normalized_symbol_company_aliases:
            config.symbol_company_aliases = normalized_symbol_company_aliases
            changed = True
        if getattr(config, "symbol_proxy_terms", None) is None:
            config.symbol_proxy_terms = {}
            changed = True
        normalized_symbol_proxy_terms = _normalize_symbol_proxy_terms(
            getattr(config, "symbol_proxy_terms", {}),
            supported_symbols,
        )
        if getattr(config, "symbol_proxy_terms", None) != normalized_symbol_proxy_terms:
            config.symbol_proxy_terms = normalized_symbol_proxy_terms
            changed = True
        if getattr(config, "symbol_proxy_terms_generated_at", None) is None:
            config.symbol_proxy_terms_generated_at = {}
            changed = True
        normalized_symbol_proxy_terms_generated_at = _normalize_symbol_proxy_terms_generated_at(
            getattr(config, "symbol_proxy_terms_generated_at", {}),
            supported_symbols,
        )
        if getattr(config, "symbol_proxy_terms_generated_at", None) != normalized_symbol_proxy_terms_generated_at:
            config.symbol_proxy_terms_generated_at = normalized_symbol_proxy_terms_generated_at
            changed = True
        normalized_display_timezone = _normalize_display_timezone(getattr(config, "display_timezone", ""))
        if getattr(config, "display_timezone", "") != normalized_display_timezone:
            config.display_timezone = normalized_display_timezone
            changed = True
        custom_rss_feeds = _normalize_custom_rss_feeds(getattr(config, "custom_rss_feeds", []))
        if getattr(config, "custom_rss_feeds", None) != custom_rss_feeds:
            config.custom_rss_feeds = custom_rss_feeds
            changed = True
        if getattr(config, "custom_rss_feed_labels", None) is None:
            config.custom_rss_feed_labels = {}
            changed = True
        normalized_custom_rss_feed_labels = _normalize_custom_rss_feed_labels(
            getattr(config, "custom_rss_feed_labels", {}),
            custom_rss_feeds,
        )
        if getattr(config, "custom_rss_feed_labels", None) != normalized_custom_rss_feed_labels:
            config.custom_rss_feed_labels = normalized_custom_rss_feed_labels
            changed = True
        normalized_enabled_rss_feeds = _normalize_enabled_rss_feeds(
            getattr(config, "enabled_rss_feeds", []),
            custom_rss_feeds,
        )
        if getattr(config, "enabled_rss_feeds", None) != normalized_enabled_rss_feeds:
            config.enabled_rss_feeds = normalized_enabled_rss_feeds
            changed = True
        normalized_rss_article_detail_mode = _normalize_rss_article_detail_mode(
            getattr(config, "rss_article_detail_mode", DEFAULT_RSS_ARTICLE_DETAIL_MODE)
        )
        if getattr(config, "rss_article_detail_mode", None) != normalized_rss_article_detail_mode:
            config.rss_article_detail_mode = normalized_rss_article_detail_mode
            changed = True
        normalized_rss_article_limits = _normalize_rss_article_limits(getattr(config, "rss_article_limits", {}))
        if getattr(config, "rss_article_limits", None) != normalized_rss_article_limits:
            config.rss_article_limits = normalized_rss_article_limits
            changed = True
        normalized_data_ingestion_interval_seconds = _coerce_int(
            getattr(config, "data_ingestion_interval_seconds", 900),
            900,
            60,
            3600,
        )
        if getattr(config, "data_ingestion_interval_seconds", None) != normalized_data_ingestion_interval_seconds:
            config.data_ingestion_interval_seconds = normalized_data_ingestion_interval_seconds
            changed = True
        if getattr(config, "snapshot_retention_limit", None) is None:
            config.snapshot_retention_limit = DEFAULT_SNAPSHOT_RETENTION_LIMIT
            changed = True
        if getattr(config, "trade_retention_days", None) is None:
            config.trade_retention_days = DEFAULT_TRADE_RETENTION_DAYS
            changed = True
        if getattr(config, "web_research_enabled", None) is None:
            config.web_research_enabled = True
            changed = True
        if getattr(config, "allow_extended_hours_trading", None) is None:
            config.allow_extended_hours_trading = True
            changed = True
        if getattr(config, "risk_policy", None) is None:
            config.risk_policy = dict(DEFAULT_RISK_POLICY)
            changed = True
        normalized_risk_policy = _normalize_risk_policy(getattr(config, "risk_policy", {}))
        if getattr(config, "risk_policy", None) != normalized_risk_policy:
            config.risk_policy = normalized_risk_policy
            changed = True
        normalized_execution_mode = _normalize_alpaca_execution_mode(
            getattr(config, "alpaca_execution_mode", DEFAULT_ALPACA_EXECUTION_MODE)
        )
        if getattr(config, "alpaca_execution_mode", None) != normalized_execution_mode:
            config.alpaca_execution_mode = normalized_execution_mode
            changed = True
        if getattr(config, "alpaca_live_trading_enabled", None) != (normalized_execution_mode == "live"):
            config.alpaca_live_trading_enabled = normalized_execution_mode == "live"
            changed = True
        # ── Preserve admin-configured OpenAI/cloud LLM fields across restarts ──
        # These must NOT be overwritten by DB defaults when a column migration
        # adds them to an existing row. Only set the default if the value is
        # genuinely unset (None), not if it already holds a user-chosen URL/model.
        if getattr(config, "openai_base_url", None) is None:
            config.openai_base_url = DEFAULT_OPENAI_BASE_URL
            changed = True
        if getattr(config, "openai_model", None) is None:
            config.openai_model = DEFAULT_OPENAI_MODEL
            changed = True
        if getattr(config, "remote_snapshot_enabled", None) is None:
            config.remote_snapshot_enabled = False
            changed = True
        if getattr(config, "telegram_remote_control_enabled", None) is None:
            config.telegram_remote_control_enabled = False
            changed = True
        if getattr(config, "telegram_remote_control_banner_active", None) is None:
            config.telegram_remote_control_banner_active = False
            changed = True
        normalized_remote_snapshot_mode = _normalize_remote_snapshot_mode(
            getattr(config, "remote_snapshot_mode", DEFAULT_REMOTE_SNAPSHOT_MODE)
        )
        if getattr(config, "remote_snapshot_mode", None) != normalized_remote_snapshot_mode:
            config.remote_snapshot_mode = normalized_remote_snapshot_mode
            changed = True
        try:
            remote_snapshot_min_pnl_change_usd = float(getattr(config, "remote_snapshot_min_pnl_change_usd", 5.0) or 5.0)
        except (TypeError, ValueError):
            remote_snapshot_min_pnl_change_usd = 5.0
        remote_snapshot_min_pnl_change_usd = round(max(0.0, min(100000.0, remote_snapshot_min_pnl_change_usd)), 2)
        if getattr(config, "remote_snapshot_min_pnl_change_usd", None) != remote_snapshot_min_pnl_change_usd:
            config.remote_snapshot_min_pnl_change_usd = remote_snapshot_min_pnl_change_usd
            changed = True
        try:
            remote_snapshot_heartbeat_minutes = int(getattr(config, "remote_snapshot_heartbeat_minutes", 360) or 360)
        except (TypeError, ValueError):
            remote_snapshot_heartbeat_minutes = 360
        remote_snapshot_heartbeat_minutes = max(15, min(7 * 24 * 60, remote_snapshot_heartbeat_minutes))
        if getattr(config, "remote_snapshot_heartbeat_minutes", None) != remote_snapshot_heartbeat_minutes:
            config.remote_snapshot_heartbeat_minutes = remote_snapshot_heartbeat_minutes
            changed = True
        try:
            remote_snapshot_interval_minutes = int(
                getattr(config, "remote_snapshot_interval_minutes", getattr(config, "remote_snapshot_heartbeat_minutes", 360)) or 360
            )
        except (TypeError, ValueError):
            remote_snapshot_interval_minutes = 360
        remote_snapshot_interval_minutes = max(15, min(7 * 24 * 60, remote_snapshot_interval_minutes))
        if getattr(config, "remote_snapshot_interval_minutes", None) != remote_snapshot_interval_minutes:
            config.remote_snapshot_interval_minutes = remote_snapshot_interval_minutes
            changed = True
        if getattr(config, "remote_snapshot_send_on_position_change", None) is None:
            config.remote_snapshot_send_on_position_change = True
            changed = True
        if getattr(config, "remote_snapshot_include_closed_trades", None) is None:
            config.remote_snapshot_include_closed_trades = False
            changed = True
        try:
            remote_snapshot_max_recommendations = int(getattr(config, "remote_snapshot_max_recommendations", 4) or 4)
        except (TypeError, ValueError):
            remote_snapshot_max_recommendations = 4
        remote_snapshot_max_recommendations = max(1, min(12, remote_snapshot_max_recommendations))
        if getattr(config, "remote_snapshot_max_recommendations", None) != remote_snapshot_max_recommendations:
            config.remote_snapshot_max_recommendations = remote_snapshot_max_recommendations
            changed = True
        if changed:
            db.add(config)
            db.commit()
            db.refresh(config)
        return config

    imported = _maybe_import_legacy_app_config(db)
    if imported:
        return imported

    config = AppConfig(
        id=1,
        auto_run_enabled=True,
        auto_run_interval_minutes=30,
        tracked_symbols=DEFAULT_TRACKED_SYMBOLS.copy(),
        custom_symbols=[],
        max_posts=50,
        include_backtest=True,
        lookback_days=14,
        symbol_prompt_overrides={},
        symbol_company_aliases={},
        symbol_proxy_terms={},
        symbol_proxy_terms_generated_at={},
        display_timezone="",
        enabled_rss_feeds=DEFAULT_RSS_FEED_URLS.copy(),
        custom_rss_feeds=[],
        custom_rss_feed_labels={},
        rss_article_detail_mode=DEFAULT_RSS_ARTICLE_DETAIL_MODE,
        rss_article_limits=dict(DEFAULT_RSS_ARTICLE_LIMITS),
        data_ingestion_interval_seconds=900,
        snapshot_retention_limit=DEFAULT_SNAPSHOT_RETENTION_LIMIT,
        trade_retention_days=DEFAULT_TRADE_RETENTION_DAYS,
        web_research_enabled=True,
        allow_extended_hours_trading=True,
        alpaca_execution_mode=DEFAULT_ALPACA_EXECUTION_MODE,
        remote_snapshot_enabled=False,
        telegram_remote_control_enabled=False,
        telegram_remote_control_banner_active=False,
        telegram_remote_control_banner_message=None,
        telegram_remote_control_banner_updated_at=None,
        remote_snapshot_mode=DEFAULT_REMOTE_SNAPSHOT_MODE,
        remote_snapshot_min_pnl_change_usd=5.0,
        remote_snapshot_heartbeat_minutes=360,
        remote_snapshot_interval_minutes=360,
        remote_snapshot_send_on_position_change=True,
        remote_snapshot_include_closed_trades=False,
        remote_snapshot_max_recommendations=4,
        risk_policy=dict(DEFAULT_RISK_POLICY),
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    _apply_env_overrides(config, db)
    return config


def update_app_config(db: Session, payload: Dict[str, Any]) -> AppConfig:
    config = get_or_create_app_config(db)

    custom_symbols = _infer_custom_symbols(
        payload.get("tracked_symbols", config.tracked_symbols),
        payload.get("custom_symbols", getattr(config, "custom_symbols", [])),
    )
    tracked_symbols = _normalize_tracked_symbols(
        payload.get("tracked_symbols", config.tracked_symbols),
        custom_symbols,
    )
    if not tracked_symbols:
        tracked_symbols = config.tracked_symbols or DEFAULT_TRACKED_SYMBOLS.copy()

    custom_rss_feeds = _normalize_custom_rss_feeds(
        payload.get("custom_rss_feeds", getattr(config, "custom_rss_feeds", []))
    )
    custom_rss_feed_labels = _normalize_custom_rss_feed_labels(
        payload.get("custom_rss_feed_labels", getattr(config, "custom_rss_feed_labels", {})),
        custom_rss_feeds,
    )
    enabled_rss_feeds = _normalize_enabled_rss_feeds(
        payload.get("enabled_rss_feeds", getattr(config, "enabled_rss_feeds", DEFAULT_RSS_FEED_URLS)),
        custom_rss_feeds,
    )

    if "auto_run_enabled" in payload:
        config.auto_run_enabled = bool(payload.get("auto_run_enabled"))
    if "auto_run_interval_minutes" in payload:
        try:
            value = int(payload.get("auto_run_interval_minutes"))
        except (TypeError, ValueError):
            value = config.auto_run_interval_minutes
        config.auto_run_interval_minutes = max(5, min(360, value))

    config.custom_symbols = custom_symbols
    config.tracked_symbols = tracked_symbols

    if "max_posts" in payload:
        try:
            value = int(payload.get("max_posts"))
        except (TypeError, ValueError):
            value = config.max_posts
        config.max_posts = max(1, min(200, value))
    if "include_backtest" in payload:
        config.include_backtest = bool(payload.get("include_backtest"))
    if "lookback_days" in payload:
        try:
            value = int(payload.get("lookback_days"))
        except (TypeError, ValueError):
            value = config.lookback_days
        config.lookback_days = max(7, min(30, value))
    if "symbol_prompt_overrides" in payload:
        config.symbol_prompt_overrides = _normalize_prompt_overrides(
            payload.get("symbol_prompt_overrides"),
            build_supported_symbols(custom_symbols),
        )
    if "display_timezone" in payload:
        config.display_timezone = _normalize_display_timezone(payload.get("display_timezone"))
    if "symbol_company_aliases" in payload:
        config.symbol_company_aliases = _normalize_symbol_company_aliases(
            payload.get("symbol_company_aliases"),
            build_supported_symbols(custom_symbols),
        )
    if "symbol_proxy_terms" in payload:
        config.symbol_proxy_terms = _normalize_symbol_proxy_terms(
            payload.get("symbol_proxy_terms"),
            build_supported_symbols(custom_symbols),
        )
    else:
        config.symbol_proxy_terms = _normalize_symbol_proxy_terms(
            getattr(config, "symbol_proxy_terms", {}),
            build_supported_symbols(custom_symbols),
        )
    if "symbol_proxy_terms_generated_at" in payload:
        config.symbol_proxy_terms_generated_at = _normalize_symbol_proxy_terms_generated_at(
            payload.get("symbol_proxy_terms_generated_at"),
            build_supported_symbols(custom_symbols),
        )
    else:
        config.symbol_proxy_terms_generated_at = _normalize_symbol_proxy_terms_generated_at(
            getattr(config, "symbol_proxy_terms_generated_at", {}),
            build_supported_symbols(custom_symbols),
        )
    if "enabled_rss_feeds" in payload or "custom_rss_feeds" in payload or "custom_rss_feed_labels" in payload:
        config.custom_rss_feeds = custom_rss_feeds
        config.custom_rss_feed_labels = custom_rss_feed_labels
        config.enabled_rss_feeds = enabled_rss_feeds
    if "rss_article_detail_mode" in payload:
        config.rss_article_detail_mode = _normalize_rss_article_detail_mode(payload.get("rss_article_detail_mode"))
    if "rss_article_limits" in payload:
        config.rss_article_limits = _normalize_rss_article_limits(payload.get("rss_article_limits"))
    if "data_ingestion_interval_seconds" in payload:
        try:
            value = int(payload.get("data_ingestion_interval_seconds"))
        except (TypeError, ValueError):
            value = config.data_ingestion_interval_seconds
        config.data_ingestion_interval_seconds = max(60, min(3600, value))
    if "snapshot_retention_limit" in payload:
        try:
            value = int(payload.get("snapshot_retention_limit"))
        except (TypeError, ValueError):
            value = getattr(config, "snapshot_retention_limit", DEFAULT_SNAPSHOT_RETENTION_LIMIT)
        config.snapshot_retention_limit = max(1, min(100, value))
    if "trade_retention_days" in payload:
        try:
            value = int(payload.get("trade_retention_days"))
        except (TypeError, ValueError):
            value = getattr(config, "trade_retention_days", DEFAULT_TRADE_RETENTION_DAYS)
        config.trade_retention_days = max(MIN_TRADE_RETENTION_DAYS, min(MAX_TRADE_RETENTION_DAYS, value))
    if "extraction_model" in payload:
        config.extraction_model = str(payload.get("extraction_model") or "").strip()
    if "reasoning_model" in payload:
        config.reasoning_model = str(payload.get("reasoning_model") or "").strip()
    if "ollama_parallel_slots" in payload:
        try:
            slots = int(payload.get("ollama_parallel_slots") or 1)
        except (TypeError, ValueError):
            slots = 1
        config.ollama_parallel_slots = max(1, min(8, slots))
    if "red_team_enabled" in payload:
        config.red_team_enabled = bool(payload.get("red_team_enabled"))
    if "inference_backend" in payload:
        config.inference_backend = _normalize_inference_backend(payload.get("inference_backend"))
    if "ollama_url" in payload:
        config.ollama_url = str(payload.get("ollama_url") or "").strip() or "http://localhost:11434/api/generate"
    if "vllm_url" in payload:
        config.vllm_url = str(payload.get("vllm_url") or "").strip() or "http://localhost:8000"
    if "openai_base_url" in payload:
        raw = str(payload.get("openai_base_url") or "").strip()
        # Validate with the same logic as openai_client._validate_base_url
        from services.openai_client import _validate_base_url as _validate_openai_url
        try:
            normalized = _validate_openai_url(raw) if raw else DEFAULT_OPENAI_BASE_URL
        except ValueError:
            normalized = getattr(config, "openai_base_url", DEFAULT_OPENAI_BASE_URL)
        config.openai_base_url = normalized
    if "api_mode" in payload:
        config.api_mode = str(payload.get("api_mode") or "local").strip().lower()
    if "cloud_provider" in payload:
        config.cloud_provider = str(payload.get("cloud_provider") or "openai").strip().lower()
    if "local_provider" in payload:
        config.local_provider = str(payload.get("local_provider") or "ollama").strip().lower()
    if "openai_model" in payload:
        config.openai_model = str(payload.get("openai_model") or "").strip() or DEFAULT_OPENAI_MODEL
    if "risk_profile" in payload:
        config.risk_profile = _normalize_risk_profile(payload.get("risk_profile"))
    if "risk_policy" in payload:
        config.risk_policy = _normalize_risk_policy(payload.get("risk_policy"))
    else:
        config.risk_policy = _normalize_risk_policy(getattr(config, "risk_policy", {}))
    if "allow_extended_hours_trading" in payload:
        config.allow_extended_hours_trading = bool(payload.get("allow_extended_hours_trading"))
    if "remote_snapshot_enabled" in payload:
        config.remote_snapshot_enabled = bool(payload.get("remote_snapshot_enabled"))
    if "telegram_remote_control_enabled" in payload:
        config.telegram_remote_control_enabled = bool(payload.get("telegram_remote_control_enabled"))
    if "remote_snapshot_mode" in payload:
        config.remote_snapshot_mode = _normalize_remote_snapshot_mode(payload.get("remote_snapshot_mode"))
    if "remote_snapshot_min_pnl_change_usd" in payload:
        try:
            value = float(payload.get("remote_snapshot_min_pnl_change_usd"))
        except (TypeError, ValueError):
            value = getattr(config, "remote_snapshot_min_pnl_change_usd", 5.0)
        config.remote_snapshot_min_pnl_change_usd = round(max(0.0, min(100000.0, value)), 2)
    if "remote_snapshot_heartbeat_minutes" in payload:
        try:
            value = int(payload.get("remote_snapshot_heartbeat_minutes"))
        except (TypeError, ValueError):
            value = getattr(config, "remote_snapshot_heartbeat_minutes", 360)
        config.remote_snapshot_heartbeat_minutes = max(15, min(7 * 24 * 60, value))
    if "remote_snapshot_interval_minutes" in payload:
        try:
            value = int(payload.get("remote_snapshot_interval_minutes"))
        except (TypeError, ValueError):
            value = getattr(config, "remote_snapshot_interval_minutes", 360)
        normalized_interval = max(15, min(7 * 24 * 60, value))
        config.remote_snapshot_interval_minutes = normalized_interval
        if hasattr(config, "remote_snapshot_heartbeat_minutes"):
            config.remote_snapshot_heartbeat_minutes = normalized_interval
    if "remote_snapshot_send_on_position_change" in payload:
        config.remote_snapshot_send_on_position_change = bool(payload.get("remote_snapshot_send_on_position_change"))
    if "remote_snapshot_include_closed_trades" in payload:
        config.remote_snapshot_include_closed_trades = bool(payload.get("remote_snapshot_include_closed_trades"))
    if "remote_snapshot_max_recommendations" in payload:
        try:
            value = int(payload.get("remote_snapshot_max_recommendations"))
        except (TypeError, ValueError):
            value = getattr(config, "remote_snapshot_max_recommendations", 4)
        config.remote_snapshot_max_recommendations = max(1, min(12, value))
    if "vol_sizing_portfolio_cap_usd" in payload:
        config.vol_sizing_portfolio_cap_usd = _normalize_trading_logic_float(payload.get("vol_sizing_portfolio_cap_usd"), 1.0, 10_000_000.0)
    if "paper_trade_amount" in payload:
        config.paper_trade_amount = _normalize_trading_logic_float(payload.get("paper_trade_amount"), 1.0, 100000.0)
    if "entry_threshold" in payload:
        config.entry_threshold = _normalize_trading_logic_float(payload.get("entry_threshold"), 0.05, 1.0)
    if "stop_loss_pct" in payload:
        config.stop_loss_pct = _normalize_trading_logic_float(payload.get("stop_loss_pct"), 0.1, 50.0)
    if "take_profit_pct" in payload:
        config.take_profit_pct = _normalize_trading_logic_float(payload.get("take_profit_pct"), 0.1, 100.0)
    if "materiality_min_posts_delta" in payload:
        config.materiality_min_posts_delta = _normalize_trading_logic_int(payload.get("materiality_min_posts_delta"), 1, 100)
    if "materiality_min_sentiment_delta" in payload:
        config.materiality_min_sentiment_delta = _normalize_trading_logic_float(payload.get("materiality_min_sentiment_delta"), 0.01, 1.0)
    if "hold_overnight" in payload:
        config.hold_overnight = bool(payload.get("hold_overnight"))
    if "trail_on_window_expiry" in payload:
        config.trail_on_window_expiry = bool(payload.get("trail_on_window_expiry"))
    if "reentry_cooldown_minutes" in payload:
        config.reentry_cooldown_minutes = _normalize_trading_logic_int(payload.get("reentry_cooldown_minutes"), 0, 10080)
    if "min_same_day_exit_edge_pct" in payload:
        config.min_same_day_exit_edge_pct = _normalize_trading_logic_float(payload.get("min_same_day_exit_edge_pct"), 0.0, 25.0)
    if "alpaca_live_trading_enabled" in payload:
        config.alpaca_live_trading_enabled = _coerce_bool(payload.get("alpaca_live_trading_enabled"), False)
        config.alpaca_execution_mode = "live" if config.alpaca_live_trading_enabled else DEFAULT_ALPACA_EXECUTION_MODE
    if "alpaca_execution_mode" in payload:
        config.alpaca_execution_mode = _normalize_alpaca_execution_mode(payload.get("alpaca_execution_mode"))
        config.alpaca_live_trading_enabled = config.alpaca_execution_mode == "live"
    if "alpaca_allow_short_selling" in payload:
        config.alpaca_allow_short_selling = _coerce_bool(payload.get("alpaca_allow_short_selling"), False)
    if "alpaca_fixed_order_size" in payload:
        config.alpaca_fixed_order_size = _coerce_bool(payload.get("alpaca_fixed_order_size"), False)
    if "alpaca_paper_trade_amount_usd" in payload:
        config.alpaca_paper_trade_amount_usd = _normalize_trading_logic_float(payload.get("alpaca_paper_trade_amount_usd"), 1.0, 1_000_000.0)
    if "alpaca_live_trade_amount_usd" in payload:
        config.alpaca_live_trade_amount_usd = _normalize_trading_logic_float(payload.get("alpaca_live_trade_amount_usd"), 1.0, 1_000_000.0)
    if "alpaca_max_position_usd" in payload:
        config.alpaca_max_position_usd = _normalize_trading_logic_float(payload.get("alpaca_max_position_usd"), 1.0, 1_000_000.0)
    if "alpaca_max_total_exposure_usd" in payload:
        config.alpaca_max_total_exposure_usd = _normalize_trading_logic_float(payload.get("alpaca_max_total_exposure_usd"), 1.0, 10_000_000.0)
    if "alpaca_order_type" in payload:
        v = str(payload.get("alpaca_order_type") or "market").strip().lower()
        config.alpaca_order_type = v if v in {"market", "limit"} else "market"
    if "alpaca_limit_slippage_pct" in payload:
        config.alpaca_limit_slippage_pct = _normalize_trading_logic_float(payload.get("alpaca_limit_slippage_pct"), 0.0001, 0.05) or 0.002
    if "alpaca_daily_loss_limit_usd" in payload:
        config.alpaca_daily_loss_limit_usd = _normalize_trading_logic_float(payload.get("alpaca_daily_loss_limit_usd"), 1.0, 1_000_000.0)
    if "alpaca_max_consecutive_losses" in payload:
        config.alpaca_max_consecutive_losses = _normalize_trading_logic_int(payload.get("alpaca_max_consecutive_losses"), 1, 50)
    if "circuit_breaker_fired_at" in payload:
        config.circuit_breaker_fired_at = payload.get("circuit_breaker_fired_at")
    if "circuit_breaker_reason" in payload:
        config.circuit_breaker_reason = payload.get("circuit_breaker_reason")
    # ── Strategy feature toggles (null = use logic_config.json default) ──
    if "continuous_entry_enabled" in payload:
        val = payload.get("continuous_entry_enabled")
        config.continuous_entry_enabled = _coerce_bool(val, None) if val is not None else None
    if "regime_adaptation_enabled" in payload:
        val = payload.get("regime_adaptation_enabled")
        config.regime_adaptation_enabled = _coerce_bool(val, None) if val is not None else None
    if "hold_decay_enabled" in payload:
        val = payload.get("hold_decay_enabled")
        config.hold_decay_enabled = _coerce_bool(val, None) if val is not None else None
    if "accumulate_on_confirmation_enabled" in payload:
        val = payload.get("accumulate_on_confirmation_enabled")
        config.accumulate_on_confirmation_enabled = _coerce_bool(val, None) if val is not None else None
    if "accumulate_max_multiplier" in payload:
        config.accumulate_max_multiplier = _normalize_trading_logic_float(payload.get("accumulate_max_multiplier"), 1.0, 100.0)

    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def mark_analysis_started(db: Session, request_id: str) -> AppConfig:
    config = get_or_create_app_config(db)
    config.last_analysis_started_at = datetime.now(timezone.utc)
    config.last_analysis_request_id = request_id
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def mark_analysis_completed(db: Session, request_id: str) -> AppConfig:
    config = get_or_create_app_config(db)
    config.last_analysis_completed_at = datetime.now(timezone.utc)
    config.last_analysis_request_id = request_id
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def try_acquire_analysis_lock(
    db: Session,
    request_id: str,
    lease_seconds: int = 20 * 60,
) -> bool:
    """Atomically claim the analysis run lease if it is free or expired."""
    get_or_create_app_config(db)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=max(60, int(lease_seconds)))
    updated = (
        db.query(AppConfig)
        .filter(AppConfig.id == 1)
        .filter(
            or_(
                AppConfig.analysis_lock_expires_at.is_(None),
                AppConfig.analysis_lock_expires_at < now,
                AppConfig.analysis_lock_request_id == request_id,
            )
        )
        .update(
            {
                AppConfig.analysis_lock_request_id: request_id,
                AppConfig.analysis_lock_acquired_at: now,
                AppConfig.analysis_lock_expires_at: expires_at,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return bool(updated)


def refresh_analysis_lock(
    db: Session,
    request_id: str,
    lease_seconds: int = 20 * 60,
) -> bool:
    """Extend an existing analysis lease for long-running jobs."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=max(60, int(lease_seconds)))
    updated = (
        db.query(AppConfig)
        .filter(AppConfig.id == 1, AppConfig.analysis_lock_request_id == request_id)
        .update(
            {
                AppConfig.analysis_lock_acquired_at: now,
                AppConfig.analysis_lock_expires_at: expires_at,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return bool(updated)


def release_analysis_lock(db: Session, request_id: str) -> None:
    """Release the analysis lease if the caller still owns it."""
    (
        db.query(AppConfig)
        .filter(AppConfig.id == 1, AppConfig.analysis_lock_request_id == request_id)
        .update(
            {
                AppConfig.analysis_lock_request_id: None,
                AppConfig.analysis_lock_acquired_at: None,
                AppConfig.analysis_lock_expires_at: None,
            },
            synchronize_session=False,
        )
    )
    db.commit()


def config_to_dict(config: AppConfig) -> Dict[str, Any]:
    seconds_until_next = 0
    can_auto_run_now = True
    auto_run_enabled = _coerce_bool(getattr(config, "auto_run_enabled", True), True)
    auto_run_interval_minutes = _coerce_int(getattr(config, "auto_run_interval_minutes", 30), 30, 5, 360)
    max_posts = _coerce_int(getattr(config, "max_posts", 50), 50, 1, 200)
    lookback_days = _coerce_int(getattr(config, "lookback_days", 14), 14, 7, 30)
    data_ingestion_interval_seconds = _coerce_int(
        getattr(config, "data_ingestion_interval_seconds", 900),
        900,
        60,
        3600,
    )
    snapshot_retention_limit = _coerce_int(
        getattr(config, "snapshot_retention_limit", DEFAULT_SNAPSHOT_RETENTION_LIMIT),
        DEFAULT_SNAPSHOT_RETENTION_LIMIT,
        1,
        100,
    )
    trade_retention_days = _coerce_int(
        getattr(config, "trade_retention_days", DEFAULT_TRADE_RETENTION_DAYS),
        DEFAULT_TRADE_RETENTION_DAYS,
        MIN_TRADE_RETENTION_DAYS,
        MAX_TRADE_RETENTION_DAYS,
    )
    remote_snapshot_interval_minutes = _coerce_int(
        getattr(config, "remote_snapshot_interval_minutes", getattr(config, "remote_snapshot_heartbeat_minutes", 360)),
        360,
        15,
        7 * 24 * 60,
    )
    remote_snapshot_max_recommendations = _coerce_int(
        getattr(config, "remote_snapshot_max_recommendations", 4),
        4,
        1,
        12,
    )

    if auto_run_enabled and config.last_analysis_started_at:
        # Ensure the stored timestamp is timezone-aware — SQLite may return
        # offset-naive datetimes depending on how they were originally stored.
        started_at = config.last_analysis_started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        next_run_at = started_at + timedelta(minutes=auto_run_interval_minutes)
        remaining = int((next_run_at - datetime.now(timezone.utc)).total_seconds())
        seconds_until_next = max(0, remaining)
        can_auto_run_now = seconds_until_next == 0
    elif not auto_run_enabled:
        can_auto_run_now = False

    custom_symbols = _normalize_custom_symbols(getattr(config, "custom_symbols", []))
    tracked_symbols = _normalize_tracked_symbols(getattr(config, "tracked_symbols", []), custom_symbols)
    custom_rss_feeds = _normalize_custom_rss_feeds(getattr(config, "custom_rss_feeds", []))
    custom_rss_feed_labels = _normalize_custom_rss_feed_labels(
        getattr(config, "custom_rss_feed_labels", {}),
        custom_rss_feeds,
    )
    enabled_rss_feeds = _normalize_enabled_rss_feeds(getattr(config, "enabled_rss_feeds", []), custom_rss_feeds)

    return {
        "auto_run_enabled": auto_run_enabled,
        "auto_run_interval_minutes": auto_run_interval_minutes,
        "tracked_symbols": tracked_symbols or DEFAULT_TRACKED_SYMBOLS.copy(),
        "custom_symbols": custom_symbols,
        "default_symbols": SUPPORTED_SYMBOLS.copy(),
        "max_custom_symbols": MAX_CUSTOM_SYMBOLS,
        "max_posts": max_posts,
        "include_backtest": _coerce_bool(getattr(config, "include_backtest", True), True),
        "lookback_days": lookback_days,
        "symbol_prompt_overrides": _normalize_prompt_overrides(
            config.symbol_prompt_overrides,
            build_supported_symbols(custom_symbols),
        ),
        "symbol_company_aliases": _normalize_symbol_company_aliases(
            getattr(config, "symbol_company_aliases", {}),
            build_supported_symbols(custom_symbols),
        ),
        "symbol_proxy_terms": _normalize_symbol_proxy_terms(
            getattr(config, "symbol_proxy_terms", {}),
            build_supported_symbols(custom_symbols),
        ),
        "symbol_proxy_terms_generated_at": _normalize_symbol_proxy_terms_generated_at(
            getattr(config, "symbol_proxy_terms_generated_at", {}),
            build_supported_symbols(custom_symbols),
        ),
        "display_timezone": _normalize_display_timezone(getattr(config, "display_timezone", "")),
        "default_rss_feeds": DEFAULT_RSS_FEEDS,
        "custom_rss_feeds": custom_rss_feeds,
        "custom_rss_feed_labels": custom_rss_feed_labels,
        "enabled_rss_feeds": enabled_rss_feeds,
        "supported_rss_feeds": build_supported_rss_feeds(custom_rss_feeds, custom_rss_feed_labels),
        "max_custom_rss_feeds": MAX_CUSTOM_RSS_FEEDS,
        "rss_article_detail_mode": _normalize_rss_article_detail_mode(
            getattr(config, "rss_article_detail_mode", DEFAULT_RSS_ARTICLE_DETAIL_MODE)
        ),
        "rss_article_limits": _normalize_rss_article_limits(getattr(config, "rss_article_limits", {})),
        "rss_articles_per_feed": resolve_rss_articles_per_feed(config),
        "data_ingestion_interval_seconds": data_ingestion_interval_seconds,
        "snapshot_retention_limit": snapshot_retention_limit,
        "trade_retention_days": trade_retention_days,
        "extraction_model": str(getattr(config, "extraction_model", "") or ""),
        "reasoning_model": str(getattr(config, "reasoning_model", "") or ""),
        "ollama_parallel_slots": int(getattr(config, "ollama_parallel_slots", 1) or 1),
        "red_team_enabled": bool(getattr(config, "red_team_enabled", True)),
        "inference_backend": _normalize_inference_backend(getattr(config, "inference_backend", DEFAULT_INFERENCE_BACKEND)),
        "ollama_url": str(getattr(config, "ollama_url", "http://localhost:11434/api/generate") or "http://localhost:11434/api/generate"),
        "vllm_url": str(getattr(config, "vllm_url", "http://localhost:8000") or "http://localhost:8000"),
        "openai_base_url": str(getattr(config, "openai_base_url", DEFAULT_OPENAI_BASE_URL) or DEFAULT_OPENAI_BASE_URL),
        "openai_model": str(getattr(config, "openai_model", DEFAULT_OPENAI_MODEL) or DEFAULT_OPENAI_MODEL),
        "api_mode": str(getattr(config, "api_mode", "local") or "local"),
        "cloud_provider": str(getattr(config, "cloud_provider", "openai") or "openai"),
        "local_provider": str(getattr(config, "local_provider", "ollama") or "ollama"),
        "risk_profile": _normalize_risk_profile(getattr(config, "risk_profile", DEFAULT_RISK_PROFILE)),
        "risk_policy": _normalize_risk_policy(getattr(config, "risk_policy", {})),
        "web_research_enabled": bool(getattr(config, "web_research_enabled", True)),
        "allow_extended_hours_trading": bool(getattr(config, "allow_extended_hours_trading", True)),
        "remote_snapshot_enabled": bool(getattr(config, "remote_snapshot_enabled", False)),
        "telegram_remote_control_enabled": bool(getattr(config, "telegram_remote_control_enabled", False)),
        "telegram_remote_control_banner_active": bool(getattr(config, "telegram_remote_control_banner_active", False)),
        "telegram_remote_control_banner_message": str(getattr(config, "telegram_remote_control_banner_message", "") or ""),
        "telegram_remote_control_banner_updated_at": config.telegram_remote_control_banner_updated_at.isoformat() if getattr(config, "telegram_remote_control_banner_updated_at", None) else None,
        "remote_snapshot_mode": _normalize_remote_snapshot_mode(getattr(config, "remote_snapshot_mode", DEFAULT_REMOTE_SNAPSHOT_MODE)),
        "remote_snapshot_interval_minutes": remote_snapshot_interval_minutes,
        "remote_snapshot_send_on_position_change": bool(getattr(config, "remote_snapshot_send_on_position_change", True)),
        "remote_snapshot_include_closed_trades": bool(getattr(config, "remote_snapshot_include_closed_trades", False)),
        "remote_snapshot_max_recommendations": remote_snapshot_max_recommendations,
        # Trading logic overrides — null means "use JSON default"
        "vol_sizing_portfolio_cap_usd": getattr(config, "vol_sizing_portfolio_cap_usd", None),
        "paper_trade_amount": getattr(config, "paper_trade_amount", None),
        "entry_threshold": getattr(config, "entry_threshold", None),
        "stop_loss_pct": getattr(config, "stop_loss_pct", None),
        "take_profit_pct": getattr(config, "take_profit_pct", None),
        "materiality_min_posts_delta": getattr(config, "materiality_min_posts_delta", None),
        "materiality_min_sentiment_delta": getattr(config, "materiality_min_sentiment_delta", None),
        "hold_overnight": bool(getattr(config, "hold_overnight", False)),
        "trail_on_window_expiry": bool(getattr(config, "trail_on_window_expiry", True)),
        "reentry_cooldown_minutes": getattr(config, "reentry_cooldown_minutes", None),
        "min_same_day_exit_edge_pct": getattr(config, "min_same_day_exit_edge_pct", None),
        # Strategy feature toggles (null = use logic_config.json default)
        "continuous_entry_enabled": getattr(config, "continuous_entry_enabled", None),
        "regime_adaptation_enabled": getattr(config, "regime_adaptation_enabled", None),
        "hold_decay_enabled": getattr(config, "hold_decay_enabled", None),
        "accumulate_on_confirmation_enabled": getattr(config, "accumulate_on_confirmation_enabled", None),
        "accumulate_max_multiplier": getattr(config, "accumulate_max_multiplier", None),
        # Alpaca brokerage execution settings
        "alpaca_execution_mode":         _normalize_alpaca_execution_mode(
            getattr(config, "alpaca_execution_mode", DEFAULT_ALPACA_EXECUTION_MODE)
        ),
        "alpaca_live_trading_enabled":   bool(getattr(config, "alpaca_live_trading_enabled",   False)),
        "alpaca_allow_short_selling":    bool(getattr(config, "alpaca_allow_short_selling",    False)),
        "alpaca_fixed_order_size":       bool(getattr(config, "alpaca_fixed_order_size",       False)),
        "alpaca_paper_trade_amount_usd": getattr(config, "alpaca_paper_trade_amount_usd",      None),
        "alpaca_live_trade_amount_usd":  getattr(config, "alpaca_live_trade_amount_usd",       None),
        "alpaca_max_position_usd":       getattr(config, "alpaca_max_position_usd",            None),
        "alpaca_max_total_exposure_usd": getattr(config, "alpaca_max_total_exposure_usd",      None),
        "alpaca_order_type":             str(getattr(config,  "alpaca_order_type",             "market") or "market"),
        "alpaca_limit_slippage_pct":     float(getattr(config, "alpaca_limit_slippage_pct",    0.002) or 0.002),
        "alpaca_daily_loss_limit_usd":   getattr(config, "alpaca_daily_loss_limit_usd",        None),
        "alpaca_max_consecutive_losses": getattr(config, "alpaca_max_consecutive_losses",      3),
        # JSON defaults (read-only, for display)
        "logic_defaults": {
            "paper_trade_amount": _L["paper_trade_amount"],
            "entry_threshold": _L["entry_thresholds"]["normal"],
            "stop_loss_pct": _L["stop_loss_pct"],
            "take_profit_pct": _L["take_profit_pct"],
            "materiality_min_posts_delta": _L["materiality_gate"]["min_posts_delta"],
            "materiality_min_sentiment_delta": _L["materiality_gate"]["min_sentiment_delta"],
            "reentry_cooldown_minutes": int(_L.get("reentry_cooldown_minutes", 120)),
            "min_same_day_exit_edge_pct": float(_L.get("min_same_day_exit_edge_pct", 0.5)),
            # Crazy profile override defaults (shown when risk_profile == "crazy" or "custom")
            "crazy": {
                "entry_threshold": _L.get("crazy", {}).get("entry_thresholds", {}).get("normal",
                    _L["entry_thresholds"]["normal"]),
                "stop_loss_pct": _L.get("crazy", {}).get("stop_loss_pct",
                    _L["stop_loss_pct"]),
                "take_profit_pct": _L.get("crazy", {}).get("take_profit_pct",
                    _L["take_profit_pct"]),
                "materiality_min_posts_delta": _L.get("crazy", {}).get("materiality_gate", {}).get("min_posts_delta",
                    _L["materiality_gate"]["min_posts_delta"]),
                "materiality_min_sentiment_delta": _L.get("crazy", {}).get("materiality_gate", {}).get("min_sentiment_delta",
                    _L["materiality_gate"]["min_sentiment_delta"]),
                "reentry_cooldown_minutes": int(_L.get("crazy", {}).get("reentry_cooldown_minutes",
                    _L.get("reentry_cooldown_minutes", 120))),
                "min_same_day_exit_edge_pct": float(_L.get("crazy", {}).get("min_same_day_exit_edge_pct",
                    _L.get("min_same_day_exit_edge_pct", 0.5))),
            },
        },
        "last_analysis_started_at": config.last_analysis_started_at.isoformat() if config.last_analysis_started_at else None,
        "last_analysis_completed_at": config.last_analysis_completed_at.isoformat() if config.last_analysis_completed_at else None,
        "last_analysis_request_id": config.last_analysis_request_id,
        "last_remote_snapshot_sent_at": config.last_remote_snapshot_sent_at.isoformat() if getattr(config, "last_remote_snapshot_sent_at", None) else None,
        "last_remote_snapshot_request_id": getattr(config, "last_remote_snapshot_request_id", None),
        "seconds_until_next_auto_run": seconds_until_next,
        "can_auto_run_now": can_auto_run_now,
        "supported_symbols": build_supported_symbols(custom_symbols),
        "estimated_analysis_seconds": DEFAULT_ESTIMATED_ANALYSIS_SECONDS,
    }


def config_to_dict_with_stats(db: Session, config: AppConfig) -> Dict[str, Any]:
    payload = config_to_dict(config)
    durations_ms: List[float] = []

    recent_results = (
        db.query(AnalysisResult)
        .order_by(AnalysisResult.timestamp.desc())
        .limit(10)
        .all()
    )

    for result in recent_results:
        run_metadata = result.run_metadata or {}
        try:
            value = float(run_metadata.get("processing_time_ms", 0.0))
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            durations_ms.append(value)

    if durations_ms:
        avg_seconds = round(sum(durations_ms) / len(durations_ms) / 1000)
        payload["estimated_analysis_seconds"] = max(20, min(300, avg_seconds))
        payload["recent_analysis_seconds"] = [round(value / 1000) for value in durations_ms]
    else:
        payload["recent_analysis_seconds"] = []

    return payload
