"""
Analysis API Router
Implements the /analyze and /analyze/stream endpoints
"""

import uuid
import json
import time
import asyncio
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List, Dict, Any, Optional, AsyncGenerator, Tuple, Callable, Awaitable
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from schemas.analysis import (
    AnalysisRequest,
    AnalysisResponse,
    SentimentScore,
    StageMetric,
    TradingSignal,
    RedTeamReview,
    RedTeamDebug,
    RedTeamSignalChange,
    BacktestResults,
    ModelInputDebug,
    ModelInputArticle,
    IngestionTraceDebug,
)
from services.symbol_proxy_terms import ensure_symbol_proxy_terms_fresh
from database.engine import get_db
from database.models import (
    ScrapedArticle,
    AnalysisResult,
    Trade,
    TradeSnapshot,
    TradeExecution,
    TradeClose,
    TradingSignal as TradingSignalModel,
)
from security import require_admin_token
from services.data_ingestion.parser import RSSFeedParser
from services.data_ingestion.worker import build_analysis_posts
from services.data_ingestion.yfinance_client import PriceClient
from services.data_ingestion.market_validation import MarketValidationClient
from services.ollama import get_llm_backend_status
from services.sentiment.engine import SentimentEngine
from services.sentiment.prompts import (
    get_symbol_specialist_focus,
    format_symbol_specialist_context_prompt,
    expand_proxy_terms_for_matching,
    normalize_text_for_matching,
    format_red_team_review_prompt,
)
from services.backtesting.optimization import RollingWindowOptimizer
from services.app_config import (
    build_enabled_rss_feed_labels,
    build_enabled_rss_feed_map,
    get_or_create_app_config,
    mark_analysis_started,
    mark_analysis_completed,
    refresh_analysis_lock,
    release_analysis_lock,
    resolve_rss_articles_per_feed,
    resolve_web_research_items_per_symbol,
    resolve_web_research_recency_days,
    try_acquire_analysis_lock,
    DEFAULT_SNAPSHOT_RETENTION_LIMIT,
)
from config.logic_loader import LOGIC as _L
from services.pnl_tracker import PnLTracker, persist_recommendation_trades
from services.remote_snapshot import trigger_remote_snapshot_delivery
from services.runtime_health import record_analysis_result, record_data_pull
from services.trading_instruments import build_execution_recommendation
from services.web_research import fetch_recent_symbol_web_context
from services.analysis.cache_service import get_price_cache_service
from services.analysis.sentiment_service import SentimentService
from services.analysis.signal_service import SignalService
from services.analysis.materiality_service import MaterialityService
from services.analysis.hysteresis_service import HysteresisService
from services.analysis.persistence_service import PersistenceService
from services.analysis.backtest_service import BacktestService
from services.analysis.market_data_service import MarketDataService
from services.analysis.pipeline_service import PipelineService
from config.market_constants import SYMBOL_RELEVANCE_TERMS
from services.risk_policy_runtime import build_crazy_ramp_context


router = APIRouter()

ProgressCallback = Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]]


def _stage_metric(
    *,
    status: str,
    model_name: str = "",
    duration_ms: float = 0.0,
    item_count: Optional[int] = None,
    **details: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": status,
        "model_name": model_name,
        "duration_ms": round(float(duration_ms or 0.0), 2),
        "item_count": item_count,
        "details": details or {},
    }
    return payload


class AnalysisLockError(RuntimeError):
    """Raised when another analysis run already owns the queue lease."""


def _utc_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def _format_recommendation_text(rec: Optional[Dict[str, Any]]) -> str:
    if not rec:
        return "No recommendation"
    action = str(rec.get("action", "") or "").upper().strip()
    symbol = str(rec.get("symbol", "") or "").upper().strip()
    leverage = str(rec.get("leverage", "") or "").strip()
    if not action and not symbol:
        return "No recommendation"
    parts = [part for part in [action, symbol, leverage] if part]
    return " ".join(parts)


def _recommendations_by_underlying(signal: Optional[TradingSignal]) -> Dict[str, Dict[str, Any]]:
    recs: Dict[str, Dict[str, Any]] = {}
    for rec in (getattr(signal, "recommendations", None) or []):
        key = str(rec.get("underlying_symbol") or rec.get("symbol") or "").upper().strip()
        if key:
            recs[key] = rec
    return recs


def _build_red_team_signal_changes(
    blue_team_signal: Optional[TradingSignal],
    consensus_signal: Optional[TradingSignal],
    red_team_review: Optional[RedTeamReview],
) -> List[RedTeamSignalChange]:
    blue_map = _recommendations_by_underlying(blue_team_signal)
    consensus_map = _recommendations_by_underlying(consensus_signal)
    review_map = {
        str(review.symbol or "").upper().strip(): review
        for review in (red_team_review.symbol_reviews if red_team_review else [])
        if str(review.symbol or "").strip()
    }
    symbols = sorted(set(blue_map.keys()) | set(consensus_map.keys()) | set(review_map.keys()))
    changes: List[RedTeamSignalChange] = []

    for symbol in symbols:
        blue_text = _format_recommendation_text(blue_map.get(symbol))
        consensus_text = _format_recommendation_text(consensus_map.get(symbol))
        changed = blue_text != consensus_text
        review = review_map.get(symbol)
        if blue_text == "No recommendation" and consensus_text != "No recommendation":
            change_type = "added"
        elif blue_text != "No recommendation" and consensus_text == "No recommendation":
            change_type = "removed"
        elif changed:
            blue_action = str((blue_map.get(symbol) or {}).get("action", "") or "").upper()
            consensus_action = str((consensus_map.get(symbol) or {}).get("action", "") or "").upper()
            blue_leverage = str((blue_map.get(symbol) or {}).get("leverage", "") or "")
            consensus_leverage = str((consensus_map.get(symbol) or {}).get("leverage", "") or "")
            change_type = "direction_flip" if blue_action and consensus_action and blue_action != consensus_action else (
                "leverage_change" if blue_leverage != consensus_leverage else "ticker_change"
            )
        else:
            change_type = "unchanged"

        changes.append(
            RedTeamSignalChange(
                symbol=symbol,
                blue_team_recommendation=blue_text,
                consensus_recommendation=consensus_text,
                changed=changed,
                change_type=change_type,
                rationale=str(getattr(review, "rationale", "") or ""),
                evidence=list(getattr(review, "evidence", []) or []),
            )
        )

    return changes

