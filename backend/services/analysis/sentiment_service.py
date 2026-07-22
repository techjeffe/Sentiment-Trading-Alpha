"""
SentimentService — two-stage sentiment analysis pipeline.

Encapsulates _analyze_sentiment and all context-building helpers from the
original router.  Wraps SentimentEngine (from services.sentiment.engine)
and delegates Ollama calls to that class.

Data Scoping Note:
  - web_context_by_symbol is computed per-request via fetch_recent_symbol_web_context.
  - The aggregated news context is built from the current post batch only.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from typing import Callable, Awaitable as AwaitableType
from sqlalchemy.orm import Session

from config.logic_loader import LOGIC
from config.market_constants import SYMBOL_RELEVANCE_TERMS
from schemas.analysis import ModelInputDebug, ModelInputArticle
from services.data_ingestion.market_validation import MarketValidationClient
from services.data_ingestion.yfinance_client import PriceClient
from services.ollama import get_ollama_status
from services.sentiment.engine import SentimentEngine, SentimentAnalysisResponse
from services.sentiment.prompts import (
    format_stage2_proxy_appendix,
    format_symbol_specialist_context_prompt,
    expand_proxy_terms_for_matching,
    normalize_text_for_matching,
)
from services.web_research import fetch_recent_symbol_web_context


class SentimentService:
    """Encapsulates sentiment analysis pipeline and context builders."""

    def __init__(
        self,
        price_cache: Any,
        logic_config: dict[str, Any],
    ) -> None:
        self._price_cache = price_cache
        self._L = logic_config

    # ── Public API ───────────────────────────────────────────────────

    async def analyze_sentiment(
        self,
        posts: List[Any],
        symbols: List[str],
        price_context: Dict[str, Any],
        prompt_overrides: Optional[Dict[str, str]],
        model_name: Optional[str],
        extraction_model: Optional[str] = None,
        reasoning_model: Optional[str] = None,
        web_context_by_symbol: Optional[Dict[str, str]] = None,
        symbol_proxy_terms_by_symbol: Optional[Dict[str, List[str]]] = None,
        openai_base_url: Optional[str] = None,
        openai_model: Optional[str] = None,
        ollama_url: Optional[str] = None,
        vllm_url: Optional[str] = None,
        cloud_provider: Optional[str] = None,
        *,
        on_symbol_complete: Optional[Callable[[str, int, int, str, float], AwaitableType[None]]] = None,
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        """
        Two-stage analysis pipeline.

        Stage 1 (extraction_model): classify articles for relevance and extract proxy terms.
        Stage 2 (reasoning_model):  per-symbol specialist analysis on filtered articles only.
        Falls back to single-stage (model_name) when orchestration models are not configured.
        """
        # Forward admin DB config to the engine so it uses the admin-chosen
        # base URL, model, and API key instead of only env-var defaults.
        engine_overrides = {}
        if ollama_url:
            engine_overrides["ollama_url"] = ollama_url
        if vllm_url:
            engine_overrides["vllm_url"] = vllm_url
        if openai_base_url:
            engine_overrides["openai_base_url"] = openai_base_url
        if openai_model:
            engine_overrides["openai_model"] = openai_model
        if cloud_provider:
            engine_overrides["cloud_provider"] = cloud_provider
        engine = SentimentEngine(
            model_name=model_name,
            overrides=engine_overrides if engine_overrides else None,
        )
        engine.clear_cache()
        web_context_by_symbol = web_context_by_symbol or {}
        symbol_proxy_terms_by_symbol = {
            str(sym).upper(): list(terms or [])
            for sym, terms in (symbol_proxy_terms_by_symbol or {}).items()
        }

        # ── Stage 1: entity extraction (optional) ────────────────────────────────
        stage1_result: Optional[Dict[str, Any]] = None
        keyword_generation_trace_by_symbol: Dict[str, Any] = {}
        exposure_hints_by_symbol: Dict[str, str] = {}
        stage_metrics: Dict[str, Dict[str, Any]] = {}

        stage1_model = extraction_model or model_name
        print(f"[DEBUG] stage1_model='{stage1_model}', extraction_model='{extraction_model}', model_name='{model_name}'")
        if stage1_model:
            stage1_started = time.time()
            print(f"Stage 1: starting keyword gen for {len(symbols)} symbols (model={stage1_model})...")
            stage1_result = await engine.extract_relevant_articles(
                posts,
                symbols,
                stage1_model,
                persisted_proxy_terms_by_symbol=symbol_proxy_terms_by_symbol,
            )
            print(f"Stage 1: keyword gen complete — {len(stage1_result['filtered_posts'])}/{len(posts)} articles passed filter")
            print(f"[DEBUG] stage1_result keys: {list(stage1_result.keys()) if stage1_result else 'None'}")
            print(f"[DEBUG] posts_by_symbol counts: { {sym: len(posts) for sym, posts in stage1_result.get('posts_by_symbol', {}).items()} if stage1_result else 'N/A' }")
            stage1_duration_ms = (time.time() - stage1_started) * 1000
            analysis_posts = stage1_result["filtered_posts"]
            proxy_terms_by_symbol = stage1_result["proxy_terms_by_symbol"]
            exposure_hints_by_symbol = stage1_result.get("exposure_hints_by_symbol", {})
            keyword_generation_trace_by_symbol = stage1_result.get("keyword_generation_trace_by_symbol", {}) or {}
            keyword_attribution_by_symbol: Dict[str, Any] = stage1_result.get("keyword_attribution_by_symbol", {}) or {}
            stage_metrics["stage1"] = {
                "status": "completed",
                "model_name": stage1_model,
                "duration_ms": stage1_duration_ms,
                "item_count": len(analysis_posts),
                "input_articles": len(posts),
                "matched_articles": len(analysis_posts),
                "analyzed_symbols": len(symbols),
            }
        else:
            analysis_posts = posts
            proxy_terms_by_symbol = {s: [] for s in symbols}
            keyword_generation_trace_by_symbol = {}
            keyword_attribution_by_symbol: Dict[str, Any] = {}
            stage_metrics["stage1"] = {
                "status": "skipped",
                "model_name": "",
                "duration_ms": 0.0,
                "item_count": len(posts),
                "reason": "no model available for stage 1",
                "input_articles": len(posts),
            }

        aggregated = self._build_aggregated_news_context(analysis_posts)
        if not aggregated.strip():
            raise ValueError("No post content available for sentiment analysis")
        # Do NOT set a global source_count here — each symbol gets its own
        # per-symbol article count from posts_by_symbol in _analyze_symbol.
        # A global count would make every symbol's confidence converge.

        # Per-symbol article subsets from Stage 1. Each symbol's specialist only
        # sees articles that matched its own proxy terms (chips/AI/export bans for
        # NVDA, not SPY macro headlines). Falls back to the global pool when Stage 1
        # was skipped or didn't produce a per-symbol breakdown.
        posts_by_symbol: Dict[str, List[Any]] = (
            (stage1_result or {}).get("posts_by_symbol") or {}
        )
        print(f"[DEBUG] posts_by_symbol extracted: { {sym: len(posts) for sym, posts in posts_by_symbol.items()} if posts_by_symbol else 'empty dict' }")
        effective_reasoning_model = reasoning_model or model_name
        stage2_started = time.time()
        stage2_symbol_count = len(symbols)

        async def _analyze_symbol(symbol: str, index: int) -> SentimentAnalysisResponse:
            sym_posts = posts_by_symbol.get(symbol)
            _B = "\033[94m"; _X = "\033[0m"
            print(f"Stage 2 [{index+1}/{stage2_symbol_count}]: analyzing {_B}{symbol}{_X}...")
            print(f"[DEBUG] Stage 2 {symbol}: sym_posts count={len(sym_posts) if sym_posts else 'None'}, analysis_posts count={len(analysis_posts)}")
            # Skip the LLM entirely when no articles matched this symbol —
            # saves tokens and produces a clear "no data" message instead of boilerplate.
            if sym_posts is not None and len(sym_posts) == 0:
                # No symbol-specific matches were found, but there may still be broad
                # macro or sector coverage in the shared analysis pool. Allow the
                # specialist to review the full filtered batch instead of forcing an
                # immediate 0/0/5 fallback.
                if analysis_posts:
                    sym_posts = analysis_posts
                else:
                    msg = f"No {symbol}-relevant articles in current batch — holding until news arrives."
                    return SentimentAnalysisResponse(
                        request_id="",
                        timestamp=datetime.now(timezone.utc),
                        is_bluster=False,
                        bluster_score=0.0,
                        bluster_indicators=[],
                        is_policy_change=False,
                        policy_score=0.0,
                        policy_indicators=[],
                        impact_severity="low",
                        confidence=0.05,
                        reasoning=msg,
                        directional_score=0.0,
                        signal_type="HOLD",
                        urgency="LOW",
                        entry_symbol=symbol,
                        analyst_writeup=msg,
                        parsed_payload={
                            "exposure_type": "UNRELATED",
                            "event_type": "noise",
                            "confirmed": False,
                            "source_count": 0,
                        },
                    )
            # Extract per-symbol technical indicators for confidence modulation
            tech_indicators_key = f"technical_indicators_{symbol.lower()}"
            per_symbol_tech_indicators = price_context.get(tech_indicators_key) or {}
            result = await engine.analyze(
                text=self._build_symbol_specific_news_context(
                    sym_posts if sym_posts is not None else analysis_posts,
                    symbol,
                    aggregated,
                    proxy_terms_by_symbol.get(symbol, []),
                ),
                text_source=f"aggregated_{symbol.lower()}",
                include_context=True,
                context_data={
                    **self._build_symbol_specific_price_context(price_context, symbol),
                    "source_count": len(sym_posts or []) or len(analysis_posts),
                    "technical_indicators": per_symbol_tech_indicators,
                },
                specialist_symbol=symbol,
                specialist_focus=self._symbol_specialist_focus(symbol, prompt_overrides),
                model_override=effective_reasoning_model,
                proxy_context=format_stage2_proxy_appendix(
                    symbol, proxy_terms_by_symbol.get(symbol, []), exposure_hints_by_symbol.get(symbol, "")
                ),
                web_research_context=web_context_by_symbol.get(symbol, ""),
            )
            _B = "\033[94m"; _R = "\033[91m"; _G = "\033[92m"; _Y = "\033[93m"; _X = "\033[0m"
            _sig = getattr(result, 'signal_type', '?')
            _sc = _R if _sig == "SHORT" else _G if _sig == "LONG" else _Y if _sig == "HOLD" else _X
            print(f"Stage 2 [{index+1}/{stage2_symbol_count}]: {_B}{symbol}{_X} done — signal={_sc}{_sig}{_X} score={getattr(result, 'confidence', 0.0):.2f}")
            return result

        async def _analyze_with_progress(symbol: str, index: int) -> SentimentAnalysisResponse:
            result = await _analyze_symbol(symbol, index)
            if on_symbol_complete is not None:
                try:
                    await on_symbol_complete(
                        symbol,
                        index + 1,
                        stage2_symbol_count,
                        getattr(result, 'signal_type', 'HOLD'),
                        getattr(result, 'confidence', 0.0),
                    )
                except Exception:
                    pass
            return result

        analyses = await asyncio.gather(*[_analyze_with_progress(symbol, i) for i, symbol in enumerate(symbols)])
        print(f"Stage 2: all symbols complete ({len(symbols)} symbols in {(time.time() - stage2_started)*1000:.0f}ms)")
        stage2_duration_ms = (time.time() - stage2_started) * 1000
        results: Dict[str, Dict[str, Any]] = {}

        for symbol, sentiment in zip(symbols, analyses):
            parsed_payload = getattr(sentiment, "parsed_payload", {}) or {}
            bluster_phrases = list(parsed_payload.get("bluster_phrases") or [])
            substance_phrases = list(parsed_payload.get("substance_phrases") or [])
            directional_score = self._coerce_score(
                getattr(sentiment, "directional_score", None),
                self._derive_directional_score(
                    signal_type=getattr(sentiment, "signal_type", None) or "",
                    policy_score=sentiment.policy_score,
                    bluster_score=sentiment.bluster_score,
                    raw_reasoning=sentiment.reasoning,
                ),
                -1.0,
                1.0,
            )
            results[symbol] = {
                'bluster_score': self._coerce_score(None, sentiment.bluster_score, -1.0, 1.0),
                'policy_score': self._coerce_score(None, sentiment.policy_score, 0.0, 1.0),
                'confidence': self._coerce_score(None, sentiment.confidence, 0.0, 1.0),
                'directional_score': directional_score,
                'signal_type': getattr(sentiment, "signal_type", "HOLD"),
                'urgency': getattr(sentiment, "urgency", "LOW"),
                'reasoning': (sentiment.analyst_writeup or sentiment.reasoning or '').strip(),
                'is_bluster': sentiment.is_bluster,
                'is_policy_change': sentiment.is_policy_change,
                'impact_severity': sentiment.impact_severity,
                'event_type': str(getattr(sentiment, "parsed_payload", {}).get("event_type") or ""),
                'confirmed': bool(getattr(sentiment, "parsed_payload", {}).get("confirmed", False)),
                'source_count': int(getattr(sentiment, "parsed_payload", {}).get("source_count") or 0),
                'exposure_type': str(
                    getattr(sentiment, "parsed_payload", {}).get("exposure_type") or ""
                ).upper(),
                'transmission_path': str(
                    getattr(sentiment, "parsed_payload", {}).get("transmission_path") or ""
                ),
                'bluster_phrases': bluster_phrases,
                'substance_phrases': substance_phrases,
            }

        stage2_runs_by_symbol: Dict[str, Dict[str, Any]] = {}
        for symbol, sentiment in zip(symbols, analyses):
            parsed_payload = getattr(sentiment, "parsed_payload", {}) or {}
            stage2_runs_by_symbol[symbol] = {
                "model": effective_reasoning_model or "",
                "prompt": getattr(sentiment, "prompt_used", "") or "",
                "raw_response": getattr(sentiment, "raw_model_response", "") or "",
                "parsed_payload": parsed_payload,
                "bluster_phrases": list(parsed_payload.get("bluster_phrases") or []),
                "substance_phrases": list(parsed_payload.get("substance_phrases") or []),
                "final_reasoning": results[symbol]["reasoning"],
                "signal_type": results[symbol]["signal_type"],
                "confidence": results[symbol]["confidence"],
            }

        trace = {
            "used_two_stage": bool(stage1_model and effective_reasoning_model),
            "pipeline_models": {
                "analysis_model": model_name or "",
                "extraction_model": stage1_model or "",
                "reasoning_model": effective_reasoning_model or "",
            },
            "stage_metrics": {
                **stage_metrics,
                "stage2": {
                    "status": "completed",
                    "model_name": effective_reasoning_model or "",
                    "duration_ms": stage2_duration_ms,
                    "item_count": len(symbols),
                    "input_articles": len(analysis_posts),
                    "analyzed_symbols": len(symbols),
                    "details": {
                        "posts_by_symbol_counts": {
                            sym: len(posts_by_symbol.get(sym) or []) for sym in symbols
                        },
                        "exposure_hints_by_symbol": dict(exposure_hints_by_symbol),
                        "keyword_terms_by_symbol": {
                            sym: list((kw.get("terms") or [])[:10])
                            for sym, kw in keyword_generation_trace_by_symbol.items()
                        },
                    },
                },
            },
            "aggregated_news_length": len(aggregated),
            "analysis_article_count": len(analysis_posts),
            "stage1": {
                **self._build_stage1_trace(posts, analysis_posts, proxy_terms_by_symbol),
                "keyword_generation_trace_by_symbol": keyword_generation_trace_by_symbol,
                "keyword_attribution_by_symbol": keyword_attribution_by_symbol,
            },
            "stage2_runs_by_symbol": stage2_runs_by_symbol,
        }
        print(f"[DEBUG] FINAL trace posts_by_symbol_counts: {trace.get('stage_metrics', {}).get('stage2', {}).get('details', {}).get('posts_by_symbol_counts', {})}")

        return results, trace

    # ── Context Builders (private) ─────────────────────────────────────

    def _build_aggregated_news_context(self, posts: List[Any]) -> str:
        aggregated_sections: List[str] = []
        for post in posts:
            source = (
                getattr(post, 'source', None)
                or getattr(post, 'feed_name', None)
                or getattr(post, 'author', None)
                or "Unknown Source"
            )
            title = (getattr(post, 'title', '') or '').strip()
            summary = (getattr(post, 'summary', '') or '').strip()
            content = (getattr(post, 'content', '') or '').strip()
            details = content or summary
            section_lines: List[str] = []
            if title:
                section_lines.append(f"Source: {source}")
                section_lines.append(f"Headline: {title}")
            elif details:
                section_lines.append(f"Source: {source}")
            if details and details != title:
                section_lines.append(f"Details: {details}")
            if section_lines:
                aggregated_sections.append("\n".join(section_lines))
        return "\n\n".join(aggregated_sections)[:12000]

    def _build_symbol_specific_news_context(
        self,
        posts: List[Any],
        symbol: str,
        fallback: str,
        proxy_terms: Optional[List[str]] = None,
    ) -> str:
        # If posts are provided (from Stage 1 per-symbol filtering), use them directly
        # without re-filtering. Re-filtering is redundant and can fail due to text
        # normalization mismatches between Stage 1 and Stage 2.
        if posts and len(posts) > 0:
            # But still check if any post matches proxy terms - if not, warn the LLM
            raw_terms = list(proxy_terms or []) or SYMBOL_RELEVANCE_TERMS.get(symbol.upper(), [])
            terms = expand_proxy_terms_for_matching(raw_terms)
            
            if terms:
                # Check if any provided post actually matches the proxy terms
                has_match = False
                for post in posts:
                    text_blob = normalize_text_for_matching(" ".join([
                        str(getattr(post, "title", "") or ""),
                        str(getattr(post, "summary", "") or ""),
                        str(getattr(post, "content", "") or ""),
                        " ".join(getattr(post, "keywords", None) or []),
                    ]))
                    if any(term in text_blob for term in terms):
                        has_match = True
                        break
                
                if not has_match:
                    # No posts match proxy terms - warn the LLM
                    brief_titles = [
                        str(getattr(post, "title", "") or "").strip()
                        for post in posts[:3]
                        if str(getattr(post, "title", "") or "").strip()
                    ]
                    spillover = "\n".join(f"- {title}" for title in brief_titles)
                    return (
                        f"No article in this batch matched {symbol} proxy terms: "
                        f"{", ".join(raw_terms[:12]) or symbol}.\n"
                        f"Do NOT assume DIRECT exposure for {symbol}. "
                        f"Use BROAD or UNRELATED unless the text shows a specific causal chain.\n"
                        f"Shared macro headlines:\n{spillover}"
                    ).strip()

            relevant_context = self._build_aggregated_news_context(posts)
            if relevant_context:
                return relevant_context
        
        # Fallback: try filtering with proxy terms
        raw_terms = list(proxy_terms or []) or SYMBOL_RELEVANCE_TERMS.get(symbol.upper(), [])
        terms = expand_proxy_terms_for_matching(raw_terms)
        if not terms:
            brief_titles = [
                str(getattr(post, "title", "") or "").strip()
                for post in posts[:3]
                if str(getattr(post, "title", "") or "").strip()
            ]
            spillover = "\n".join(f"- {title}" for title in brief_titles)
            return (
                f"No symbol-specific matches were found for {symbol}. "
                f"Do NOT assume DIRECT exposure.\n"
                f"Treat {symbol} as BROAD or UNRELATED unless the text explicitly names "
                f"the company, asset, sector, or a clear transmission path.\n"
                f"Shared macro headlines:\n{spillover}"
            ).strip()

        relevant_posts: List[Any] = []
        for post in posts:
            text_blob = normalize_text_for_matching(" ".join([
                str(getattr(post, "title", "") or ""),
                str(getattr(post, "summary", "") or ""),
                str(getattr(post, "content", "") or ""),
                " ".join(getattr(post, "keywords", None) or []),
            ]))
            if any(term in text_blob for term in terms):
                relevant_posts.append(post)

        relevant_context = self._build_aggregated_news_context(relevant_posts)
        if relevant_context:
            return relevant_context

        brief_titles = [
            str(getattr(post, "title", "") or "").strip()
            for post in posts[:3]
            if str(getattr(post, "title", "") or "").strip()
        ]
        spillover = "\n".join(f"- {title}" for title in brief_titles)
        return (
            f"No article in this batch matched {symbol} proxy terms: "
            f"{', '.join(raw_terms[:12]) or symbol}.\n"
            f"Do NOT assume DIRECT exposure for {symbol}. "
            f"Use BROAD or UNRELATED unless the text shows a specific causal chain.\n"
            f"Shared macro headlines:\n{spillover}"
        ).strip()

    def _build_symbol_specific_price_context(self, price_context: Dict[str, Any], symbol: str) -> Dict[str, Any]:
        context = dict(price_context)
        context["active_symbol"] = symbol
        context["active_symbol_price"] = context.get(f"{symbol.lower()}_price", 0.0)
        market_validation = context.get("market_validation") or {}
        symbol_payload = market_validation.get(symbol, {})
        if symbol_payload:
            summary = str(symbol_payload.get("summary", "") or "").strip()
            status = str(symbol_payload.get("status", "unavailable")).upper()
            context["validation_context"] = f"{symbol} [{status}]: {summary}" if summary else ""
        tech_key = f"technical_context_{symbol.lower()}"
        if tech_key in price_context:
            context["technical_context"] = price_context[tech_key]
        return context

    def _coerce_score(self, value: Any, default: float, lower: float, upper: float) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = float(default)
        return max(lower, min(upper, numeric))

    def _derive_directional_score(
        self,
        signal_type: str,
        policy_score: float,
        bluster_score: float,
        raw_reasoning: str,
    ) -> float:
        normalized_signal = (signal_type or "").upper().strip()
        if normalized_signal == "LONG":
            return min(1.0, max(0.15, policy_score))
        if normalized_signal == "SHORT":
            return max(-1.0, min(-0.15, -max(abs(bluster_score), policy_score)))

        reasoning = (raw_reasoning or "").lower()
        positive_hints = ["bullish", "beneficiary", "re-rate higher", "rally", "positive for"]
        negative_hints = ["bearish", "headwind", "sell-off", "negative for", "pressure on"]
        if any(token in reasoning for token in positive_hints):
            return min(1.0, max(0.1, policy_score * 0.8))
        if any(token in reasoning for token in negative_hints):
            return max(-1.0, min(-0.1, -max(abs(bluster_score), policy_score * 0.8)))
        return 0.0

    def _symbol_specialist_focus(self, symbol: str, prompt_overrides: Optional[Dict[str, str]] = None) -> str:
        base_focus = self._get_symbol_specialist_focus_with_overrides(symbol, prompt_overrides)
        return base_focus

    def _get_symbol_specialist_focus_with_overrides(
        self, symbol: str, prompt_overrides: Optional[Dict[str, str]] = None
    ) -> str:
        base_focus = self._get_base_specialist_focus(symbol)
        override = ((prompt_overrides or {}).get(symbol) or "").strip()
        if override:
            return f"{base_focus}\n\nAdditional admin guidance for {symbol}:\n{override}"
        return base_focus

    def _get_base_specialist_focus(self, symbol: str) -> str:
        """Get the base specialist focus for a symbol. Falls back to generic if not configured."""
        # This would normally call get_symbol_specialist_focus from sentiment.prompts
        # For now, return a placeholder that the real implementation would override
        from services.sentiment.prompts import get_symbol_specialist_focus
        return get_symbol_specialist_focus(symbol)

    def _build_stage1_trace(
        self,
        posts: List[Any],
        filtered_posts: List[Any],
        proxy_terms_by_symbol: Dict[str, List[str]],
    ) -> Dict[str, Any]:
        filtered_ids = {id(post) for post in filtered_posts}
        article_rows: List[Dict[str, Any]] = []
        matched_count = 0

        for post in posts:
            blob = (
                f"{getattr(post, 'title', '') or ''} "
                f"{getattr(post, 'summary', '') or ''} "
                f"{getattr(post, 'content', '') or ''} "
                f"{' '.join(getattr(post, 'keywords', None) or [])}"
            ).lower()
            matched_terms_by_symbol: Dict[str, List[str]] = {}
            matched_symbols: List[str] = []
            for sym, terms in (proxy_terms_by_symbol or {}).items():
                matched_terms = [term for term in terms if term and term.lower() in blob]
                if matched_terms:
                    matched_symbols.append(sym)
                    matched_terms_by_symbol[sym] = matched_terms[:8]
            if matched_symbols:
                matched_count += 1
            article_rows.append({
                **self._post_trace_summary(post),
                "selected_for_reasoning": id(post) in filtered_ids,
                "matched_symbols": matched_symbols,
                "matched_terms_by_symbol": matched_terms_by_symbol,
            })

        return {
            "proxy_terms_by_symbol": proxy_terms_by_symbol,
            "matched_article_count": matched_count,
            "filtered_article_count": len(filtered_posts),
            "used_keyword_matches": bool(matched_count),
            "articles": article_rows,
        }

    def _post_trace_summary(self, post: Any) -> Dict[str, Any]:
        return {
            "source": getattr(post, "source", None) or getattr(post, "feed_name", None) or getattr(post, "author", None) or "Unknown",
            "title": getattr(post, "title", "") or "",
            "summary": getattr(post, "summary", "") or "",
            "content": getattr(post, "content", "") or "",
            "keywords": list(getattr(post, "keywords", None) or []),
        }

    async def get_symbol_web_research(
        self,
        symbols: List[str],
        enabled: bool,
        max_items_per_symbol: int,
        max_age_days: int,
        symbol_company_aliases: Optional[Dict[str, str]] = None,
    ) -> tuple[Dict[str, str], Dict[str, List[Dict[str, str]]]]:
        if not enabled:
            import logging
            logger = logging.getLogger(__name__)
            logger.info("Web research skipped — web_research_enabled is False in app config")
            return {}, {}

        results = await asyncio.gather(*[
            asyncio.to_thread(
                fetch_recent_symbol_web_context,
                symbol,
                company_alias=str(symbol_company_aliases.get(symbol, "") or "").strip(),
                max_items=max_items_per_symbol,
                max_age_days=max_age_days,
            )
            for symbol in symbols
        ], return_exceptions=True)

        web_context_by_symbol: Dict[str, str] = {}
        web_items_by_symbol: Dict[str, List[Dict[str, str]]] = {}
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                web_context_by_symbol[symbol] = ""
                web_items_by_symbol[symbol] = []
                continue
            web_context_by_symbol[symbol] = str(result.get("summary", "") or "").strip()
            web_items_by_symbol[symbol] = list(result.get("items") or [])

        return web_context_by_symbol, web_items_by_symbol

    def build_model_input_debug(
        self,
        posts: List[Any],
        price_context: Dict[str, Any],
        market_validation: Dict[str, Dict[str, Any]],
        symbols: Optional[List[str]] = None,
        prompt_overrides: Optional[Dict[str, str]] = None,
        web_context_by_symbol: Optional[Dict[str, str]] = None,
        web_items_by_symbol: Optional[Dict[str, List[Dict[str, str]]]] = None,
    ) -> ModelInputDebug:
        validation_context = str(price_context.get("validation_context", "") or "")
        visible_price_context = {
            key: value
            for key, value in price_context.items()
            if key.endswith("_price")
        }
        articles: List[ModelInputArticle] = []
        for post in posts:
            source = (
                getattr(post, 'source', None)
                or getattr(post, 'feed_name', None)
                or getattr(post, 'author', None)
                or "Unknown Source"
            )
            title = (getattr(post, 'title', '') or '').strip()
            description = (getattr(post, 'content', '') or '').strip()
            keywords = [
                str(keyword).strip()
                for keyword in (getattr(post, 'keywords', None) or [])
                if str(keyword).strip()
            ]
            if not title and not description:
                continue
            articles.append(ModelInputArticle(
                source=str(source),
                title=title,
                description="" if description == title else description,
                content=description,
                keywords=keywords[:8],
            ))

        return ModelInputDebug(
            news_context=self._build_aggregated_news_context(posts),
            validation_context=validation_context,
            price_context=visible_price_context,
            articles=articles,
            per_symbol_prompts=self._build_per_symbol_prompts(
                posts, price_context, symbols or [], prompt_overrides,
                web_context_by_symbol=web_context_by_symbol,
            ),
            web_context_by_symbol=web_context_by_symbol or {},
            web_items_by_symbol=web_items_by_symbol or {},
        )

    def _build_per_symbol_prompts(
        self,
        posts: List[Any],
        price_context: Dict[str, Any],
        symbols: List[str],
        prompt_overrides: Optional[Dict[str, str]] = None,
        web_context_by_symbol: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        aggregated = self._build_aggregated_news_context(posts)
        if not aggregated.strip():
            return {}

        prompts: Dict[str, str] = {}
        web_context_by_symbol = web_context_by_symbol or {}
        for symbol in symbols:
            symbol_context = self._build_symbol_specific_price_context(price_context, symbol)
            symbol_text = self._build_symbol_specific_news_context(posts, symbol, aggregated)
            validation_ctx = str(symbol_context.get("validation_context", "") or "")
            technical_ctx = str(symbol_context.get("technical_context", "") or "")
            combined_validation = "\n\n".join(filter(None, [validation_ctx, technical_ctx]))
            prompts[symbol] = format_symbol_specialist_context_prompt(
                symbol=symbol,
                specialist_focus=self._symbol_specialist_focus(symbol, prompt_overrides),
                text=symbol_text,
                date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                active_symbol=str(symbol_context.get("active_symbol", symbol)),
                active_symbol_price=float(symbol_context.get("active_symbol_price", 0.0) or 0.0),
                uso_price=float(symbol_context.get("uso_price", 0.0) or 0.0),
                bito_price=float(symbol_context.get("bito_price", 0.0) or 0.0),
                qqq_price=float(symbol_context.get("qqq_price", 0.0) or 0.0),
                spy_price=float(symbol_context.get("spy_price", 0.0) or 0.0),
                recent_sentiment=str(symbol_context.get("recent_sentiment", "") or ""),
                validation_context=combined_validation,
                web_research_context=web_context_by_symbol.get(symbol, ""),
            )
        return prompts
