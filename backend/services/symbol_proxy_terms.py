from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from services.sentiment.engine import SentimentEngine, _keyword_trace_cache
from services.sentiment.prompts import TICKER_PROXY_MAP

PROXY_TERMS_TTL_DAYS = 30


def _normalize_terms(terms: List[str]) -> List[str]:
    normalized: List[str] = []
    for term in terms:
        value = str(term or "").strip().lower()
        if value and value not in normalized:
            normalized.append(value)
        if len(normalized) >= 50:
            break
    return normalized


async def generate_proxy_terms_for_symbol(
    *,
    symbol: str,
    model_name: str,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Generate proxy terms for one symbol using existing Stage 1 logic.
    Returns normalized terms and trace metadata for UI notices/debug.
    """
    sym = str(symbol or "").upper().strip()
    if not sym:
        return {"symbol": "", "terms": [], "trace": {"mode": "invalid", "error": "empty symbol"}}

    engine = SentimentEngine(model_name=model_name)

    if force_refresh:
        # Bypass in-memory cache to force a fresh LLM generation/fallback pass.
        try:
            from services.sentiment import engine as engine_module
            engine_module._keyword_cache.pop(sym, None)
            engine_module._keyword_trace_cache.pop(sym, None)
        except Exception:
            pass

    terms = await engine._generate_symbol_keywords(sym, model_name)
    normalized_terms = _normalize_terms(list(terms or []))
    trace = dict(_keyword_trace_cache.get(sym) or {})

    # Built-ins are static and should not be persisted in config.
    if sym in TICKER_PROXY_MAP:
        return {"symbol": sym, "terms": [], "trace": trace}

    return {"symbol": sym, "terms": normalized_terms, "trace": trace}


async def ensure_symbol_proxy_terms_fresh(
    *,
    db: Session,
    config: Any,
    symbols: List[str],
    model_name: str,
    max_age_days: int = PROXY_TERMS_TTL_DAYS,
    force: bool = False,
) -> Dict[str, List[str]]:
    """
    Persisted-cache gate for Stage 1 proxy terms.

    For each custom symbol (built-ins in TICKER_PROXY_MAP are skipped — they use
    static terms), reuse the DB-persisted terms unless they're missing, stale
    (older than `max_age_days`), or `force` is set. Regenerated terms are
    written back to app_config.symbol_proxy_terms / symbol_proxy_terms_generated_at
    so the next call is a pure cache hit — no LLM call needed until the TTL expires.
    """
    symbol_proxy_terms: Dict[str, List[str]] = dict(getattr(config, "symbol_proxy_terms", {}) or {})
    generated_at_map: Dict[str, str] = dict(getattr(config, "symbol_proxy_terms_generated_at", {}) or {})
    now = datetime.now(timezone.utc)
    changed = False

    for raw_symbol in symbols:
        sym = str(raw_symbol or "").upper().strip()
        if not sym or sym in TICKER_PROXY_MAP:
            continue

        existing_terms = symbol_proxy_terms.get(sym)
        generated_at_raw = generated_at_map.get(sym)

        stale = force or not existing_terms
        if not stale:
            if not generated_at_raw:
                # Terms exist from before this TTL tracking existed — trust them
                # and start the 30-day clock now instead of forcing a regeneration.
                generated_at_map[sym] = now.isoformat()
                changed = True
            else:
                try:
                    generated_at = datetime.fromisoformat(generated_at_raw)
                    if generated_at.tzinfo is None:
                        generated_at = generated_at.replace(tzinfo=timezone.utc)
                    stale = (now - generated_at) > timedelta(days=max_age_days)
                except ValueError:
                    stale = True

        if not stale or not model_name:
            continue

        result = await generate_proxy_terms_for_symbol(
            symbol=sym,
            model_name=model_name,
            force_refresh=force,
        )
        terms = list(result.get("terms") or [])
        if terms:
            symbol_proxy_terms[sym] = terms
            generated_at_map[sym] = now.isoformat()
            changed = True

    if changed:
        config.symbol_proxy_terms = symbol_proxy_terms
        config.symbol_proxy_terms_generated_at = generated_at_map
        db.add(config)
        db.commit()

    return symbol_proxy_terms
