"""
PipelineService — orchestration layer for the analysis pipeline.

Encapsulates _run_analysis_pipeline from the original router.  This is the
main entry point that the refactored router calls.  All DI is explicit:
Session, PriceCacheService, and config are passed to the constructor.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import aclosing
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from config.market_constants import SYMBOL_RELEVANCE_TERMS
from schemas.analysis import AnalysisRequest, AnalysisResponse, SentimentScore, TradingSignal, BacktestResults
from services.analysis.cache_service import PriceCacheService
from services.analysis.sentiment_service import SentimentService
from services.analysis.signal_service import SignalService
from services.analysis.market_data_service import MarketDataService
from services.analysis.materiality_service import MaterialityService
from services.analysis.hysteresis_service import HysteresisService
from services.analysis.persistence_service import PersistenceService
from services.analysis.backtest_service import BacktestService
from services.risk_policy_runtime import build_crazy_ramp_context
from database.models import ScrapedArticle

# Analysis lock lease duration (seconds). 10 minutes = 600 seconds.
# This is the maximum time a single pipeline run is allowed to hold the lock.
# If a run crashes or the process is killed, the lock auto-expires after this
# duration, allowing the next run to proceed.
ANALYSIS_LOCK_LEASE_SECONDS = 600

# How often (in seconds) to refresh the lock lease while a long-running
# pipeline is still active.  Refresh at half the lease duration so there is
# ample time for retries if a DB write fails temporarily.
ANALYSIS_LOCK_REFRESH_INTERVAL_SECONDS = 240


class PipelineService:
    """Orchestrates the full analysis pipeline: ingest → snapshot → sentiment → signal → persist."""

    def __init__(
        self,
        db: Session,
        price_cache: PriceCacheService,
        logic_config: dict[str, Any],
        continuous_entry_enabled: Optional[bool] = None,
        regime_adaptation_enabled: Optional[bool] = None,
        hold_decay_enabled: Optional[bool] = None,
    ) -> None:
        self._db = db
        self._price_cache = price_cache
        self._L = logic_config

        # Compose child services (dependency tree: unidirectional)
        self._sentiment = SentimentService(price_cache=price_cache, logic_config=logic_config)
        self._market = MarketDataService(price_cache=price_cache, logic_config=logic_config)
        self._signal = SignalService(
            logic_config=logic_config,
            continuous_entry_enabled=continuous_entry_enabled,
            regime_adaptation_enabled=regime_adaptation_enabled,
            hold_decay_enabled=hold_decay_enabled,
        )
        self._materiality = MaterialityService(logic_config=logic_config)
        self._hysteresis = HysteresisService(logic_config=logic_config)
        self._persistence = PersistenceService(logic_config=logic_config)
        self._backtest = BacktestService(logic_config=logic_config)

        # Pipeline state
        self.request_id: str = ""
        self.symbols: List[str] = []
        self.model_name: str = ""
        self.timestamp: str = ""
        self.analysis_id: str = ""

    # ── Public API ───────────────────────────────────────────────────

    async def run(
        self,
        request: AnalysisRequest,
        db: Session,
        config: Any,
        prompt_overrides: Optional[Dict[str, str]] = None,
        article_ids: Optional[List[int]] = None,
        trigger_source: str = "api",
    ) -> AnalysisResponse:
        """
        Run the full analysis pipeline synchronously (for non-streaming callers).
        This delegates to the async generator but collects all output.
        """
        response = None
        terminal_error: Optional[str] = None
        async with aclosing(
            self.run_stream(
                request,
                db,
                config,
                prompt_overrides,
                article_ids=article_ids,
                trigger_source=trigger_source,
            )
        ) as stream:
            async for chunk in stream:
                # Parse the SSE result to extract the final response
                if isinstance(chunk, dict) and chunk.get("type") == "final_response":
                    response = chunk.get("response")
                elif isinstance(chunk, dict) and chunk.get("type") == "error":
                    terminal_error = str(chunk.get("detail") or chunk.get("message") or "Pipeline error")
                    break
                elif isinstance(chunk, dict) and chunk.get("type") == "materiality_blocked":
                    terminal_error = str(chunk.get("reason") or "Materiality gate blocked analysis")
                    break
        if terminal_error:
            raise HTTPException(status_code=400, detail=terminal_error)
        if response is None:
            raise HTTPException(status_code=500, detail="Pipeline failed to produce a response")
        return response  # type: ignore[return-value]

    async def run_stream(
        self,
        request: AnalysisRequest,
        db: Session,
        config: Any,
        prompt_overrides: Optional[Dict[str, str]] = None,
        article_ids: Optional[List[int]] = None,
        trigger_source: str = "api",
    ) -> AsyncGenerator[Any, None]:
        """Run the full analysis pipeline as an async stream, yielding SSE events."""
        started_at = time.time()
        # ── Request setup ───────────────────────────────────────────────
        self.request_id = str(uuid.uuid4())
        self.symbols = list({s.upper() for s in (request.symbols or [])})
        if not self.symbols:
            self.symbols = self._get_default_symbols()
        self.model_name = self._resolve_active_model_name(config)
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self._run_started = time.time()

        # ── Decision Log: run start ──────────────────────────────────────
        try:
            from database.engine import DecisionLogSessionLocal
            from services.decision_logger import logger as dl
            ddb = DecisionLogSessionLocal()
            try:
                dl.log_run_start(
                    ddb,
                    run_id=self.request_id,
                    trigger_source=trigger_source,
                    extraction_model=str(getattr(config, "extraction_model", "") or "").strip() or None,
                    reasoning_model=str(getattr(config, "reasoning_model", "") or "").strip() or None,
                    config_snapshot={
                        "entry_threshold": getattr(config, "entry_threshold", None),
                        "inference_backend": getattr(config, "inference_backend", "ollama"),
                        "risk_profile": getattr(config, "risk_profile", "moderate"),
                    },
                )
                ddb.commit()
            except Exception as dl_exc:
                ddb.rollback()
                print(f"[decision-log] run start error: {dl_exc}")
            finally:
                ddb.close()
        except Exception as dl_exc:
            print(f"[decision-log] run start error (non-fatal): {dl_exc}")

        # ── Lock (DB-backed lease with auto-expiry) ──────────────────────
        lock_acquired = False
        try:
            analysis_id = await self._acquire_lock(db, self.request_id)
            self.analysis_id = analysis_id
            lock_acquired = True

            # Start a background task to refresh the lock lease periodically
            # so long-running pipelines don't lose their lease.
            lock_refresh_task = asyncio.create_task(
                self._refresh_lock_loop(db, self.request_id)
            )

            # ── Apply defaults ───────────────────────────────────────────────
            _ = self._apply_request_defaults(request, config)
            stage_metrics: Dict[str, Dict[str, Any]] = {}

            # ── Stage 1: Data ingestion ───────────────────────────────────────
            ingest_started_at = time.time()
            posts, ingestion_trace = await self._market.ingest_data(
                db,
                request,
                config,
                article_ids=article_ids,
                trigger_source=trigger_source,
            )
            stage_metrics["ingest"] = {
                "status": "completed",
                "model_name": "",
                "duration_ms": (time.time() - ingest_started_at) * 1000,
                "item_count": len(posts),
                "details": {
                    "selected_article_ids": list(ingestion_trace.get("selected_article_ids") or []),
                    "usable_article_ids": list(ingestion_trace.get("usable_article_ids") or []),
                },
            }
            if not posts:
                yield {"type": "error", "stage": "ingestion", "detail": "No usable articles found"}
                return

            # ── Market snapshot ───────────────────────────────────────────────
            snapshot_started_at = time.time()
            price_context, quotes_by_symbol, market_validation = await self._market.get_market_snapshot(
                self.symbols,
            )
            # Collect price keys (e.g. "uso_price", "spy_price") for stage_metrics
            _price_keys = [k for k in price_context if k.endswith("_price")]
            stage_metrics["snapshot"] = {
                "status": "completed",
                "model_name": "",
                "duration_ms": (time.time() - snapshot_started_at) * 1000,
                "item_count": len(_price_keys),
                "details": {
                    "prices_available": [k.replace("_price", "").upper() for k in _price_keys],
                },
            }

            # ── Stage 2: Sentiment analysis ──────────────────────────────────
            sentiment_started_at = time.time()
            sentiment_results, sentiment_trace = await self._sentiment.analyze_sentiment(
                posts=posts,
                symbols=self.symbols,
                price_context=price_context,
                prompt_overrides=prompt_overrides,
                model_name=self.model_name,
                extraction_model=str(getattr(config, "extraction_model", "") or "").strip() or None,
            )
            stage_metrics["sentiment"] = {
                "status": "completed",
                "model_name": self.model_name,
                "duration_ms": (time.time() - sentiment_started_at) * 1000,
                "item_count": len(sentiment_results),
                "details": {
                    "used_two_stage": bool(sentiment_trace.get("used_two_stage", False)),
                    "pipeline_models": sentiment_trace.get("pipeline_models", {}),
                },
            }

            # ── Stage 3: Signal generation ───────────────────────────────────
            signal_started_at = time.time()
            # Inject technical indicators from price_context so they're available
            # for regime adaptation, ATR-based leverage capping, etc.
            price_context = self._market.inject_technical_context(
                price_context, self.symbols, db,
            )
            consensus_signal = self._signal.generate_trading_signal(
                sentiment_results=sentiment_results,
                quotes_by_symbol=quotes_by_symbol,
                risk_profile=str(getattr(config, "risk_profile", None) or "moderate"),
                previous_signal=None,
                price_context=price_context,
            )
            stage_metrics["signal"] = {
                "status": "completed",
                "model_name": "",
                "duration_ms": (time.time() - signal_started_at) * 1000,
                "item_count": len(consensus_signal) if isinstance(consensus_signal, dict) else 0,
                "details": {},
            }

            # ── Stage 4: Materiality gate ────────────────────────────────────
            materiality_started_at = time.time()
            is_material = self._materiality.material_change_gate(
                db=db,
                symbols=self.symbols,
                posts_count=len(posts),
                sentiment_results=sentiment_results,
                price_context=price_context,
                quotes_by_symbol=quotes_by_symbol,
                previous_state=self._hysteresis.latest_previous_analysis_state(db),
                candidate_signal=consensus_signal,
                per_symbol_counts=ingestion_trace.get("per_symbol_article_counts"),
            )
            materiality_details: Dict[str, Any] = {"checked": True, "blocked": not is_material}
            stage_metrics["materiality"] = {
                "status": "completed",
                "model_name": "",
                "duration_ms": (time.time() - materiality_started_at) * 1000,
                "item_count": 0,
                "details": {
                    "is_material": is_material,
                    "gate_checked": materiality_details.get("checked", False),
                    "gate_blocked": materiality_details.get("blocked", False),
                    "gate_reason": materiality_details.get("reason"),
                    "rolling_baseline": materiality_details.get("rolling_baseline"),
                },
            }

            # ── Build the AnalysisResponse (needed before persistence/backtest) ──
            # Map sentiment result keys to SentimentScore field names
            sentiment_scores = {}
            for sym, result in sentiment_results.items():
                sentiment_scores[sym] = SentimentScore(
                    market_bluster=float(result.get("bluster_score", 0.0) or 0.0),
                    policy_change=float(result.get("policy_score", 0.0) or 0.0),
                    confidence=float(result.get("confidence", 0.0) or 0.0),
                    reasoning=str(result.get("reasoning", "") or ""),
                )
            response = AnalysisResponse(
                request_id=self.request_id,
                timestamp=self.timestamp,
                symbols_analyzed=list(sentiment_results.keys()),
                posts_scraped=len(posts),
                sentiment_scores=sentiment_scores,
                trading_signal=consensus_signal,
                stage_metrics=stage_metrics,
                market_validation=market_validation,
            )

            # ── Stage 5: Backtest ────────────────────────────────────────────
            backtest_started_at = time.time()
            bt_results = await self._backtest.run_backtest(
                symbols=self.symbols,
                sentiment_results=sentiment_results,
                lookback_days=getattr(request, "lookback_days", 14) or 14,
                risk_profile=str(getattr(config, "risk_profile", None) or "standard"),
            )
            # Convert raw dict to Pydantic model so downstream consumers
            # (e.g. PersistenceService._save_analysis_result) can access
            # attributes like .total_return instead of ["total_return"]
            response.backtest_results = BacktestResults(**bt_results) if bt_results else None
            stage_metrics["backtest"] = {
                "status": "completed",
                "model_name": "",
                "duration_ms": (time.time() - backtest_started_at) * 1000,
                "item_count": 1 if bt_results else 0,
                "details": {},
            }

            # ── Stage 6: Persistence ─────────────────────────────────────────
            persistence_started_at = time.time()
            if is_material:
                self._persistence.save_analysis_result(
                    db=db,
                    request_id=self.request_id,
                    response=response,
                    quotes_by_symbol=quotes_by_symbol,
                    posts=posts,
                    model_name=self.model_name,
                    prompt_overrides=prompt_overrides,
                    sentiment_results=sentiment_results,
                    per_symbol_counts=ingestion_trace.get("per_symbol_article_counts"),
                    price_context=price_context,
                    extraction_model=str(getattr(config, "extraction_model", "") or "").strip() or "",
                    reasoning_model=str(getattr(config, "reasoning_model", "") or "").strip() or "",
                    risk_profile=str(getattr(config, "risk_profile", None) or "moderate"),
                )
            else:
                # Keep previous signal when materiality gate blocks
                previous_state = self._hysteresis.latest_previous_analysis_state(db)
                if previous_state:
                    materiality_details["kept_previous_signal"] = True
                    print(f"[pipeline] Materiality gate BLOCKED — keeping previous signal")
                else:
                    print(f"[pipeline] Materiality gate: is_material={is_material}, has_previous_state={previous_state is not None}")
            stage_metrics["persistence"] = {
                "status": "completed",
                "model_name": "",
                "duration_ms": (time.time() - persistence_started_at) * 1000,
                "item_count": 0,
                "details": {
                    "is_material": is_material,
                    "kept_previous_signal": materiality_details.get("kept_previous_signal", False),
                },
            }

            # ── Decision Log: run complete ───────────────────────────────────
            try:
                from database.engine import DecisionLogSessionLocal
                from services.decision_logger import logger as dl

                ddb = DecisionLogSessionLocal()
                try:
                    # Run-level logging
                    dl.log_run_complete(
                        ddb,
                        run_id=self.request_id,
                        total_articles_considered=len(posts) if posts else None,
                        total_articles_used=len(sentiment_results) if sentiment_results else None,
                        duration_ms=int((time.time() - self._run_started) * 1000) if hasattr(self, '_run_started') else None,
                    )

                    # Per-symbol logging
                    _stage1_trace = (sentiment_trace or {}).get("stage1", {})
                    _kw_attr_by_sym = _stage1_trace.get("keyword_attribution_by_symbol", {})
                    for sym, result in (sentiment_results or {}).items():
                        raw_scores = {
                            "bluster": result.get("bluster_score"),
                            "policy": result.get("policy_score"),
                            "confidence": result.get("confidence"),
                            "directional": result.get("directional_score"),
                        }
                        signal_dict = consensus_signal.model_dump(mode="json") if consensus_signal else {}
                        parsed_payload = result.get("parsed_payload") or {}
                        dl.log_symbol_scores(
                            ddb,
                            run_id=self.request_id,
                            symbol=sym,
                            blue_team_output=parsed_payload,
                            raw_scores=raw_scores,
                            final_signal={
                                "type": signal_dict.get("signal_type"),
                                "conviction": signal_dict.get("conviction_level"),
                                "trading_type": signal_dict.get("trading_type"),
                                "holding_window_hours": signal_dict.get("holding_period_hours"),
                                "urgency": signal_dict.get("urgency"),
                                "stop_loss_pct": signal_dict.get("stop_loss_pct"),
                                "take_profit_pct": signal_dict.get("take_profit_pct"),
                                "data_gap_hold": signal_dict.get("data_gap_hold"),
                            },
                            entry_threshold_used=stage_metrics.get("entry_threshold"),
                            materiality_info={
                                "checked": stage_metrics.get("materiality", {}).get("gate_checked", False),
                                "blocked": stage_metrics.get("materiality", {}).get("gate_blocked", False),
                                "reason": stage_metrics.get("materiality", {}).get("gate_reason"),
                                "rolling_baseline": stage_metrics.get("materiality", {}).get("rolling_baseline"),
                            },
                            event_type=str(parsed_payload.get("event_type") or "").strip() or None,
                            keyword_attribution=_kw_attr_by_sym.get(sym),
                        )
                    ddb.commit()
                except Exception as dl_exc:
                    ddb.rollback()
                    print(f"[decision-log] symbol logging error: {dl_exc}")
                finally:
                    ddb.close()
            except Exception as dl_exc:
                print(f"[decision-log] pipeline logging error (non-fatal): {dl_exc}")

            yield {"type": "final_response", "response": response}
        finally:
            # Cancel the lock refresh task if it's still running
            if lock_acquired:
                try:
                    lock_refresh_task.cancel()
                    try:
                        await asyncio.wait_for(lock_refresh_task, timeout=5)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                except (NameError, UnboundLocalError):
                    pass  # lock_refresh_task was never assigned

            # Always release the DB-backed lease when the pipeline finishes
            # (success, error, or early return via generator close).
            if lock_acquired:
                try:
                    from services.app_config import release_analysis_lock
                    release_analysis_lock(db, self.request_id)
                except Exception as exc:
                    print(f"[pipeline] Error releasing analysis lock: {exc}")

    # ── Helpers (private) ───────────────────────────────────────────────

    def _get_default_symbols(self) -> List[str]:
        """Return the default symbol list when no symbols are explicitly provided."""
        return ["USO", "IBIT", "QQQ", "SPY"]

    def _resolve_active_model_name(self, config: Any) -> str:
        # Prefer the current two-stage config fields; keep legacy support.
        reasoning_model = str(getattr(config, "reasoning_model", "") or "").strip()
        extraction_model = str(getattr(config, "extraction_model", "") or "").strip()
        legacy_model = str(getattr(config, "analysis_model", "") or "").strip()
        env_model = str(os.getenv("OLLAMA_MODEL", "") or "").strip()
        return reasoning_model or extraction_model or legacy_model or env_model or "unknown"

    def _apply_request_defaults(self, request: AnalysisRequest, config: Any) -> AnalysisRequest:
        return request

    def _coerce_to_json_compatible(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: str(value) if isinstance(value, (set, frozenset)) else value
            for key, value in result.items()
        }

    async def _acquire_lock(self, db: Session, request_id: str) -> str:
        """Acquire a DB-backed analysis lease with auto-expiry.

        Uses the existing analysis_lock columns in app_config to provide a
        distributed lease that survives process restarts.  If a previous run
        crashed without releasing its lease, the lease will have expired after
        ANALYSIS_LOCK_LEASE_SECONDS and this call will succeed.
        """
        from services.app_config import try_acquire_analysis_lock

        acquired = try_acquire_analysis_lock(
            db,
            request_id,
            lease_seconds=ANALYSIS_LOCK_LEASE_SECONDS,
        )
        if not acquired:
            raise RuntimeError(
                f"Analysis already in progress (lock held by another request). "
                f"Request {request_id} rejected to prevent concurrent pipeline runs."
            )
        analysis_id = str(uuid.uuid4())
        return analysis_id

    async def _refresh_lock_loop(self, db: Session, request_id: str) -> None:
        """Periodically refresh the DB-backed lease while the pipeline runs.

        This runs as a background asyncio task and is cancelled when the
        pipeline finishes (success or error).
        """
        from services.app_config import refresh_analysis_lock

        while True:
            try:
                await asyncio.sleep(ANALYSIS_LOCK_REFRESH_INTERVAL_SECONDS)
                refresh_analysis_lock(
                    db,
                    request_id,
                    lease_seconds=ANALYSIS_LOCK_LEASE_SECONDS,
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[pipeline] Lock refresh error (non-fatal): {exc}")

    def _mark_scraped_articles_processed(self, db: Session, article_ids: List[int]) -> None:
        """Mark selected queued articles as processed."""
        if not article_ids:
            return
        (
            db.query(ScrapedArticle)
            .filter(ScrapedArticle.id.in_(article_ids))
            .update({ScrapedArticle.processed: True}, synchronize_session=False)
        )
        db.commit()