@router.post("/trades/{trade_id}/execute", tags=["Analysis"])
async def record_trade_execution(
    trade_id: int,
    payload: Dict[str, Any],
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    """Record the user's actual trade execution for a recommendation."""
    executed_action = str(payload.get("executed_action", "")).upper().strip()
    if executed_action not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="executed_action must be BUY or SELL")

    try:
        executed_price = float(payload.get("executed_price"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="executed_price must be a positive number")
    if executed_price <= 0:
        raise HTTPException(status_code=400, detail="executed_price must be a positive number")

    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    tracker = PnLTracker()
    execution = tracker.record_execution(
        db=db,
        trade_id=trade_id,
        executed_action=executed_action,
        executed_price=executed_price,
        notes=str(payload.get("notes", "")).strip(),
    )
    return {
        "id": execution.id,
        "trade_id": trade_id,
        "executed_action": execution.executed_action,
        "executed_price": execution.executed_price,
        "executed_at": execution.executed_at.isoformat(),
    }


@router.post("/trades/{trade_id}/close", tags=["Analysis"])
async def record_trade_close(
    trade_id: int,
    payload: Dict[str, Any],
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    """Record the user's closing price for a trade, locking in realized P&L."""
    try:
        closed_price = float(payload.get("closed_price"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="closed_price must be a positive number")
    if closed_price <= 0:
        raise HTTPException(status_code=400, detail="closed_price must be a positive number")

    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    existing = db.query(TradeClose).filter(TradeClose.trade_id == trade_id).first()
    if existing:
        existing.closed_price = closed_price
        existing.notes = str(payload.get("notes", "")).strip() or None
        db.commit()
        db.refresh(existing)
        close = existing
    else:
        close = TradeClose(
            trade_id=trade_id,
            closed_price=closed_price,
            notes=str(payload.get("notes", "")).strip() or None,
        )
        db.add(close)
        db.commit()
        db.refresh(close)

    from services.pnl_tracker import calculate_return_pct, ensure_utc
    closed_return_pct = calculate_return_pct(
        action=trade.action,
        entry_price=trade.entry_price,
        exit_price=closed_price,
    )
    return {
        "id": close.id,
        "trade_id": trade_id,
        "closed_price": close.closed_price,
        "closed_at": ensure_utc(close.closed_at).isoformat(),
        "closed_return_pct": round(closed_return_pct, 4),
    }


@router.delete("/trades/{trade_id}", tags=["Analysis"])
async def delete_trade(
    trade_id: int,
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    """Delete an unexecuted trade recommendation. Blocked if a user execution exists."""
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    execution = db.query(TradeExecution).filter(TradeExecution.trade_id == trade_id).first()
    if execution:
        raise HTTPException(status_code=409, detail="Cannot delete a trade that has been executed")

    db.query(TradeSnapshot).filter(TradeSnapshot.trade_id == trade_id).delete()
    db.query(TradeClose).filter(TradeClose.trade_id == trade_id).delete()
    db.delete(trade)
    db.commit()
    return {"deleted": trade_id}


@router.delete("/trades/{trade_id}/execution", tags=["Analysis"])
async def delete_trade_execution(
    trade_id: int,
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    """Remove an accidental execution record, reverting the trade to unexecuted."""
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    execution = db.query(TradeExecution).filter(TradeExecution.trade_id == trade_id).first()
    if not execution:
        raise HTTPException(status_code=404, detail="No execution record found for this trade")

    db.delete(execution)
    db.commit()
    return {"deleted_execution": trade_id}


@router.get("/prices", tags=["Market Data"])
async def get_market_prices(
    symbols: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Return session-aware quotes for the requested symbols, defaulting to tracked config."""
    client = PriceClient()
    cache = get_price_cache_service()
    config = get_or_create_app_config(db)
    requested_symbols = [
        str(symbol).upper().strip()
        for symbol in (symbols.split(",") if symbols else (config.tracked_symbols or ["USO", "IBIT", "QQQ", "SPY"]))
        if str(symbol).strip()
    ]
    result = {}
    symbols_to_fetch = []

    for symbol in requested_symbols:
        cached_entry = cache.get(symbol)
        if cached_entry:
            result[symbol] = cached_entry
            continue
        symbols_to_fetch.append(symbol)

    if symbols_to_fetch:
        print(f"Fetching fresh prices from yfinance: {', '.join(symbols_to_fetch)}")
        for symbol in symbols_to_fetch:
            quote = client.get_realtime_quote(symbol)
            if not quote:
                continue
            price = quote.get("current_price") or quote.get("previous_close")
            if not price:
                continue
            prev = quote.get("previous_close") or price
            change_pct = round((price - prev) / prev * 100, 2) if prev else 0.0
            cache_ttl = cache.resolve_ttl(quote)
            entry = {
                "price": round(price, 2),
                "change": round(price - prev, 2),
                "change_pct": change_pct,
                "day_low": round(quote.get("day_low") or price, 2),
                "day_high": round(quote.get("day_high") or price, 2),
                "session": quote.get("session") or "closed",
                "as_of": _utc_iso(quote.get("timestamp")),
                "source": quote.get("source") or "unknown",
                "is_stale": bool(quote.get("is_stale")),
                "cache_ttl_seconds": cache_ttl,
            }
            cache.set(symbol, entry)
            result[symbol] = entry

    return result


@router.get("/ollama/status", tags=["System"])
async def get_ollama_runtime_status(db: Session = Depends(get_db)):
    """Return reachability and active model details from the configured inference backend."""
    config = get_or_create_app_config(db)
    backend = str(getattr(config, "inference_backend", "ollama") or "ollama")
    try:
        return get_llm_backend_status(backend=backend)
    except Exception as exc:
        return {
            "reachable": False,
            "ollama_root": os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate").replace("/api/generate", ""),
            "configured_model": os.getenv("OLLAMA_MODEL", "").strip(),
            "active_model": "",
            "available_models": [],
            "resolution": "unreachable",
            "error": str(exc),
        }


@router.get("/analysis-snapshots", tags=["System"])
async def list_analysis_snapshots(limit: int = 10, db: Session = Depends(get_db)):
    """List recent persisted analysis snapshots for Advanced Mode comparison."""
    config = get_or_create_app_config(db)
    retention_limit = max(1, min(100, int(getattr(config, "snapshot_retention_limit", DEFAULT_SNAPSHOT_RETENTION_LIMIT))))
    capped_limit = max(1, min(retention_limit, int(limit)))
    results = (
        db.query(AnalysisResult)
        .order_by(AnalysisResult.timestamp.desc())
        .limit(capped_limit)
        .all()
    )
    items = []
    for result in results:
        metadata = result.run_metadata or {}
        snapshot = metadata.get("dataset_snapshot") or {}
        signal_data = result.signal or {}
        trade_recommendations = []
        trade_rows = (
            db.query(Trade)
            .filter(Trade.analysis_id == result.id)
            .order_by(Trade.id.asc())
            .all()
        )
        for trade in trade_rows:
            trade_recommendations.append(
                {
                    "action": trade.action,
                    "symbol": trade.symbol,
                    "leverage": trade.leverage,
                    "underlying_symbol": trade.underlying_symbol or trade.symbol,
                }
            )
        recommendations = (
            signal_data.get("recommendations")
            or snapshot.get("trading_signal", {}).get("recommendations")
            or trade_recommendations
        )
        items.append(
            {
                "request_id": result.request_id,
                "timestamp": _utc_iso(result.timestamp),
                "model_name": metadata.get("model_name") or "",
                "extraction_model": snapshot.get("extraction_model") or "",
                "reasoning_model": snapshot.get("reasoning_model") or "",
                "risk_profile": snapshot.get("risk_profile") or metadata.get("risk_profile") or "",
                "symbols": metadata.get("symbols") or [],
                "posts_scraped": metadata.get("posts_scraped") or 0,
                "snapshot_available": bool(snapshot),
                "snapshot_article_count": len(snapshot.get("posts") or []),
                "signal_type": signal_data.get("signal_type") or "HOLD",
                "confidence_score": signal_data.get("confidence_score") or 0.0,
                "recommendations": recommendations or [],
            }
        )
    return {"items": items}


def _load_saved_analysis_response(analysis: AnalysisResult) -> AnalysisResponse:
    """Reconstruct a full analysis response from a persisted analysis row."""
    metadata = analysis.run_metadata or {}
    snapshot = metadata.get("dataset_snapshot") or {}
    sentiment_data = analysis.sentiment_data or {}
    signal_data = analysis.signal or {}
    backtest_data = analysis.backtest_results or {}
    blue_signal_data = metadata.get("blue_team_signal") or {}
    red_team_debug_payload = metadata.get("red_team_debug") or snapshot.get("red_team_debug") or {}

    sentiment_scores_payload = sentiment_data.get("sentiment_scores") or {}
    aggregated_payload = sentiment_data.get("aggregated_sentiment") or {}
    market_validation = sentiment_data.get("market_validation") or snapshot.get("market_validation") or {}
    model_inputs_payload = snapshot.get("model_inputs") or {}

    sentiment_scores = {
        symbol: SentimentScore(
            market_bluster=float((payload or {}).get("market_bluster", 0.0) or 0.0),
            policy_change=float((payload or {}).get("policy_change", 0.0) or 0.0),
            confidence=float((payload or {}).get("confidence", 0.0) or 0.0),
            reasoning=str((payload or {}).get("reasoning", "") or ""),
        )
        for symbol, payload in sentiment_scores_payload.items()
    }

    aggregated_sentiment = None
    if aggregated_payload:
        aggregated_sentiment = SentimentScore(
            market_bluster=float(aggregated_payload.get("market_bluster", 0.0) or 0.0),
            policy_change=float(aggregated_payload.get("policy_change", 0.0) or 0.0),
            confidence=float(aggregated_payload.get("confidence", 0.0) or 0.0),
            reasoning=str(aggregated_payload.get("reasoning", "") or ""),
        )

    trading_signal = TradingSignal(
        signal_type=str(signal_data.get("signal_type", "HOLD") or "HOLD"),
        confidence_score=float(signal_data.get("confidence_score", 0.0) or 0.0),
        urgency=str(signal_data.get("urgency", "LOW") or "LOW"),
        entry_symbol=str(signal_data.get("entry_symbol", "") or ""),
        recommendations=list(signal_data.get("recommendations") or []),
        conviction_level=str(signal_data.get("conviction_level", "LOW") or "LOW"),
        holding_period_hours=int(signal_data.get("holding_period_hours", 2) or 2),
        trading_type=str(signal_data.get("trading_type", "VOLATILE_EVENT") or "VOLATILE_EVENT"),
        action_if_already_in_position=str(signal_data.get("action_if_already_in_position", "HOLD") or "HOLD"),
        entry_price=signal_data.get("entry_price"),
        stop_loss_pct=float(signal_data.get("stop_loss_pct", 2.0) or 2.0),
        take_profit_pct=float(signal_data.get("take_profit_pct", 3.0) or 3.0),
        position_size_usd=float(signal_data.get("position_size_usd", 1000.0) or 1000.0),
    )
    blue_team_signal = None
    if blue_signal_data:
        blue_team_signal = TradingSignal(
            signal_type=str(blue_signal_data.get("signal_type", "HOLD") or "HOLD"),
            confidence_score=float(blue_signal_data.get("confidence_score", 0.0) or 0.0),
            urgency=str(blue_signal_data.get("urgency", "LOW") or "LOW"),
            entry_symbol=str(blue_signal_data.get("entry_symbol", "") or ""),
            recommendations=list(blue_signal_data.get("recommendations") or []),
            conviction_level=str(blue_signal_data.get("conviction_level", "LOW") or "LOW"),
            holding_period_hours=int(blue_signal_data.get("holding_period_hours", 2) or 2),
            trading_type=str(blue_signal_data.get("trading_type", "VOLATILE_EVENT") or "VOLATILE_EVENT"),
            action_if_already_in_position=str(blue_signal_data.get("action_if_already_in_position", "HOLD") or "HOLD"),
            entry_price=blue_signal_data.get("entry_price"),
            stop_loss_pct=float(blue_signal_data.get("stop_loss_pct", 2.0) or 2.0),
            take_profit_pct=float(blue_signal_data.get("take_profit_pct", 3.0) or 3.0),
            position_size_usd=float(blue_signal_data.get("position_size_usd", 1000.0) or 1000.0),
        )
    red_team_payload = metadata.get("red_team_review") or {}

    backtest_results = None
    if backtest_data:
        backtest_results = BacktestResults(
            total_return=float(backtest_data.get("total_return", 0.0) or 0.0),
            win_rate=float(backtest_data.get("win_rate", 0.0) or 0.0),
            max_drawdown=float(backtest_data.get("max_drawdown", 0.0) or 0.0),
            sharpe_ratio=float(backtest_data.get("sharpe_ratio", 0.0) or 0.0),
            total_trades=int(backtest_data.get("total_trades", 0) or 0),
            lookback_days=int(backtest_data.get("lookback_days", snapshot.get("lookback_days", 14)) or 14),
        )

    return AnalysisResponse(
        request_id=analysis.request_id,
        timestamp=analysis.timestamp,
        symbols_analyzed=list(metadata.get("symbols") or snapshot.get("symbols") or []),
        posts_scraped=int(metadata.get("posts_scraped", 0) or 0),
        sentiment_scores=sentiment_scores,
        aggregated_sentiment=aggregated_sentiment,
        trading_signal=trading_signal,
        blue_team_signal=blue_team_signal,
        market_validation=market_validation,
        ingestion_trace=IngestionTraceDebug.model_validate(snapshot.get("ingestion_trace") or {}) if snapshot.get("ingestion_trace") else None,
        red_team_review=RedTeamReview.model_validate(red_team_payload) if red_team_payload else None,
        red_team_debug=RedTeamDebug.model_validate(red_team_debug_payload) if red_team_debug_payload else None,
        stage_metrics={
            key: StageMetric.model_validate(value)
            for key, value in (metadata.get("stage_metrics") or {}).items()
        },
        model_inputs=ModelInputDebug.model_validate(model_inputs_payload) if model_inputs_payload else None,
        backtest_results=backtest_results,
        processing_time_ms=float(metadata.get("processing_time_ms", 0.0) or 0.0),
        status="SUCCESS",
    )


@router.get("/analysis-snapshots/{request_id}", tags=["System"])
async def get_analysis_snapshot_detail(request_id: str, db: Session = Depends(get_db)):
    """Return the persisted full analysis payload for one saved run."""
    analysis = db.query(AnalysisResult).filter(AnalysisResult.request_id == request_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Saved analysis snapshot not found")
    return _load_saved_analysis_response(analysis)


@router.post("/analysis-snapshots/{request_id}/rerun", tags=["Analysis"])
async def rerun_analysis_snapshot(
    request_id: str,
    payload: Dict[str, Any],
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    """Re-run a frozen analysis dataset snapshot with alternate model settings."""
    analysis = db.query(AnalysisResult).filter(AnalysisResult.request_id == request_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Saved analysis snapshot not found")

    metadata = analysis.run_metadata or {}
    snapshot = metadata.get("dataset_snapshot") or {}
    if not snapshot:
        raise HTTPException(status_code=400, detail="This analysis does not contain a reusable dataset snapshot")

    requested_model = str(payload.get("model_name", "") or "").strip()
    rerun_extraction = str(payload.get("extraction_model", "") or "").strip() or None
    rerun_reasoning = str(payload.get("reasoning_model", "") or "").strip() or None
    effective_model = requested_model or rerun_extraction or ""
    if not effective_model:
        raise HTTPException(status_code=400, detail="model_name or extraction_model is required")

    started = time.time()
    rerun_request_id = str(uuid.uuid4())[:8]
    symbols = list(snapshot.get("symbols") or metadata.get("symbols") or [])
    prompt_overrides = snapshot.get("prompt_overrides") or {}
    posts = _restore_snapshot_posts(snapshot.get("posts") or [])
    price_context = snapshot.get("price_context") or {}
    saved_model_inputs = snapshot.get("model_inputs") or {}
    web_context_by_symbol = saved_model_inputs.get("web_context_by_symbol") or {}
    web_items_by_symbol = saved_model_inputs.get("web_items_by_symbol") or {}
    saved_secret_trace = snapshot.get("secret_trace") or metadata.get("secret_trace") or {}
    quotes_by_symbol = _restore_snapshot_quotes(snapshot.get("quotes_by_symbol") or {})
    market_validation = snapshot.get("market_validation") or {}

    if not posts:
        raise HTTPException(status_code=400, detail="Saved snapshot is missing article data")

    config = get_or_create_app_config(db)
    services = _analysis_services(db)
    sentiment_service = services["sentiment"]
    signal_service = services["signal"]
    materiality_service = services["materiality"]
    hysteresis_service = services["hysteresis"]
    persistence_service = services["persistence"]

    mark_analysis_started(db, rerun_request_id)
    stage_metrics: Dict[str, Dict[str, Any]] = {
        "ingest": _stage_metric(
            status="completed",
            duration_ms=0.0,
            item_count=len(posts),
            trigger_source="snapshot_rerun",
            selected_count=len(posts),
            usable_count=len(posts),
            source_request_id=request_id,
        )
    }

    try:
        previous_state = hysteresis_service.latest_previous_analysis_state(db)
        normalized_previous_state = _normalize_previous_state(previous_state)
        previous_response = normalized_previous_state.get("response") if normalized_previous_state else None

        _prev_analysis = (previous_state or {}).get("analysis")
        _prev_ts = getattr(_prev_analysis, "timestamp", None) if _prev_analysis else None
        if _prev_ts is not None:
            if _prev_ts.tzinfo is None:
                _prev_ts = _prev_ts.replace(tzinfo=timezone.utc)
            signal_age_hours = (datetime.now(timezone.utc) - _prev_ts).total_seconds() / 3600.0
        else:
            signal_age_hours = 0.0

        # Ensure symbol proxy terms are fresh (30-day TTL check)
        extraction_model_for_terms = rerun_extraction or effective_model
        symbol_proxy_terms_by_symbol = await ensure_symbol_proxy_terms_fresh(
            db=db,
            config=config,
            symbols=symbols,
            model_name=extraction_model_for_terms,
        )

        sentiment_results, sentiment_trace = await sentiment_service.analyze_sentiment(
            posts=posts,
            symbols=symbols,
            price_context=price_context,
            prompt_overrides=prompt_overrides,
            model_name=effective_model,
            extraction_model=rerun_extraction,
            reasoning_model=rerun_reasoning,
            web_context_by_symbol=web_context_by_symbol,
            symbol_proxy_terms_by_symbol=symbol_proxy_terms_by_symbol,
        )
        stage_metrics.update(sentiment_trace.get("stage_metrics") or {})

        snapshot_risk = str(snapshot.get("risk_profile") or getattr(config, "risk_profile", "standard") or "standard")
        previous_signal = None
        if previous_response:
            prev_signal_payload = previous_response.get("blue_team_signal") or previous_response.get("trading_signal")
            if isinstance(prev_signal_payload, dict):
                try:
                    previous_signal = TradingSignal.model_validate(prev_signal_payload)
                except Exception:
                    previous_signal = None

        use_closed_market_hysteresis = (
            hysteresis_service.is_closed_market_session(quotes_by_symbol)
            and previous_response is not None
        )
        blue_team_signal = signal_service.generate_trading_signal(
            sentiment_results=sentiment_results,
            quotes_by_symbol=quotes_by_symbol,
            risk_profile=snapshot_risk,
            previous_signal=previous_signal,
            stability_mode="closed_market_hysteresis" if use_closed_market_hysteresis else "normal",
            price_context=price_context,
            signal_age_hours=signal_age_hours,
            crazy_ramp_context=await build_crazy_ramp_context(
                symbols=symbols,
                risk_profile=snapshot_risk,
                risk_policy=dict(getattr(config, "risk_policy", {}) or {}),
                price_context=price_context,
            ),
        )

        per_symbol_counts = materiality_service._count_symbol_articles(
            posts,
            symbols,
            relevance_terms=SYMBOL_RELEVANCE_TERMS,
        )
        if normalized_previous_state and not materiality_service.material_change_gate(
            db=db,
            symbols=symbols,
            posts_count=len(posts),
            sentiment_results=sentiment_results,
            price_context=price_context,
            quotes_by_symbol=quotes_by_symbol,
            previous_state=normalized_previous_state,
            candidate_signal=blue_team_signal,
            per_symbol_counts=per_symbol_counts,
        ):
            if previous_signal:
                blue_team_signal = previous_signal

        quotes_by_symbol = signal_service.ensure_execution_quotes(blue_team_signal, quotes_by_symbol)
        if blue_team_signal.entry_symbol in quotes_by_symbol:
            blue_team_signal.entry_price = quotes_by_symbol[blue_team_signal.entry_symbol].get("current_price")

        red_team_review = None
        red_team_debug = None
        trading_signal = blue_team_signal
        if bool(getattr(config, "red_team_enabled", True)):
            red_team_started = time.time()
            print(f"[red-team] stage starting — {len(symbols)} symbol(s), model={rerun_reasoning or effective_model}")
            red_team_context = signal_service.build_red_team_context(
                symbols=symbols,
                posts=posts,
                sentiment_results=sentiment_results,
                trading_signal=blue_team_signal,
                price_context=price_context,
                quotes_by_symbol=quotes_by_symbol,
                market_validation=market_validation,
            )
            red_team_review, red_team_debug = signal_service.run_red_team_review(
                model_name=rerun_reasoning or effective_model,
                context={"raw_context": json.dumps(red_team_context, ensure_ascii=True, default=str, indent=2)},
            )
            trading_signal = signal_service.build_consensus_trading_signal(
                blue_team_signal=blue_team_signal,
                red_team_review=red_team_review,
                quotes_by_symbol=quotes_by_symbol,
                risk_profile=snapshot_risk,
            )
            if red_team_debug and red_team_review:
                red_team_debug.signal_changes = signal_service.build_red_team_signal_changes(
                    blue_team_signal,
                    trading_signal,
                    red_team_review,
                )
            stage_metrics["red_team"] = _stage_metric(
                status="completed",
                model_name=rerun_reasoning or effective_model,
                duration_ms=(time.time() - red_team_started) * 1000,
                item_count=len(symbols),
                reviewed_symbols=len(symbols),
            )
        else:
            print("[red-team] stage skipped — red_team_enabled=False in config")
            stage_metrics["red_team"] = _stage_metric(
                status="skipped",
                model_name=rerun_reasoning or effective_model,
                duration_ms=0.0,
                item_count=len(symbols),
                reason="disabled in config",
            )

        quotes_by_symbol = signal_service.ensure_execution_quotes(trading_signal, quotes_by_symbol)
        if trading_signal.entry_symbol in quotes_by_symbol:
            trading_signal.entry_price = quotes_by_symbol[trading_signal.entry_symbol].get("current_price")

        processing_time_ms = (time.time() - started) * 1000
        restored_model_inputs = sentiment_service.build_model_input_debug(
            posts=posts,
            price_context=price_context,
            market_validation=market_validation,
            symbols=symbols,
            prompt_overrides=prompt_overrides,
            web_context_by_symbol=web_context_by_symbol,
            web_items_by_symbol=web_items_by_symbol,
        )

        response = AnalysisResponse(
            request_id=rerun_request_id,
            timestamp=datetime.now(timezone.utc),
            symbols_analyzed=symbols,
            posts_scraped=len(posts),
            sentiment_scores={
                symbol: SentimentScore(
                    market_bluster=float(sentiment.get("bluster_score", 0.0) or 0.0),
                    policy_change=float(sentiment.get("policy_score", 0.0) or 0.0),
                    confidence=float(sentiment.get("confidence", 0.5) or 0.5),
                    reasoning=str(sentiment.get("reasoning", "") or ""),
                )
                for symbol, sentiment in sentiment_results.items()
            },
            aggregated_sentiment=_aggregate_sentiment(sentiment_results),
            trading_signal=trading_signal,
            blue_team_signal=blue_team_signal,
            market_validation=market_validation,
            red_team_review=red_team_review,
            red_team_debug=red_team_debug,
            stage_metrics={key: StageMetric.model_validate(value) for key, value in stage_metrics.items()},
            model_inputs=restored_model_inputs,
            ingestion_trace=IngestionTraceDebug.model_validate(snapshot.get("ingestion_trace") or {}) if snapshot.get("ingestion_trace") else None,
            backtest_results=None,
            processing_time_ms=processing_time_ms,
            status="SUCCESS",
        )

        rerun_secret_trace = dict(saved_secret_trace or {})
        rerun_secret_trace["request_id"] = rerun_request_id
        rerun_secret_trace["models"] = {
            **dict((saved_secret_trace or {}).get("models") or {}),
            "active_model": effective_model,
            "extraction_model": rerun_extraction or "",
            "reasoning_model": rerun_reasoning or "",
            "risk_profile": snapshot_risk,
        }
        rerun_secret_trace["blue_team_signal"] = blue_team_signal.model_dump(mode="json") if blue_team_signal else {}
        rerun_secret_trace["trading_signal"] = trading_signal.model_dump(mode="json") if trading_signal else {}
        rerun_secret_trace["red_team_review"] = red_team_review.model_dump(mode="json") if red_team_review else {}
        rerun_secret_trace["red_team_debug"] = red_team_debug.model_dump(mode="json") if red_team_debug else {}

        persistence_service.save_analysis_result(
            db=db,
            request_id=rerun_request_id,
            response=response,
            quotes_by_symbol=quotes_by_symbol,
            posts=posts,
            model_name=effective_model,
            prompt_overrides=prompt_overrides,
            dataset_snapshot=snapshot,
            extraction_model=rerun_extraction or "",
            reasoning_model=rerun_reasoning or "",
            risk_profile=snapshot_risk,
            secret_trace=rerun_secret_trace,
            sentiment_results=sentiment_results,
            per_symbol_counts=per_symbol_counts,
            price_context=price_context,
        )
        mark_analysis_completed(db, rerun_request_id)
        record_analysis_result(
            status="success",
            request_id=rerun_request_id,
            duration_ms=processing_time_ms,
            active_model=effective_model,
        )
        return response
    except Exception as exc:
        record_analysis_result(
            status="failed",
            request_id=rerun_request_id,
            active_model=effective_model,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Snapshot rerun failed",
                "message": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


def _analysis_services(db: Session) -> Dict[str, Any]:
    config = get_or_create_app_config(db)
    price_cache = get_price_cache_service()
    # Read DB overrides for strategy feature toggles (null = use logic_config.json default)
    _ce = getattr(config, "continuous_entry_enabled", None)
    _ra = getattr(config, "regime_adaptation_enabled", None)
    _hd = getattr(config, "hold_decay_enabled", None)
    return {
        "config": config,
        "sentiment": SentimentService(price_cache=price_cache, logic_config=_L),
        "signal": SignalService(logic_config=_L, continuous_entry_enabled=_ce, regime_adaptation_enabled=_ra, hold_decay_enabled=_hd),
        "materiality": MaterialityService(logic_config=_L),
        "hysteresis": HysteresisService(logic_config=_L),
        "persistence": PersistenceService(logic_config=_L),
        "backtest": BacktestService(logic_config=_L),
        "market": MarketDataService(price_cache=price_cache, logic_config=_L),
        "pipeline": PipelineService(db=db, price_cache=price_cache, logic_config=_L, continuous_entry_enabled=_ce, regime_adaptation_enabled=_ra, hold_decay_enabled=_hd),
    }


def _apply_request_defaults(request: AnalysisRequest, config: Any) -> AnalysisRequest:
    symbols = request.symbols or config.tracked_symbols or ["USO", "IBIT", "QQQ", "SPY"]
    return AnalysisRequest(
        symbols=[str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()],
        max_posts=request.max_posts or config.max_posts,
        include_backtest=False,
        lookback_days=request.lookback_days or config.lookback_days,
    )


def _analysis_timeout_seconds(config: Any) -> int:
    configured = getattr(config, "analysis_timeout_seconds", None)
    env_value = os.getenv("ANALYSIS_TIMEOUT_SECONDS") or os.getenv("ANALYSIS_MAX_SECONDS")
    for value in (configured, env_value, 900):
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return seconds
    return 900


def _restore_snapshot_posts(posts: List[Dict[str, Any]]) -> List[Any]:
    restored: List[Any] = []
    for post in posts:
        restored.append(
            SimpleNamespace(
                source=post.get("source"),
                feed_name=post.get("feed_name"),
                author=post.get("author"),
                title=post.get("title", ""),
                summary=post.get("summary", ""),
                content=post.get("content", ""),
                keywords=list(post.get("keywords") or []),
            )
        )
    return restored


def _restore_snapshot_quotes(quotes_by_symbol: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    restored: Dict[str, Dict[str, Any]] = {}
    for symbol, quote in (quotes_by_symbol or {}).items():
        normalized = dict(quote or {})
        timestamp = normalized.get("timestamp")
        if isinstance(timestamp, str):
            try:
                normalized["timestamp"] = datetime.fromisoformat(timestamp)
            except ValueError:
                pass
        restored[symbol] = normalized
    return restored


def _aggregate_sentiment(sentiment_results: Dict[str, Dict[str, Any]]) -> Optional[SentimentScore]:
    if not sentiment_results:
        return None
    avg_bluster = sum(float(r.get("bluster_score", 0.0) or 0.0) for r in sentiment_results.values()) / len(sentiment_results)
    avg_policy = sum(float(r.get("policy_score", 0.0) or 0.0) for r in sentiment_results.values()) / len(sentiment_results)
    avg_confidence = sum(float(r.get("confidence", 0.0) or 0.0) for r in sentiment_results.values()) / len(sentiment_results)
    representative_reasoning = next(
        ((result.get("reasoning") or "").strip() for result in sentiment_results.values() if (result.get("reasoning") or "").strip()),
        "Aggregated across all analyzed sources",
    )
    return SentimentScore(
        market_bluster=avg_bluster,
        policy_change=avg_policy,
        confidence=avg_confidence,
        reasoning=representative_reasoning,
    )


def _normalize_signal_payload(signal: Any) -> Optional[Dict[str, Any]]:
    if signal is None:
        return None
    if isinstance(signal, dict):
        return signal
    if hasattr(signal, "model_dump"):
        return signal.model_dump(mode="json")
    return {
        "signal_type": str(getattr(signal, "signal_type", "HOLD") or "HOLD"),
        "confidence_score": float(getattr(signal, "confidence_score", 0.0) or 0.0),
        "entry_symbol": str(getattr(signal, "entry_symbol", "") or ""),
        "recommendations": list(getattr(signal, "recommendations", None) or []),
    }


def _normalize_previous_state(previous_state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not previous_state:
        return None
    response = previous_state.get("response")
    if response is None:
        return previous_state
    if isinstance(response, dict):
        return previous_state

    sentiment_scores: Dict[str, Dict[str, Any]] = {}
    for symbol, score in (getattr(response, "sentiment_scores", {}) or {}).items():
        if isinstance(score, dict):
            sentiment_scores[symbol] = score
            continue
        sentiment_scores[symbol] = {
            "market_bluster": float(getattr(score, "market_bluster", 0.0) or 0.0),
            "policy_change": float(getattr(score, "policy_change", 0.0) or 0.0),
            "confidence": float(getattr(score, "confidence", 0.0) or 0.0),
            "reasoning": str(getattr(score, "reasoning", "") or ""),
        }

    normalized_response = {
        "posts_scraped": int(getattr(response, "posts_scraped", 0) or 0),
        "sentiment_scores": sentiment_scores,
        "blue_team_signal": _normalize_signal_payload(getattr(response, "blue_team_signal", None)),
        "trading_signal": _normalize_signal_payload(getattr(response, "trading_signal", None)),
    }
    normalized = dict(previous_state)
    normalized["response"] = normalized_response
    return normalized


def _build_symbol_specific_news_context(
    posts: List[Any],
    symbol: str,
    fallback: str,
    proxy_terms: Optional[List[str]] = None,
) -> str:
    sentiment_service = SentimentService(price_cache=get_price_cache_service(), logic_config=_L)
    return sentiment_service._build_symbol_specific_news_context(
        posts=posts,
        symbol=symbol,
        fallback=fallback,
        proxy_terms=proxy_terms,
    )


def _generate_trading_signal(
    sentiment_results: Dict[str, Dict],
    quotes_by_symbol: Optional[Dict[str, Dict[str, Any]]] = None,
    risk_profile: str = "standard",
    previous_signal: Optional[TradingSignal] = None,
    stability_mode: str = "normal",
    entry_threshold_override: Optional[float] = None,
    price_context: Optional[Dict[str, Any]] = None,
) -> TradingSignal:
    signal_service = SignalService(logic_config=_L)
    return signal_service.generate_trading_signal(
        sentiment_results=sentiment_results,
        quotes_by_symbol=quotes_by_symbol,
        risk_profile=risk_profile,
        previous_signal=previous_signal,
        stability_mode=stability_mode,
        entry_threshold_override=entry_threshold_override,
        price_context=price_context,
    )


def _material_change_gate(
    *,
    symbols: List[str],
    posts_count: int,
    sentiment_results: Dict[str, Dict[str, Any]],
    price_context: Dict[str, Any],
    quotes_by_symbol: Dict[str, Dict[str, Any]],
    previous_state: Optional[Dict[str, Any]],
    candidate_signal: Optional[TradingSignal],
    min_posts_delta: Optional[int] = None,
    min_sentiment_delta: Optional[float] = None,
    per_symbol_counts: Optional[Dict[str, int]] = None,
    db: Optional[Any] = None,
) -> bool:
    materiality_service = MaterialityService(logic_config=_L)
    normalized_previous_state = _normalize_previous_state(previous_state)
    return materiality_service.material_change_gate(
        db=db,
        symbols=symbols,
        posts_count=posts_count,
        sentiment_results=sentiment_results,
        price_context=price_context,
        quotes_by_symbol=quotes_by_symbol,
        previous_state=normalized_previous_state,
        candidate_signal=candidate_signal,
        min_posts_delta=min_posts_delta,
        min_sentiment_delta=min_sentiment_delta,
        per_symbol_counts=per_symbol_counts,
    )


async def run_analysis_for_pending_articles(
    *,
    db: Session,
    symbols: List[str],
    article_ids: Optional[List[int]] = None,
    trigger_source: str = "worker",
) -> Optional[AnalysisResponse]:
    services = _analysis_services(db)
    config = services["config"]
    pipeline: PipelineService = services["pipeline"]
    request = AnalysisRequest(
        symbols=symbols,
        max_posts=max(1, len(article_ids or []) or 50),
        include_backtest=False,
        lookback_days=14,
    )
    try:
        return await pipeline.run(
            request=_apply_request_defaults(request, config),
            db=db,
            config=config,
            prompt_overrides=config.symbol_prompt_overrides or {},
            article_ids=article_ids,
            trigger_source=trigger_source,
        )
    except Exception as exc:
        print(f"Skipping {trigger_source} analysis trigger: {exc}")
        return None


async def _pre_ingest_stream(
    db: Session,
    request: AnalysisRequest,
    config: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[str, None]:
    """Pull RSS feeds and market context, emitting verbose SSE diagnostics to the live feed."""
    # 1. Build feed map (excluding Yahoo Finance RSS feeds)
    yahoo_symbols = [str(sym).strip().upper() for sym in (request.symbols or []) if str(sym).strip()]
    merged_feeds = build_enabled_rss_feed_map(config)
    merged_labels = build_enabled_rss_feed_labels(config)
    feed_count = len(merged_feeds) + len(yahoo_symbols)
    configured_count = len(merged_feeds)
    yahoo_count = len(yahoo_symbols)

    _msg = f'Loading {feed_count} feeds ({configured_count} configured RSS + {yahoo_count} Yahoo Finance symbols)...'
    print(f"[stream] {_msg}")
    yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"
    _msg = 'Pulling RSS feeds and market context'
    print(f"[stream] Phase 0/4: {_msg}")
    yield f"data: {json.dumps({'type': 'phase', 'phase': 0, 'label': _msg}, default=str)}\n\n"

    # 2. Parse feeds
    articles = []
    try:
        from services.data_ingestion.parser import RSSFeedParser
        import asyncio as _asyncio
        parser = RSSFeedParser(feeds=merged_feeds, feed_labels=merged_labels)
        
        # Parse RSS feeds
        rss_articles = await _asyncio.to_thread(parser.parse_feeds)
        articles.extend(rss_articles)
        
        # Fetch Yahoo Finance news
        if yahoo_symbols:
            yahoo_articles = await _asyncio.to_thread(parser.fetch_yahoo_finance_news, yahoo_symbols)
            articles.extend(yahoo_articles)

        _msg = f'Fetched {len(articles)} raw articles ({len(rss_articles)} RSS + {len(yahoo_articles) if yahoo_symbols else 0} Yahoo Finance)'
        print(f"[stream] {_msg}")
        yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"
        for article in articles:
            source = str(getattr(article, "source", "") or "Unknown").strip() or "Unknown"
            title = str(getattr(article, "title", "") or "").strip()
            summary = str(getattr(article, "summary", "") or getattr(article, "content", "") or "").strip()
            keywords = list(getattr(article, "keywords", None) or [])
            _art_msg = f"  [{source}] {title}"
            print(f"[stream] {_art_msg}")
            yield f"data: {json.dumps({'type': 'article', 'source': source, 'title': title, 'description': summary[:500], 'keywords': keywords}, default=str)}\n\n"
    except Exception as exc:
        _msg = f'Feed parse error: {exc}'
        print(f"[stream] {_msg}")
        yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"
        articles = []
        return

    if not articles:
        _msg = '⚠ No articles fetched from RSS feeds — pipeline will use snapshot fallback'
        print(f"[stream] {_msg}")
        yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"

    # 3. Stage 0 filter with verbose per-article output
    from services.data_ingestion.worker import (
        _matches_stage0_filter,
        check_fast_lane,
        _resolve_fast_lane_symbols,
        normalize_text_for_matching,
        _load_symbol_relevance_terms,
        expand_proxy_terms_for_matching,
        _upsert_scraped_article,
        _utc_now,
        _coerce_utc,
    )
    from database.models import ScrapedArticle
    from services.app_config import get_or_create_app_config

    config = get_or_create_app_config(db)
    tracked_symbols = [str(s).upper().strip() for s in (config.tracked_symbols or []) if str(s).strip()]
    company_aliases = {
        str(k).upper(): str(v).strip()
        for k, v in (getattr(config, "symbol_company_aliases", None) or {}).items()
        if str(v).strip()
    }
    yahoo_source_labels = {"Yahoo Finance"}

    # Filter
    kept = []
    filtered_out = 0
    symbol_relevance = _load_symbol_relevance_terms()

    for article in articles:
        link = str(article.link or "").strip()
        if not link:
            filtered_out += 1
            continue
        if article.source in yahoo_source_labels:
            kept.append(article)
            continue
        if _matches_stage0_filter(article, tracked_symbols, company_aliases):
            kept.append(article)
        else:
            filtered_out += 1

    _msg = f'Stage 0 filter: {len(kept)}/{len(articles)} articles passed ({filtered_out} filtered out)'
    print(f"[stream] {_msg}")
    yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"

    # Show matched relevance terms per symbol
    all_terms = expand_proxy_terms_for_matching(
        [t for terms in symbol_relevance.values() for t in terms]
        + ["federal reserve", "fed", "rate cut", "rate hike", "fomc", "cpi", "inflation", "jobs report", "payrolls", "tariff", "trade war", "sanctions", "opec", "export controls", "emergency order"]
    )
    _msg = f'Stage 0 terms: {len(all_terms)} relevance+policy terms active'
    print(f"[stream] {_msg}")
    yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"

    # Store articles in DB queue (upsert, bypass duplicates silently)
    stored = 0
    dupes = 0
    fast_lane_ids = []
    fast_lane_syms = []
    all_article_ids: List[int] = []
    if metadata is not None:
        metadata["all_article_ids"] = all_article_ids

    kept.sort(key=lambda a: _coerce_utc(getattr(a, "published_date", None)) or _utc_now(), reverse=True)
    session = db

    for article in kept[:50]:  # cap at 50 for pre-ingest
        fallback = " ".join(p for p in [article.summary or "", article.content or "", article.title or ""] if p)
        try:
            from services.data_ingestion.worker import fetch_article_text
            full_content = await fetch_article_text(article.link, fallback_text=fallback)
        except Exception:
            full_content = fallback

        summary_blob = " ".join([article.title or "", article.summary or "", full_content or ""])
        fast_hit = check_fast_lane(summary_blob)
        row, is_new = _upsert_scraped_article(session, article, full_content, fast_hit)
        session.commit()
        all_article_ids.append(int(row.id))
        if is_new:
            stored += 1
        else:
            dupes += 1
        if fast_hit:
            fast_lane_ids.append(int(row.id))
            fast_lane_syms.extend(_resolve_fast_lane_symbols(summary_blob, tracked_symbols))

    _msg = f'Queue updated: {stored} new + {dupes} duplicates stored'
    print(f"[stream] {_msg}")
    yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"

    # Pending count
    pending = session.query(ScrapedArticle).filter(ScrapedArticle.processed.is_(False)).count()
    _msg = f'DB queue: {pending} unprocessed articles ready for analysis'
    print(f"[stream] {_msg}")
    yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"


@router.post(
    "/analyze/stream",
    summary="Run full analysis pipeline with real-time progress",
    tags=["Analysis"]
)
async def analyze_market_stream(
    request: AnalysisRequest,
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db)
):
    """SSE endpoint streaming heartbeat comments and the final result."""

    async def generate() -> AsyncGenerator[str, None]:
        services = _analysis_services(db)
        config = services["config"]
        pipeline: PipelineService = services["pipeline"]
        effective_request = _apply_request_defaults(request, config)
        prompt_overrides = config.symbol_prompt_overrides or {}
        timeout_seconds = _analysis_timeout_seconds(config)
        SentimentEngine.configure_parallelism(int(getattr(config, "ollama_parallel_slots", 1) or 1))
        backend = str(getattr(config, "inference_backend", "ollama") or "ollama")
        print(f"Router → SentimentEngine.set_backend({backend!r})")
        SentimentEngine.set_backend(backend)
        run_marker = str(uuid.uuid4())
        run_marked = False

        try:
            try:
                mark_analysis_started(db, run_marker)
                run_marked = True
            except Exception:
                run_marked = False
            # Prime upstream/proxy buffers so the browser gets immediate stream bytes.
            yield f": {' ' * 2048}\n\n"
            try:
                ollama_status = get_ollama_status()
                runtime_model = str(ollama_status.get("active_model") or "").strip() or "unknown model"
            except Exception:
                runtime_model = "unknown model"

            configured_extract = str(getattr(config, "extraction_model", "") or "").strip()
            configured_reason = str(getattr(config, "reasoning_model", "") or "").strip()
            configured_pipeline_model = configured_reason or configured_extract
            if configured_extract and configured_reason:
                configured_label = f"{configured_extract} → {configured_reason}"
            else:
                configured_label = configured_pipeline_model

            if configured_label:
                log_message = (
                    f"Ollama reachable — runtime: {runtime_model}; pipeline: {configured_label}"
                )
            else:
                log_message = f"Ollama reachable — runtime: {runtime_model}"
            print(f"[stream] {log_message}")
            yield f"data: {json.dumps({'type': 'log', 'message': log_message}, default=str)}\n\n"
            symbols_label = ", ".join(effective_request.symbols)
            _msg = f'Fetching real-time price data for {symbols_label}...'
            print(f"[stream] {_msg}")
            yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"
            _msg = 'Phase 0/4: Pulling RSS feeds and market context...'
            print(f"[stream] {_msg}")
            yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"
            _msg = 'Pulling RSS feeds and market context'
            print(f"[stream] Phase 0/4: {_msg}")
            yield f"data: {json.dumps({'type': 'phase', 'phase': 0, 'label': _msg}, default=str)}\n\n"

            # Pre-analysis RSS ingestion — ensures we always have fresh articles even after DB reset
            ingestion_result: Dict[str, Any] = {}
            try:
                async for chunk in _pre_ingest_stream(db, effective_request, config, metadata=ingestion_result):
                    yield chunk
            except Exception:
                pass
            _msg = 'Phase 1/4: Ingesting queued articles and market context...'
            print(f"[stream] {_msg}")
            yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"
            _label = 'Ingesting queued articles and market context'
            print(f"[stream] Phase 1/4: {_label}")
            yield f"data: {json.dumps({'type': 'phase', 'phase': 1, 'label': _label}, default=str)}\n\n"

            # Always load all unprocessed articles from the DB queue rather than
            # restricting to only newly ingested ones. The pre-ingest step above
            # ensures fresh articles exist in the queue, but most will be duplicates
            # of what the background scheduler already stored. Restricting to only
            # new article IDs starves the pipeline (e.g. 1 new article when 194 are
            # available), causing all symbols to analyze the same article.
            _msg = 'Loading all unprocessed articles from DB queue for analysis'
            print(f"[stream] {_msg}")
            yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"

            task = asyncio.create_task(
                pipeline.run(
                    request=effective_request,
                    db=db,
                    config=config,
                    prompt_overrides=prompt_overrides,
                    article_ids=None,
                    trigger_source="stream",
                )
            )
            started_at = time.monotonic()
            heartbeat_count = 0
            while not task.done():
                elapsed = int(time.monotonic() - started_at)
                remaining = timeout_seconds - elapsed
                if remaining <= 0:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass
                    timeout_msg = f"Analysis timed out after {timeout_seconds}s"
                    print(f"[stream] {timeout_msg}")
                    yield f"data: {json.dumps({'type': 'log', 'message': timeout_msg}, default=str)}\n\n"
                    yield f"data: {json.dumps({'type': 'error', 'message': timeout_msg})}\n\n"
                    return

                done, _ = await asyncio.wait({task}, timeout=min(20, remaining))
                if not done:
                    heartbeat_count += 1
                    if heartbeat_count == 1:
                        _msg = 'Phase 2/4: Running symbol specialists...'
                        print(f"[stream] {_msg}")
                        yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"
                        _label = 'Running symbol specialists'
                        print(f"[stream] Phase 2/4: {_label}")
                        yield f"data: {json.dumps({'type': 'phase', 'phase': 2, 'label': _label}, default=str)}\n\n"
                    elif heartbeat_count % 3 == 0:
                        _msg = f'Still analyzing ({elapsed}s elapsed)'
                        print(f"[stream] {_msg}")
                        yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"
                    yield ": stage2-heartbeat\n\n"
            response = task.result()
            _msg = 'Phase 4/4: Persisting results and snapshots...'
            print(f"[stream] {_msg}")
            yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"
            _label = 'Persisting results and snapshots'
            print(f"[stream] Phase 4/4: {_label}")
            yield f"data: {json.dumps({'type': 'phase', 'phase': 4, 'label': _label}, default=str)}\n\n"

            # ── Verbose diagnostics ─────────────────────────────────────────
            # Articles that were ingested and passed Stage 0
            model_inputs = response.model_inputs
            if model_inputs and model_inputs.articles:
                _msg = f'▶ {response.posts_scraped} articles ingested — {len(model_inputs.articles)} selected for analysis'
                print(f"[stream] {_msg}")
                yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"
                for art in model_inputs.articles:
                    art_desc = getattr(art, "description", None) or getattr(art, "summary", "") or ""
                    art_kw = getattr(art, "keywords", None) or []
                    _art_msg = f"  [{art.source or 'Unknown'}] {art.title or ''}"
                    print(f"[stream] {_art_msg}")
                    yield f"data: {json.dumps({'type': 'article', 'source': art.source or 'Unknown', 'title': art.title or '', 'description': str(art_desc)[:500], 'keywords': list(art_kw)}, default=str)}\n\n"
            else:
                _msg = f'▶ {response.posts_scraped} articles ingested (model_inputs empty — pipeline used snapshot fallback)'
                print(f"[stream] {_msg}")
                yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"

            # Per-symbol Stage 1 + Stage 2 diagnostic summary
            response_dict = response.model_dump(mode="json")
            stage2_details = ((response_dict.get("stage_metrics") or {}).get("stage2") or {}).get("details") or {}
            posts_by_sym_counts = stage2_details.get("posts_by_symbol_counts") or {}
            exposure_hints = stage2_details.get("exposure_hints_by_symbol") or {}
            kw_by_sym = stage2_details.get("keyword_terms_by_symbol") or {}

            for sym, score in (response_dict.get("sentiment_scores") or {}).items():
                count = posts_by_sym_counts.get(sym)
                hint = exposure_hints.get(sym, "")
                terms = kw_by_sym.get(sym) or []
                conf = round(float(score.get("confidence") or 0) * 100)
                exposure = str(score.get("reasoning") or "").split(".")[0]  # first sentence
                count_str = f"{count} matched articles" if count is not None else "article count unknown"
                kw_str = f" | keywords: {', '.join(terms[:6])}" if terms else ""
                hint_str = f" ({hint})" if hint else ""
                _msg = f'▶ {sym}: {count_str}{hint_str} → {conf}% confidence{kw_str}'
                print(f"[stream] {_msg}")
                yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"

            _msg = '▶ Analysis complete'
            print(f"[stream] {_msg}")
            yield f"data: {json.dumps({'type': 'log', 'message': _msg}, default=str)}\n\n"
            yield f"data: {json.dumps({'type': 'result', 'data': response_dict}, default=str)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, default=str)}\n\n"
            return
        except Exception as exc:
            _msg = f'Error: {exc}'
            print(f"[stream] {_msg}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, default=str)}\n\n"
        finally:
            if run_marked:
                try:
                    mark_analysis_completed(db, run_marker)
                except Exception:
                    pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/analyze",
    response_model=AnalysisResponse,
    summary="Run full sentiment analysis pipeline",
    tags=["Analysis"]
)
async def analyze_market(
    request: AnalysisRequest,
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db)
):
    services = _analysis_services(db)
    config = services["config"]
    pipeline: PipelineService = services["pipeline"]
    effective_request = _apply_request_defaults(request, config)
    timeout_seconds = _analysis_timeout_seconds(config)
    SentimentEngine.configure_parallelism(int(getattr(config, "ollama_parallel_slots", 1) or 1))
    SentimentEngine.set_backend(str(getattr(config, "inference_backend", "ollama") or "ollama"))
    run_marker = str(uuid.uuid4())
    run_marked = False

    try:
        try:
            mark_analysis_started(db, run_marker)
            run_marked = True
        except Exception:
            run_marked = False
        return await asyncio.wait_for(
            pipeline.run(
                request=effective_request,
                db=db,
                config=config,
                prompt_overrides=config.symbol_prompt_overrides or {},
                article_ids=None,
                trigger_source="api",
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "error": "Analysis timed out",
                "message": f"Analysis exceeded {timeout_seconds} seconds",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Analysis failed",
                "message": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    finally:
        if run_marked:
            try:
                mark_analysis_completed(db, run_marker)
            except Exception:
                pass


@router.get("/paper-trading/summary", tags=["Paper Trading"])
async def get_paper_trading_summary(db: Session = Depends(get_db)):
    from services.paper_trading import get_summary
    return get_summary(db)


@router.get("/paper-trading/closed-trades", tags=["Paper Trading"])
async def get_paginated_closed_trades(
    offset: int = Query(default=0, ge=0, description="Number of trades to skip"),
    limit: int = Query(default=20, ge=1, le=100, description="Max trades to return"),
    db: Session = Depends(get_db),
):
    """Return older closed trades for pagination. Trades older than those returned by /summary."""
    from database.models import PaperTrade
    from services.paper_trading import _safe_utc, _utc_iso, _directional_pnl, _directional_return_pct
    from datetime import timedelta

    now_utc = _safe_utc(datetime.now(timezone.utc))
    cutoff_date = now_utc - timedelta(days=4)

    # Get the oldest trade from the "last 4 days" set (the one with the most recent exited_at that's still >= cutoff)
    # Then skip `offset` more trades and return `limit` additional trades
    base_query = db.query(PaperTrade).filter(
        PaperTrade.exited_at.isnot(None),
        PaperTrade.exited_at < cutoff_date,
    ).order_by(PaperTrade.exited_at.desc())

    # Skip offset trades
    trades = base_query.offset(offset).limit(limit).all()

    closed_trades = []
    for t in trades:
        pnl = _directional_pnl(t.signal_type, t.entry_price, float(t.exit_price or t.entry_price), t.amount)
        pnl_pct = _directional_return_pct(t.signal_type, t.entry_price, float(t.exit_price or t.entry_price))
        closed_trades.append({
            "id": t.id,
            "underlying": t.underlying,
            "execution_ticker": t.execution_ticker,
            "signal_type": t.signal_type,
            "leverage": t.leverage,
            "amount": t.amount,
            "shares": t.shares,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "entered_at": _utc_iso(t.entered_at),
            "exited_at": _utc_iso(t.exited_at),
            "realized_pnl": round(pnl, 4),
            "realized_pnl_pct": round(pnl_pct, 4),
            "market_session": t.market_session,
            "conviction_level": t.conviction_level,
            "trading_type": t.trading_type,
            "holding_period_hours": t.holding_period_hours,
            "close_reason": t.close_reason,
        })

    # Also return total count of older trades for UI
    total_count = db.query(PaperTrade).filter(
        PaperTrade.exited_at.isnot(None),
        PaperTrade.exited_at < cutoff_date,
    ).count()

    return {
        "closed_trades": closed_trades,
        "total_available": total_count,
        "offset": offset,
        "limit": limit,
    }


@router.post("/paper-trading/expire-check", tags=["Paper Trading"])
async def paper_trading_expire_check(
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db)
):
    """Close any open positions whose conviction window has expired. Safe to call at any time."""
    from services.paper_trading import close_expired_positions
    closed = close_expired_positions(db)
    return {"closed": len(closed), "positions": closed}


@router.delete("/paper-trading/reset", tags=["Paper Trading"])
async def reset_paper_trading(
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    from database.models import PaperTrade
    deleted = db.query(PaperTrade).delete()
    db.commit()
    return {"deleted": deleted, "message": "Paper trading history cleared"}


@router.post("/paper-trading/{trade_id}/close", tags=["Paper Trading"])
async def manual_close_trade(
    trade_id: int, 
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db)
):
    from database.models import PaperTrade
    from services.data_ingestion.yfinance_client import PriceClient
    from datetime import datetime, timezone
    
    trade = db.query(PaperTrade).filter(PaperTrade.id == trade_id).first()
    if not trade or trade.exited_at is not None:
        raise HTTPException(status_code=400, detail="Trade not found or already closed")
        
    # Get current market price
    price_client = PriceClient()
    try:
        quote = price_client.get_realtime_quote(trade.execution_ticker)
        current_price = float((quote or {}).get("current_price") or trade.entry_price or 0.0)
    except Exception:
        current_price = float(trade.entry_price or 0.0)
        
    now = datetime.now(timezone.utc)
    
    from services.paper_trading import _close_position, _dispatch_alpaca_orders
    _close_position(trade, current_price, now, db, reason="Manual Close")
    
    _alpaca_pending = [(trade, "close")]
    db.commit()
    
    try:
        from services.app_config import get_or_create_app_config
        config = get_or_create_app_config(db)
        _dispatch_alpaca_orders(db, _alpaca_pending, config)
    except Exception:
        pass
        
    return {"status": "success", "closed_price": current_price}


@router.get("/analysis-debug/latest", tags=["System"])
async def get_latest_analysis_debug(
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    latest = (
        db.query(AnalysisResult)
        .order_by(AnalysisResult.timestamp.desc())
        .first()
    )
    if not latest:
        raise HTTPException(status_code=404, detail="No saved analysis runs found")

    metadata = latest.run_metadata or {}
    snapshot = metadata.get("dataset_snapshot") or {}
    secret_trace = metadata.get("secret_trace") or snapshot.get("secret_trace") or {}
    return {
        "request_id": latest.request_id,
        "timestamp": _utc_iso(latest.timestamp),
        "model_name": metadata.get("model_name") or "",
        "risk_profile": metadata.get("risk_profile") or "",
        "processing_time_ms": metadata.get("processing_time_ms") or 0,
        "signal": latest.signal or {},
        "sentiment_data": latest.sentiment_data or {},
        "dataset_snapshot": snapshot,
        "secret_trace": secret_trace,
    }


@router.get("/pnl", tags=["Analysis"])
async def get_pnl_summary(db: Session = Depends(get_db)):
    """Return persisted recommendation trades and resolved forward P&L snapshots."""
    tracker = PnLTracker()
    return tracker.get_summary(db)
