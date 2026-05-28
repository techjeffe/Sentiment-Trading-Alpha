"""
PersistenceService — database persistence layer for analysis results.

Encapsulates _save_analysis_result, _save_analysis_and_trades,
_build_dataset_snapshot, _serialize_snapshot_posts, _prune_saved_analyses,
and all related snapshot/persistence helpers from the original router.

Data Scoping Note:
  - All query operations are request-scoped (DB session passed explicitly).
  - Snapshot data is frozen per-request — no cross-request leakage.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from database.models import AnalysisResult, PaperTrade, ScrapedArticle, Trade
from database.models import TradeExecution, TradeSnapshot, TradingSignal as TradingSignalModel
from schemas.analysis import AnalysisResponse
from services.data_ingestion.worker import build_analysis_posts
from services.runtime_health import record_analysis_result
from services.paper_trading import process_signals as paper_process_signals
from services.pnl_tracker import persist_recommendation_trades
from services.remote_snapshot import trigger_remote_snapshot_delivery
from services.app_config import get_or_create_app_config, DEFAULT_SNAPSHOT_RETENTION_LIMIT


class PersistenceService:
    """Encapsulates analysis persistence and snapshot management."""

    def __init__(self, logic_config: dict[str, Any]) -> None:
        self._L = logic_config

    # ── Public API ───────────────────────────────────────────────────

    def save_analysis_result(
        self,
        db: Session,
        request_id: str,
        response: AnalysisResponse,
        quotes_by_symbol: Dict[str, Dict[str, Any]],
        posts: Optional[List[Any]] = None,
        model_name: str = "",
        prompt_overrides: Optional[Dict[str, str]] = None,
        dataset_snapshot: Optional[Dict[str, Any]] = None,
        extraction_model: str = "",
        reasoning_model: str = "",
        risk_profile: str = "moderate",
        secret_trace: Optional[Dict[str, Any]] = None,
        sentiment_results: Optional[Dict[str, Any]] = None,
        per_symbol_counts: Optional[Dict[str, int]] = None,
        price_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Save the full analysis result and trigger downstream hooks."""
        counts = per_symbol_counts or (
            self._count_symbol_articles(posts, list(response.symbols_analyzed or []))
            if posts else {}
        )
        self._save_analysis_result(
            db=db,
            request_id=request_id,
            response=response,
            quotes_by_symbol=quotes_by_symbol,
            posts=posts,
            model_name=model_name,
            prompt_overrides=prompt_overrides,
            dataset_snapshot=dataset_snapshot,
            extraction_model=extraction_model,
            reasoning_model=reasoning_model,
            risk_profile=risk_profile,
            secret_trace=secret_trace,
            sentiment_results=sentiment_results,
            per_symbol_counts=counts,
            price_context=price_context,
        )

    def load_saved_analysis_response(self, analysis: AnalysisResult) -> Optional[Dict[str, Any]]:
        """Reconstruct a full analysis response from a persisted AnalysisResult."""
        metadata = analysis.run_metadata or {}
        snapshot = metadata.get("dataset_snapshot") or {}
        sentiment_data = analysis.sentiment_data or {}
        signal_data = analysis.signal or {}
        backtest_data = analysis.backtest_results or {}

        sentiment_scores_payload = sentiment_data.get("sentiment_scores") or {}
        aggregated_payload = sentiment_data.get("aggregated_sentiment") or {}
        market_validation = sentiment_data.get("market_validation") or snapshot.get("market_validation") or {}

        sentiment_scores = {}
        for symbol, payload in sentiment_scores_payload.items():
            sentiment_scores[symbol] = {
                "market_bluster": float((payload or {}).get("market_bluster", 0.0) or 0.0),
                "policy_change": float((payload or {}).get("policy_change", 0.0) or 0.0),
                "confidence": float((payload or {}).get("confidence", 0.0) or 0.0),
                "reasoning": str((payload or {}).get("reasoning", "") or ""),
            }

        aggregated_sentiment = None
        if aggregated_payload:
            aggregated_sentiment = {
                "market_bluster": float(aggregated_payload.get("market_bluster", 0.0) or 0.0),
                "policy_change": float(aggregated_payload.get("policy_change", 0.0) or 0.0),
                "confidence": float(aggregated_payload.get("confidence", 0.0) or 0.0),
                "reasoning": str(aggregated_payload.get("reasoning", "") or ""),
            }

        trading_signal = {
            "signal_type": str(signal_data.get("signal_type", "HOLD") or "HOLD"),
            "confidence_score": float(signal_data.get("confidence_score", 0.0) or 0.0),
            "urgency": str(signal_data.get("urgency", "LOW") or "LOW"),
            "entry_symbol": str(signal_data.get("entry_symbol", "") or ""),
            "recommendations": list(signal_data.get("recommendations") or []),
            "conviction_level": str(signal_data.get("conviction_level", "LOW") or "LOW"),
            "holding_period_hours": int(signal_data.get("holding_period_hours", 2) or 2),
            "trading_type": str(signal_data.get("trading_type", "VOLATILE_EVENT") or "VOLATILE_EVENT"),
            "action_if_already_in_position": str(signal_data.get("action_if_already_in_position", "HOLD") or "HOLD"),
            "entry_price": signal_data.get("entry_price"),
            "stop_loss_pct": float(signal_data.get("stop_loss_pct", 2.0) or 2.0),
            "take_profit_pct": float(signal_data.get("take_profit_pct", 3.0) or 3.0),
            "position_size_usd": float(signal_data.get("position_size_usd", 1000.0) or 1000.0),
        }

        blue_signal_data = metadata.get("blue_team_signal") or {}
        blue_team_signal = None
        if blue_signal_data:
            blue_team_signal = {
                "signal_type": str(blue_signal_data.get("signal_type", "HOLD") or "HOLD"),
                "confidence_score": float(blue_signal_data.get("confidence_score", 0.0) or 0.0),
                "urgency": str(blue_signal_data.get("urgency", "LOW") or "LOW"),
                "entry_symbol": str(blue_signal_data.get("entry_symbol", "") or ""),
                "recommendations": list(blue_signal_data.get("recommendations") or []),
                "conviction_level": str(blue_signal_data.get("conviction_level", "LOW") or "LOW"),
                "holding_period_hours": int(blue_signal_data.get("holding_period_hours", 2) or 2),
                "trading_type": str(blue_signal_data.get("trading_type", "VOLATILE_EVENT") or "VOLATILE_EVENT"),
                "action_if_already_in_position": str(blue_signal_data.get("action_if_already_in_position", "HOLD") or "HOLD"),
                "entry_price": blue_signal_data.get("entry_price"),
                "stop_loss_pct": float(blue_signal_data.get("stop_loss_pct", 2.0) or 2.0),
                "take_profit_pct": float(blue_signal_data.get("take_profit_pct", 3.0) or 3.0),
                "position_size_usd": float(blue_signal_data.get("position_size_usd", 1000.0) or 1000.0),
            }

        backtest_results = None
        if backtest_data:
            backtest_results = {
                "total_return": float(backtest_data.get("total_return", 0.0) or 0.0),
                "win_rate": float(backtest_data.get("win_rate", 0.0) or 0.0),
                "max_drawdown": float(backtest_data.get("max_drawdown", 0.0) or 0.0),
                "sharpe_ratio": float(backtest_data.get("sharpe_ratio", 0.0) or 0.0),
                "total_trades": int(backtest_data.get("total_trades", 0) or 0),
                "lookback_days": int(backtest_data.get("lookback_days", snapshot.get("lookback_days", 14)) or 14),
            }

        red_team_payload = metadata.get("red_team_review") or {}

        return {
            "request_id": analysis.request_id,
            "timestamp": analysis.timestamp,
            "symbols_analyzed": list(metadata.get("symbols") or snapshot.get("symbols") or []),
            "posts_scraped": int(metadata.get("posts_scraped", 0) or 0),
            "sentiment_scores": sentiment_scores,
            "aggregated_sentiment": aggregated_sentiment,
            "trading_signal": trading_signal,
            "blue_team_signal": blue_team_signal,
            "market_validation": market_validation,
            "red_team_review": red_team_payload if red_team_payload else None,
            "stage_metrics": {
                key: value
                for key, value in (metadata.get("stage_metrics") or {}).items()
            },
            "backtest_results": backtest_results,
            "processing_time_ms": float(metadata.get("processing_time_ms", 0.0) or 0.0),
            "status": "SUCCESS",
        }

    def prune_saved_analyses(self, db: Session, keep_limit: int = 100) -> None:
        """Keep only the most recent saved analyses and delete related trade history for older ones."""
        normalized_limit = max(1, min(100, int(keep_limit)))
        stale_analyses = (
            db.query(AnalysisResult)
            .order_by(AnalysisResult.timestamp.desc())
            .offset(normalized_limit)
            .all()
        )
        if not stale_analyses:
            return

        stale_analysis_ids = [analysis.id for analysis in stale_analyses]
        stale_trades = db.query(Trade).filter(Trade.analysis_id.in_(stale_analysis_ids)).all()
        stale_trade_ids = [trade.id for trade in stale_trades]

        if stale_trade_ids:
            db.query(TradeExecution).filter(TradeExecution.trade_id.in_(stale_trade_ids)).delete(synchronize_session=False)
            db.query(TradeSnapshot).filter(TradeSnapshot.trade_id.in_(stale_trade_ids)).delete(synchronize_session=False)
            db.query(Trade).filter(Trade.id.in_(stale_trade_ids)).delete(synchronize_session=False)

        db.query(TradingSignalModel).filter(TradingSignalModel.analysis_id.in_(stale_analysis_ids)).delete(synchronize_session=False)
        db.query(AnalysisResult).filter(AnalysisResult.id.in_(stale_analysis_ids)).delete(synchronize_session=False)

    # ── Internal (private) ───────────────────────────────────────────────

    def _save_analysis_result(
        self,
        db: Session,
        request_id: str,
        response: AnalysisResponse,
        quotes_by_symbol: Dict[str, Dict[str, Any]],
        posts: Optional[List[Any]],
        model_name: str,
        prompt_overrides: Optional[Dict[str, str]],
        dataset_snapshot: Optional[Dict[str, Any]],
        extraction_model: str,
        reasoning_model: str,
        risk_profile: str,
        secret_trace: Optional[Dict[str, Any]],
        sentiment_results: Optional[Dict[str, Any]],
        per_symbol_counts: Dict[str, int],
        price_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            frozen_snapshot = dataset_snapshot or self._build_dataset_snapshot(
                response=response,
                posts=posts or [],
                quotes_by_symbol=quotes_by_symbol,
                model_name=model_name,
                prompt_overrides=prompt_overrides,
                extraction_model=extraction_model,
                reasoning_model=reasoning_model,
                risk_profile=risk_profile,
                secret_trace=secret_trace,
            )
            analysis = AnalysisResult(
                request_id=request_id,
                sentiment_data={
                    "sentiment_scores": {
                        symbol: {
                            "market_bluster": score.market_bluster,
                            "policy_change": score.policy_change,
                            "confidence": score.confidence,
                            "reasoning": score.reasoning,
                            "directional_score": (sentiment_results or {}).get(symbol, {}).get("directional_score"),
                            "signal_type": (sentiment_results or {}).get(symbol, {}).get("signal_type", "HOLD"),
                            "urgency": (sentiment_results or {}).get(symbol, {}).get("urgency", "LOW"),
                        }
                        for symbol, score in response.sentiment_scores.items()
                    },
                    "aggregated_sentiment": {
                        "market_bluster": response.aggregated_sentiment.market_bluster if response.aggregated_sentiment else 0,
                        "policy_change": response.aggregated_sentiment.policy_change if response.aggregated_sentiment else 0,
                        "confidence": response.aggregated_sentiment.confidence if response.aggregated_sentiment else 0
                    },
                    "market_validation": response.market_validation,
                },
                signal={
                    "signal_type": response.trading_signal.signal_type if response.trading_signal else "HOLD",
                    "confidence_score": response.trading_signal.confidence_score if response.trading_signal else 0,
                    "urgency": response.trading_signal.urgency if response.trading_signal else "LOW",
                    "entry_symbol": response.trading_signal.entry_symbol if response.trading_signal else "",
                    "recommendations": response.trading_signal.recommendations if response.trading_signal else [],
                },
                backtest_results={
                    "total_return": response.backtest_results.total_return if response.backtest_results else 0,
                    "sharpe_ratio": response.backtest_results.sharpe_ratio if response.backtest_results else 0
                } if response.backtest_results else None,
                run_metadata={
                    "symbols": response.symbols_analyzed,
                    "posts_scraped": response.posts_scraped,
                    "processing_time_ms": response.processing_time_ms,
                    "model_name": model_name,
                    "risk_profile": risk_profile,
                    "stage_metrics": {
                        key: value.model_dump(mode="json")
                        for key, value in (response.stage_metrics or {}).items()
                    },
                    "blue_team_signal": response.blue_team_signal.model_dump(mode="json") if response.blue_team_signal else None,
                    "red_team_review": response.red_team_review.model_dump(mode="json") if response.red_team_review else None,
                    "red_team_debug": response.red_team_debug.model_dump(mode="json") if response.red_team_debug else None,
                    "dataset_snapshot": frozen_snapshot,
                    "secret_trace": secret_trace or frozen_snapshot.get("secret_trace") or {},
                    "per_symbol_article_counts": per_symbol_counts or {},
                    "ramp_metadata": {
                        str(r.get("underlying_symbol") or r.get("symbol") or "").upper(): {
                            "ramp_threshold_bucket": r.get("ramp_threshold_bucket"),
                            "threshold_source": r.get("threshold_source"),
                            "fetch_latency_ms": r.get("fetch_latency_ms"),
                            "fetch_timeout_hit": r.get("fetch_timeout_hit"),
                            "ramp_promotion_enabled": r.get("ramp_promotion_enabled"),
                        }
                        for r in (response.trading_signal.recommendations or [])
                        if str(r.get("underlying_symbol") or r.get("symbol") or "").strip()
                    },
                }
            )
            db.add(analysis)
            db.flush()

            persist_recommendation_trades(
                db=db,
                analysis_id=analysis.id,
                request_id=request_id,
                response=response,
                quotes_by_symbol=quotes_by_symbol,
            )
            config = get_or_create_app_config(db)
            # Paper trading — auto-simulate $100 per signal during market hours
            try:
                recs_by_underlying = {}
                if response.trading_signal:
                    for r in (response.trading_signal.recommendations or []):
                        sym = (r.get("underlying_symbol") or "").upper()
                        if sym:
                            recs_by_underlying[sym] = r
                recs_for_paper = []
                _L = self._L
                _final_signal = response.trading_signal
                _cv = _L["conviction"]

                # Per-symbol conviction thresholds — use crazy overrides if applicable
                _is_crazy = str(risk_profile or "").strip().lower() == "crazy"
                _crazy_cv = _L.get("crazy", {}).get("conviction", {})
                _high_score_threshold = float(_crazy_cv.get("high_score_threshold", _cv.get("high_score_threshold", 0.5)))
                _high_confidence_threshold = float(_crazy_cv.get("high_confidence_threshold", _cv.get("high_confidence_threshold", 0.8)))
                if not _is_crazy:
                    _high_score_threshold = float(_cv.get("high_score_threshold", 0.5))
                    _high_confidence_threshold = float(_cv.get("high_confidence_threshold", 0.8))

                def _compute_symbol_conviction(sym: str, sym_signal_type: str) -> str:
                    """Compute per-symbol conviction from its own sentiment scores.
                    
                    Each symbol's conviction is independent of the portfolio-level signal.
                    HOLD signals always get LOW conviction.
                    Directional signals use the symbol's own directional_score and confidence.
                    Distribution target: ~25% HIGH, ~50% MEDIUM, ~25% LOW.
                    HIGH: confidence >= 0.80 (confidence-based).
                    LOW: weak directional signal AND low confidence.
                    MEDIUM: everything else.
                    """
                    if sym_signal_type == "HOLD":
                        return "LOW"
                    sym_result = (sentiment_results or {}).get(sym, {})
                    directional = float(sym_result.get("directional_score", 0.0) or 0.0)
                    confidence = float(sym_result.get("confidence", 0.0) or 0.0)
                    # HIGH: confidence-based (strong conviction regardless of score magnitude)
                    if confidence >= _high_confidence_threshold:
                        return "HIGH"
                    # LOW: weak directional signal AND low confidence
                    _low_dir_thresh = float(_cv.get("low_directional_threshold", 0.30))
                    _low_conf_thresh = float(_cv.get("low_confidence_threshold", 0.55))
                    if abs(directional) < _low_dir_thresh and confidence < _low_conf_thresh:
                        return "LOW"
                    return "MEDIUM"

                # Build paper trading entries from final recommendations
                covered_syms: set = set()
                for rec in recs_by_underlying.values():
                    underlying = str(rec.get("underlying_symbol") or "").upper()
                    if not underlying:
                        continue
                    covered_syms.add(underlying)
                    action = str(rec.get("action") or "").upper()
                    thesis = str(rec.get("thesis") or "").upper()
                    # Map final recommendation to signal_type using thesis field
                    if thesis == "LONG":
                        signal_type = "LONG"
                    elif thesis == "SHORT":
                        signal_type = "SHORT"
                    else:
                        signal_type = "HOLD"
                    _conviction = _compute_symbol_conviction(underlying, signal_type)
                    _trade_type = {"HIGH": "POSITION", "MEDIUM": "SWING", "LOW": "VOLATILE_EVENT"}.get(_conviction, "SWING")
                    # LOW conviction uses shorter holding windows
                    if _conviction == "LOW":
                        _hold_mins = _cv.get("low_holding_minutes", _cv["holding_minutes"]).get(_trade_type, 60)
                    else:
                        _hold_mins = _cv["holding_minutes"].get(_trade_type, 720)
                    _atr_pct = 0.0
                    if price_context:
                        _indicators = (price_context.get(f"technical_indicators_{underlying.lower()}") or {})
                        try:
                            _atr_pct = float(_indicators.get("atr_14_pct") or 0.0)
                        except (TypeError, ValueError):
                            _atr_pct = 0.0
                    recs_for_paper.append({
                        "underlying": underlying,
                        "execution_ticker": str(rec.get("symbol", underlying) or underlying).upper(),
                        "signal_type": signal_type,
                        "leverage": str(rec.get("leverage", "1x") or "1x"),
                        "conviction_level": _conviction,
                        "trading_type": _trade_type,
                        "holding_minutes": _hold_mins,
                        "atr_pct": _atr_pct,
                        "size_pct": str(rec.get("size_pct", "100.0") or "100.0"),
                    })

                # Add HOLD entries for any analyzed symbols that didn't get a final
                # recommendation — this ensures existing open positions for those
                # symbols get closed via process_signals orphan cleanup.
                for sym in (sentiment_results or {}):
                    sym_upper = sym.upper()
                    if sym_upper not in covered_syms:
                        recs_for_paper.append({
                            "underlying": sym_upper,
                            "execution_ticker": sym_upper,
                            "signal_type": "HOLD",
                            "leverage": "1x",
                            "conviction_level": "LOW",
                            "trading_type": "VOLATILE_EVENT",
                            "holding_minutes": _cv["holding_minutes"].get("VOLATILE_EVENT", 60),
                            "atr_pct": 0.0,
                        })
                if recs_for_paper:
                    _paper_actions = paper_process_signals(
                        db=db,
                        recommendations=recs_for_paper,
                        quotes_by_symbol=quotes_by_symbol,
                        request_id=request_id,
                        trade_amount=float(getattr(config, "paper_trade_amount", None) or 0) or None,
                    )
                    _skipped = [a for a in (_paper_actions or []) if a.get("action") == "skipped"]
                    if _skipped:
                        print(f"[paper] {len(_skipped)} signal(s) skipped this run: {[a.get('reason') for a in _skipped]}")
            except Exception as _pe:
                import traceback as _tb
                print(f"Paper trading hook error: {_pe}\n{_tb.format_exc()}")
            retention_limit = int(getattr(config, "snapshot_retention_limit", DEFAULT_SNAPSHOT_RETENTION_LIMIT))
            self.prune_saved_analyses(db, retention_limit)
            db.commit()
            trigger_remote_snapshot_delivery(request_id)
        except Exception as e:
            db.rollback()
            raise RuntimeError(f"Error saving analysis result: {e}") from e

    def _build_dataset_snapshot(
        self,
        response: AnalysisResponse,
        posts: List[Any],
        quotes_by_symbol: Dict[str, Dict[str, Any]],
        model_name: str,
        prompt_overrides: Optional[Dict[str, str]],
        extraction_model: str,
        reasoning_model: str,
        risk_profile: str,
        secret_trace: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        request_payload = getattr(response, "request_payload", None) or {}
        article_ids = list(getattr(response, "article_ids", None) or [])
        frozen_snapshot: Dict[str, Any] = {
            "request_id": response.request_id or "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_payload": request_payload,
            "prompt_overrides": dict(prompt_overrides or {}),
            "dataset": {
                "symbols_analyzed": list(response.symbols_analyzed or []),
                "article_ids": article_ids,
                "posts_scraped": int(response.posts_scraped or 0),
                "articles": self._serialize_snapshot_posts(response.request_id, posts) if posts else [],
                "quotes_by_symbol": self._restore_snapshot_quotes(quotes_by_symbol),
                "market_validation": response.market_validation or {},
                "model_name": model_name,
                "extraction_model": extraction_model,
                "reasoning_model": reasoning_model,
                "risk_profile": risk_profile,
            },
            "quotes_by_symbol": self._restore_snapshot_quotes(quotes_by_symbol),
            "lookback_days": int(getattr(response, 'lookback_days', 14) or 14),
            "secret_trace": secret_trace or self._build_secret_trace(response, quotes_by_symbol),
        }
        return frozen_snapshot

    def _serialize_snapshot_posts(self, request_id: str, posts: List[Any]) -> List[Dict[str, Any]]:
        """Serialize posts to a compact form suitable for snapshot storage."""
        return [
            {
                "source": str(getattr(post, "source", "") or getattr(post, "feed_name", "") or ""),
                "title": str(getattr(post, "title", "") or ""),
                "summary": str(getattr(post, "summary", "") or ""),
                "content": str(getattr(post, "content", "") or ""),
                "keywords": list(getattr(post, "keywords", None) or []),
            }
            for post in posts
        ]

    def _restore_snapshot_quotes(self, quotes_by_symbol: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Restore a lightweight snapshot of quotes (price + session only)."""
        return {
            symbol: {
                "current_price": quote.get("current_price"),
                "session": quote.get("session"),
                "is_stale": quote.get("is_stale"),
            }
            for symbol, quote in quotes_by_symbol.items()
        }

    def _build_secret_trace(self, response: AnalysisResponse, quotes_by_symbol: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        request_payload = getattr(response, "request_payload", None) or {}
        return {
            "source": request_payload.get("secret_source"),
            "price_source": [
                {
                    "symbol": symbol,
                    "price": float(quote.get("current_price", 0.0)),
                    "source": "cache" if quote.get("is_cached") else "live",
                    "session": quote.get("session", "unknown"),
                }
                for symbol, quote in quotes_by_symbol.items()
            ],
            "config_snapshot": "omitted (sensitive)",
        }

    def _count_symbol_articles(
        self,
        posts: List[Any],
        symbols: List[str],
        relevance_terms: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, int]:
        """Count articles relevant to each symbol by keyword matching."""
        from services.sentiment.prompts import expand_proxy_terms_for_matching, normalize_text_for_matching
        from config.market_constants import SYMBOL_RELEVANCE_TERMS

        counts: Dict[str, int] = {}
        terms = relevance_terms or SYMBOL_RELEVANCE_TERMS
        for symbol in symbols:
            sym_upper = symbol.upper()
            terms_raw = terms.get(sym_upper)
            if not terms_raw:
                counts[sym_upper] = len(posts)
                continue
            terms_list = expand_proxy_terms_for_matching(terms_raw)
            count = 0
            for post in posts:
                text = normalize_text_for_matching(" ".join([
                    str(getattr(post, "title", "") or ""),
                    str(getattr(post, "content", "") or ""),
                    str(getattr(post, "description", "") or ""),
                ]))
                if any(term in text for term in terms_list):
                    count += 1
            counts[sym_upper] = count
        return counts
