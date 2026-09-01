"""
StreamService — SSE (Server-Sent Events) streaming layer.

Encapsulates the AsyncGenerator pattern from the original analyze_market_stream().
CRITICAL: The Stage 2 heartbeat (yield ": stage2-heartbeat\n\n") is preserved
to prevent proxy timeouts in Undici/Next.js 5-min limits.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import AsyncGenerator, Any, Dict, List, Optional

from fastapi import HTTPException

from config.logic_loader import LOGIC
from schemas.analysis import AnalysisResponse
from services.analysis.pipeline_service import PipelineService
from services.analysis.sentiment_service import SentimentService
from services.analysis.market_data_service import MarketDataService
from services.analysis.signal_service import SignalService
from services.analysis.materiality_service import MaterialityService
from services.analysis.hysteresis_service import HysteresisService
from services.analysis.persistence_service import PersistenceService
from services.analysis.backtest_service import BacktestService
from services.regime import market_regime_from_price_context
from services.analysis.cache_service import PriceCacheService
from services.symbol_proxy_terms import ensure_symbol_proxy_terms_fresh
from services.risk_policy_runtime import build_crazy_ramp_context


class StreamService:
    """Encapsulates SSE streaming and stage heartbeat logic."""

    def __init__(
        self,
        price_cache: PriceCacheService,
        sentiment_service: SentimentService,
        market_data_service: MarketDataService,
        signal_service: SignalService,
        materiality_service: MaterialityService,
        hysteresis_service: HysteresisService,
        persistence_service: PersistenceService,
        backtest_service: BacktestService,
        logic_config: dict[str, Any],
    ) -> None:
        self._price_cache = price_cache
        self._sentiment = sentiment_service
        self._market = market_data_service
        self._signal = signal_service
        self._materiality = materiality_service
        self._hysteresis = hysteresis_service
        self._persistence = persistence_service
        self._backtest = backtest_service
        self._L = logic_config

    # ── Public API ───────────────────────────────────────────────────

    async def generate(
        self,
        pipeline: PipelineService,
        request: Any,
        db: Any,
        config: Any,
        prompt_overrides: Optional[Dict[str, str]] = None,
        analysis_lock: Any = None,
    ) -> AsyncGenerator[str, None]:
        """SSE stream generator — mirrors the original analyze_market_stream() flow."""
        start_time = time.time()
        request_id = pipeline.request_id
        symbols = pipeline.symbols
        max_posts = int(getattr(request, 'max_posts', 50) or getattr(config, 'max_posts', 50) or 50)
        analysis_id = pipeline.analysis_id

        yield f": {json.dumps({'type': 'init', 'request_id': request_id, 'symbols': symbols, 'max_posts': max_posts})}\n\n"

        # Stage 1: Data ingestion
        _stage1_start = time.time()
        yield f": {json.dumps({'type': 'stage-start', 'stage': 1})}\n\n"
        try:
            posts, ingestion_trace = await self._market.ingest_data(
                db, request, config, prompt_overrides
            )
        except Exception as e:
            yield f": {json.dumps({'type': 'error', 'stage': 1, 'detail': str(e)})}\n\n"
            return
        stage1_ms = (time.time() - _stage1_start) * 1000
        yield self._sse_result("stage1", "completed", ingestion_trace)

        if not posts:
            yield self._sse_result("stage1", "skipped", {"reason": "no_posts_found"})
            yield self._sse_error("no_posts", "No usable articles found in the queue")
            return

        # Market snapshot
        _snapshot_start = time.time()
        yield f": {json.dumps({'type': 'stage-start', 'stage': 1.5})}\n\n"
        try:
            price_context, quotes_by_symbol, market_validation = await self._market.get_market_snapshot(symbols)
        except Exception as e:
            yield f": {json.dumps({'type': 'error', 'stage': 1.5, 'detail': str(e)})}\n\n"
            return
        stage2_ms = (time.time() - _snapshot_start) * 1000
        yield self._sse_result("market_snapshot", "completed", {
            "status": "ok",
            "quotes_fetched": len(quotes_by_symbol),
            "price_context": {k: v for k, v in price_context.items() if k.endswith("_price")},
            "validation_status": {sym: payload.get("status") for sym, payload in (market_validation or {}).items()},
        })

        # Technical context
        try:
            price_context = self._market.inject_technical_context(price_context, symbols, db)
            yield self._sse_result("technical_context", "completed", {
                "status": "ok",
                "symbols_processed": len(symbols),
            })
        except Exception as e:
            yield f": {json.dumps({'type': 'error', 'stage': 'tech_ctx', 'detail': str(e)})}\n\n"
            yield self._sse_result("technical_context", "partial", {"detail": str(e)})

        # Web research
        _web_research_start = time.time()
        web_research_enabled = bool(getattr(config, 'web_research_enabled', None))
        web_research_max_items = int(getattr(config, 'web_research_max_items', 10) or 10)
        web_research_max_age_days = int(getattr(config, 'web_research_max_age_days', 5) or 5)
        symbol_company_aliases = dict(getattr(config, 'symbol_company_aliases', {}) or {})

        web_context_by_symbol, web_items_by_symbol = {}, {}
        if web_research_enabled:
            yield f": {json.dumps({'type': 'stage-start', 'stage': 1.75})}\n\n"
            try:
                web_context_by_symbol, web_items_by_symbol = await self._sentiment.get_symbol_web_research(
                    symbols=symbols,
                    enabled=web_research_enabled,
                    max_items_per_symbol=web_research_max_items,
                    max_age_days=web_research_max_age_days,
                    symbol_company_aliases=symbol_company_aliases,
                )
            except Exception as e:
                yield f": {json.dumps({'type': 'error', 'stage': 'web_research', 'detail': str(e)})}\n\n"
            yield self._sse_result("web_research", "completed", {
                "status": "ok",
                "symbols_processed": list(web_context_by_symbol.keys()),
                "context_lengths": {s: len(ctx) for s, ctx in web_context_by_symbol.items()},
            })

        # Stage 2: Sentiment analysis
        yield f": {json.dumps({'type': 'stage-start', 'stage': 2})}\n\n"
        extraction_model = getattr(config, "extraction_model", None)
        reasoning_model = getattr(config, "reasoning_model", None)

        # Hysteresis check
        stability_mode = "normal"
        previous_response = None
        entry_threshold_override = None
        signal_age_hours = 0.0
        if self._hysteresis.is_closed_market_session(quotes_by_symbol):
            stability_mode = "closed_market_hysteresis"
            _prev_state = self._hysteresis.latest_previous_analysis_state(db)
            previous_response = (_prev_state or {}).get("response")
            entry_threshold_override = self._L["entry_thresholds"].get("closed_market", 0.25)
            _prev_ts = getattr((_prev_state or {}).get("analysis"), "timestamp", None)
            if _prev_ts is not None:
                if _prev_ts.tzinfo is None:
                    _prev_ts = _prev_ts.replace(tzinfo=timezone.utc)
                signal_age_hours = (datetime.now(timezone.utc) - _prev_ts).total_seconds() / 3600.0

        sentiment_results = None
        sentiment_trace = None
        _sentiment_start = time.time()
        # Ensure symbol proxy terms are fresh (30-day TTL check)
        extraction_model_for_terms = extraction_model or pipeline.model_name
        symbol_proxy_terms_by_symbol = await ensure_symbol_proxy_terms_fresh(
            db=db,
            config=config,
            symbols=symbols,
            model_name=extraction_model_for_terms,
        )
        sentiment_task = asyncio.create_task(
            self._sentiment.analyze_sentiment(
                posts=posts,
                symbols=symbols,
                price_context=price_context,
                prompt_overrides=prompt_overrides,
                model_name=pipeline.model_name,
                extraction_model=extraction_model,
                reasoning_model=reasoning_model,
                web_context_by_symbol=web_context_by_symbol,
                symbol_proxy_terms_by_symbol=symbol_proxy_terms_by_symbol,
                openai_base_url=getattr(config, "openai_base_url", None),
                openai_model=getattr(config, "openai_model", None),
            )
        )
        while True:
            done, pending = await asyncio.wait(
                [sentiment_task],
                timeout=float(self._L.get("stage2_timeout", 20)),
            )
            if sentiment_task in done:
                break
            # Stage 2 heartbeat to prevent proxy timeouts (Undici/Next.js 5-min limits)
            yield ": stage2-heartbeat\n\n"
            pending.clear()

        sentiment_results, sentiment_trace = sentiment_task.result()
        stage2_ms = (time.time() - _sentiment_start) * 1000
        yield self._sse_result("sentiment", "completed", {
            "status": "ok",
            "duration_ms": stage2_ms,
            "symbols_analyzed": len(sentiment_results),
            "pipeline_trace": sentiment_trace,
        })

        # Trading signal
        yield f": {json.dumps({'type': 'stage-start', 'stage': 3})}\n\n"
        _signal_start = time.time()
        candidate_signal = self._signal.generate_trading_signal(
            sentiment_results=sentiment_results,
            quotes_by_symbol=quotes_by_symbol,
            risk_profile=getattr(config, 'risk_profile', 'moderate'),
            previous_signal=None,
            stability_mode=stability_mode,
            entry_threshold_override=entry_threshold_override,
            price_context=price_context,
            signal_age_hours=signal_age_hours,
            crazy_ramp_context=await build_crazy_ramp_context(
                symbols=symbols,
                risk_profile=getattr(config, "risk_profile", "moderate"),
                risk_policy=dict(getattr(config, "risk_policy", {}) or {}),
                price_context=price_context,
            ),
        )
        per_symbol_counts = self._materiality._count_symbol_articles(
            posts, list(sentiment_results.keys()),
            relevance_terms={sym.upper(): [] for sym in symbols},
        )

        # Ensure materiality
        is_material = self._materiality.material_change_gate(
            db=db,
            symbols=list(sentiment_results.keys()),
            posts_count=len(posts),
            sentiment_results=sentiment_results,
            price_context=price_context,
            quotes_by_symbol=quotes_by_symbol,
            previous_state={"response": previous_response} if previous_response else None,
            candidate_signal=candidate_signal,
            min_posts_delta=None,
            min_sentiment_delta=None,
            per_symbol_counts=per_symbol_counts,
        )
        if not is_material:
            yield self._sse_result("materiality_gate", "blocked", {
                "status": "blocked",
                "reason": "input_did_not_meet_materiality_thresholds",
                "thresholds": {
                    "posts_delta": None,
                    "sentiment_delta": None,
                    "price_move_pct": None,
                },
            })
            # Yield final heartbeat before closing
            yield ": stage2-heartbeat\n\n"
            return

        # Red-team review
        red_team_review = None
        red_team_debug = None
        red_team_enabled = bool(getattr(config, 'red_team_enabled', True))
        if red_team_enabled:
            print(f"[red-team] stage starting — {len(symbols)} symbol(s), model={pipeline.model_name}")
            yield f": {json.dumps({'type': 'stage-start', 'stage': 4})}\n\n"
            try:
                context = self._signal.build_red_team_context(
                    symbols=symbols,
                    posts=posts,
                    sentiment_results=sentiment_results,
                    trading_signal=candidate_signal,
                    price_context=price_context,
                    quotes_by_symbol=quotes_by_symbol,
                    market_validation=market_validation or {},
                )
                red_team_context = {"raw_context": json.dumps(context, ensure_ascii=True, default=str, indent=2)}
                red_team_review, red_team_debug = self._signal.run_red_team_review(
                    model_name=pipeline.model_name,
                    context=red_team_context,
                )
                if red_team_review:
                    signal_changes = self._signal.build_red_team_signal_changes(
                        candidate_signal, candidate_signal, red_team_review
                    )
                    red_team_review.signal_changes = signal_changes
            except Exception as e:
                print(f"[red-team] stage FAILED: {e}")
                yield f": {json.dumps({'type': 'error', 'stage': 'red_team', 'detail': str(e)})}\n\n"
            yield self._sse_result("red_team", "completed", {
                "status": "ok" if red_team_review else "not_applied",
            })
        else:
            print("[red-team] stage skipped — red_team_enabled=False in config")

        # Backtest
        yield f": {json.dumps({'type': 'stage-start', 'stage': 5})}\n\n"
        try:
            backtest_results = await self._backtest.run_backtest(
                symbols=list(sentiment_results.keys()),
                sentiment_results=sentiment_results,
                risk_profile=getattr(config, "risk_profile", "moderate"),
            )
        except Exception as e:
            backtest_results = {
                "total_return": 0.0, "annualized_return": 0.0, "sharpe_ratio": 0.0,
                "max_drawdown": 0.0, "win_rate": 0.0, "total_trades": 0,
                "lookback_days": 14, "walk_forward_steps": 0,
            }
            yield f": {json.dumps({'type': 'error', 'stage': 'backtest', 'detail': str(e)})}\n\n"
        yield self._sse_result("backtest", "completed", {"status": "ok"})

        # Consensus signal
        consensus_signal = self._signal.build_consensus_trading_signal(
            blue_team_signal=candidate_signal,
            red_team_review=red_team_review,
            quotes_by_symbol=quotes_by_symbol,
            risk_profile=getattr(config, 'risk_profile', 'moderate'),
            regime=market_regime_from_price_context(price_context),
        )

        # Serialize result
        _serialize_start = time.time()
        response = AnalysisResponse(
            request_id=request_id,
            status="SUCCESS",
            timestamp=pipeline.timestamp,
            symbols_analyzed=list(sentiment_results.keys()),
            posts_scraped=len(posts),
            sentiment_scores={
                symbol: self._coerce_to_json_compatible(result)
                for symbol, result in sentiment_results.items()
            },
            aggregated_sentiment=None,
            trading_signal=consensus_signal,
            blue_team_signal=candidate_signal,
            market_validation=market_validation or {},
            stage_metrics={
                "stage1": {"status": "completed", "duration_ms": stage1_ms, "item_count": len(posts), "input_articles": len(posts)},
                "stage2": {"status": "completed", "duration_ms": stage2_ms, "item_count": len(sentiment_results), "input_articles": len(posts)},
                "materiality": {"status": "passed"},
                "red_team": {"status": "completed", "model_name": getattr(config, 'red_team_model', '') or pipeline.model_name},
                "backtest": {"status": "ok"},
                "serialize": {"status": "ok", "duration_ms": (time.time() - _serialize_start) * 1000},
            },
            backtest_results=backtest_results,
            processing_time_ms=(time.time() - start_time) * 1000,
            request_payload=getattr(request, 'model_dump', lambda: {})(
            ) if hasattr(request, 'model_dump') else getattr(request, '__dict__', {}),
        )
        if red_team_debug:
            red_team_debug.signal_changes = self._signal.build_red_team_signal_changes(
                candidate_signal, consensus_signal, red_team_review
            ) if red_team_review else []

        # Save
        try:
            self._persistence.save_analysis_result(
                db=db,
                request_id=request_id,
                response=response,
                quotes_by_symbol=quotes_by_symbol,
                posts=posts,
                model_name=pipeline.model_name,
                prompt_overrides=prompt_overrides,
                extraction_model=extraction_model or "",
                reasoning_model=reasoning_model or "",
                risk_profile=getattr(config, 'risk_profile', 'moderate'),
                price_context=price_context,
            )
            yield self._sse_result("save", "ok", {"request_id": request_id, "analysis_id": analysis_id})
        except Exception as save_err:
            yield f": {json.dumps({'type': 'error', 'stage': 'save', 'detail': str(save_err)})}\n\n"

        # Final heartbeat before closing
        yield ": stage2-heartbeat\n\n"

    # ── Helpers (private) ───────────────────────────────────────────────

    def _sse_result(self, stage: str, status: str, details: Dict[str, Any]) -> str:
        return f": {json.dumps({'type': 'result', 'stage': stage, 'status': status, **details})}\n\n"

    def _sse_error(self, code: str, detail: str) -> str:
        return f": {json.dumps({'type': 'error', 'code': code, 'detail': detail})}\n\n"

    def _coerce_to_json_compatible(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: str(value) if isinstance(value, (set, frozenset)) else value
            for key, value in result.items()
        }
