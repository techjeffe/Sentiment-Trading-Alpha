"""
Decision Logger — comprehensive forensic logging to a separate SQLite DB.

Records every analysis run, per-symbol scores (before/after decay/blending),
article usage, blend contributions, technical indicators, materiality gate
checks, red team disagreements, trade lifecycle events, and decision diffs.

All data goes to ``decision_log.db`` (separate from the main trading DB)
to avoid lock contention and allow independent querying while the system
is running.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from database.engine import DecisionLogSessionLocal, DECISION_LOG_PATH, _decision_log_engine
from database.models import DecisionLogBase
from database.models import (
    DecisionLogRun,
    DecisionLogSymbol,
    DecisionLogArticle,
    DecisionLogBlend,
    DecisionLogTechnical,
    DecisionLogTrade,
    DecisionLogTradeEvent,
    DecisionLogDecisionDiff,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _utcnow():
    return datetime.now(timezone.utc)


def config_hash(config_flat: Dict[str, Any]) -> str:
    """SHA256 of a flattened config dict for version correlation."""
    raw = json.dumps(config_flat, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_write_session() -> Session:
    """Get a fresh session for writing to the decision log DB."""
    db = DecisionLogSessionLocal()
    return db


# ── DecisionLogger ───────────────────────────────────────────────────────


class DecisionLogger:
    """Stateless logging facade. Each method takes a session and writes rows."""

    # ── Run lifecycle ────────────────────────────────────────────────

    @staticmethod
    def log_run_start(
        db: Session,
        *,
        run_id: str,
        trigger_source: Optional[str] = None,
        extraction_model: Optional[str] = None,
        reasoning_model: Optional[str] = None,
        config_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record the start of an analysis run."""
        cfg_hash = config_hash(config_snapshot or {})
        row = DecisionLogRun(
            run_id=run_id,
            started_at=_utcnow(),
            trigger_source=trigger_source,
            extraction_model=extraction_model,
            reasoning_model=reasoning_model,
            config_hash=cfg_hash,
            config_snapshot=config_snapshot,
        )
        db.add(row)

    @staticmethod
    def log_run_complete(
        db: Session,
        *,
        run_id: str,
        total_articles_considered: Optional[int] = None,
        total_articles_used: Optional[int] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        """Mark a run as completed with summary stats."""
        row = db.query(DecisionLogRun).filter(DecisionLogRun.run_id == run_id).first()
        if row is None:
            return
        row.completed_at = _utcnow()
        row.total_articles_considered = total_articles_considered
        row.total_articles_used = total_articles_used
        row.duration_ms = duration_ms

    # ── Per-symbol scores ────────────────────────────────────────────

    @staticmethod
    def log_symbol_scores(
        db: Session,
        *,
        run_id: str,
        symbol: str,
        blue_team_output: Optional[Dict[str, Any]] = None,
        red_team_output: Optional[Dict[str, Any]] = None,
        raw_scores: Optional[Dict[str, float]] = None,
        decay_info: Optional[Dict[str, Any]] = None,
        blended_scores: Optional[Dict[str, float]] = None,
        final_signal: Optional[Dict[str, Any]] = None,
        atr_info: Optional[Dict[str, Any]] = None,
        regime_info: Optional[Dict[str, Any]] = None,
        entry_threshold_used: Optional[float] = None,
        materiality_info: Optional[Dict[str, Any]] = None,
        red_team_info: Optional[Dict[str, Any]] = None,
        event_type: Optional[str] = None,
        keyword_attribution: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Record per-symbol scores and return the symbol_log_id."""
        row = DecisionLogSymbol(
            run_id=run_id,
            symbol=symbol,
            blue_team_structured_output=blue_team_output,
            red_team_structured_output=red_team_output,
            raw_bluster_score=raw_scores.get("bluster") if raw_scores else None,
            raw_policy_score=raw_scores.get("policy") if raw_scores else None,
            raw_confidence_score=raw_scores.get("confidence") if raw_scores else None,
            raw_directional_score=raw_scores.get("directional") if raw_scores else None,
            decay_half_life_hours=decay_info.get("half_life") if decay_info else None,
            hold_decay_half_life_hours=decay_info.get("hold_half_life") if decay_info else None,
            decay_factor_applied=decay_info.get("decay_factor") if decay_info else None,
            hold_decay_factor_applied=decay_info.get("hold_decay_factor") if decay_info else None,
            blended_bluster_score=blended_scores.get("bluster") if blended_scores else None,
            blended_policy_score=blended_scores.get("policy") if blended_scores else None,
            blended_confidence_score=blended_scores.get("confidence") if blended_scores else None,
            blended_directional_score=blended_scores.get("directional") if blended_scores else None,
            final_signal_type=final_signal.get("type") if final_signal else None,
            final_conviction=final_signal.get("conviction") if final_signal else None,
            final_trading_type=final_signal.get("trading_type") if final_signal else None,
            final_holding_window_hours=final_signal.get("holding_window_hours") if final_signal else None,
            final_urgency=final_signal.get("urgency") if final_signal else None,
            final_stop_loss_pct=final_signal.get("stop_loss_pct") if final_signal else None,
            final_take_profit_pct=final_signal.get("take_profit_pct") if final_signal else None,
            data_gap_hold=1 if (final_signal or {}).get("data_gap_hold") else 0,
            atr_14d_pct=atr_info.get("atr_pct") if atr_info else None,
            regime_adaptation_triggered=1 if (regime_info or {}).get("triggered") else 0,
            regime_adaptation_atr_pct=regime_info.get("atr_pct") if regime_info else None,
            regime_adaptation_original_threshold=regime_info.get("original_threshold") if regime_info else None,
            regime_adaptation_adjusted_threshold=regime_info.get("adjusted_threshold") if regime_info else None,
            entry_threshold_used=entry_threshold_used,
            materiality_gate_checked=1 if (materiality_info or {}).get("checked") else 0,
            materiality_gate_blocked=1 if (materiality_info or {}).get("blocked") else 0,
            materiality_gate_reason=materiality_info.get("reason") if materiality_info else None,
            materiality_rolling_baseline=materiality_info.get("rolling_baseline") if materiality_info else None,
            red_team_disagreed=1 if (red_team_info or {}).get("disagreed") else 0,
            red_team_confidence_delta=red_team_info.get("confidence_delta") if red_team_info else None,
            red_team_override_resolved_to=red_team_info.get("resolved_to") if red_team_info else None,
            event_type=event_type,
            keyword_attribution=keyword_attribution,
        )
        db.add(row)
        db.flush()
        return row.id

    # ── Articles ─────────────────────────────────────────────────────

    @staticmethod
    def log_articles(
        db: Session,
        *,
        symbol_log_id: int,
        articles: List[Dict[str, Any]],
    ) -> None:
        """Record articles considered for a symbol."""
        for a in articles:
            db.add(DecisionLogArticle(
                symbol_log_id=symbol_log_id,
                title=a.get("title"),
                source=a.get("source"),
                published_at=a.get("published_at"),
                url_hash=a.get("url_hash"),
                was_used=1 if a.get("was_used") else 0,
                relevance_score=a.get("relevance_score"),
            ))

    # ── Blend contributions ──────────────────────────────────────────

    @staticmethod
    def log_blend_contributions(
        db: Session,
        *,
        symbol_log_id: int,
        contributions: List[Dict[str, Any]],
    ) -> None:
        """Record prior runs that contributed to blended scores."""
        for c in contributions:
            db.add(DecisionLogBlend(
                symbol_log_id=symbol_log_id,
                prior_run_timestamp=c.get("timestamp"),
                prior_run_request_id=c.get("request_id"),
                weight=c.get("weight"),
                prior_directional_score=c.get("directional_score"),
            ))

    # ── Technical indicators ─────────────────────────────────────────

    @staticmethod
    def log_technical_indicators(
        db: Session,
        *,
        symbol_log_id: int,
        indicators: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record technical indicator values and confidence adjustments."""
        if not indicators:
            return
        db.add(DecisionLogTechnical(
            symbol_log_id=symbol_log_id,
            rsi_14=indicators.get("rsi_14"),
            sma_50=indicators.get("sma_50"),
            sma_200=indicators.get("sma_200"),
            golden_cross=1 if indicators.get("golden_cross") else 0,
            death_cross=1 if indicators.get("death_cross") else 0,
            macd_positive=1 if indicators.get("macd_positive") else 0,
            volume_above_average=1 if indicators.get("volume_above_average") else None,
            bb_position=indicators.get("bb_position"),
            confidence_adjustment_total=indicators.get("adjustment_total"),
            adjustment_breakdown=indicators.get("adjustment_breakdown"),
        ))

    # ── Trade lifecycle ──────────────────────────────────────────────

    @staticmethod
    def log_trade_entry(
        db: Session,
        *,
        paper_trade_id: Optional[int] = None,
        symbol: str,
        direction: str,
        entry_timestamp: datetime,
        entry_price: Optional[float] = None,
        entry_directional_score: Optional[float] = None,
        entry_confidence: Optional[float] = None,
        entry_atr_pct: Optional[float] = None,
        entry_trade_size: Optional[float] = None,
        entry_size_reasoning: Optional[str] = None,
        entry_leverage: Optional[int] = None,
        entry_leverage_reasoning: Optional[str] = None,
        holding_window_hours: Optional[float] = None,
    ) -> int:
        """Record a new trade open and return the trade_log_id."""
        row = DecisionLogTrade(
            paper_trade_id=paper_trade_id,
            symbol=symbol,
            direction=direction,
            entry_timestamp=entry_timestamp,
            entry_price=entry_price,
            entry_directional_score=entry_directional_score,
            entry_confidence=entry_confidence,
            entry_atr_pct=entry_atr_pct,
            entry_trade_size=entry_trade_size,
            entry_size_reasoning=entry_size_reasoning,
            entry_leverage=entry_leverage,
            entry_leverage_reasoning=entry_leverage_reasoning,
            holding_window_hours=holding_window_hours,
        )
        db.add(row)
        db.flush()
        return row.id

    @staticmethod
    def log_trade_event(
        db: Session,
        *,
        trade_log_id: int,
        event_type: str,
        run_id: Optional[str] = None,
        directional_score: Optional[float] = None,
        decay_factor: Optional[float] = None,
        keep_vs_close: Optional[str] = None,
        decision_reason: Optional[str] = None,
        event_details: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Append a lifecycle event to a trade and return the event_id."""
        row = DecisionLogTradeEvent(
            trade_log_id=trade_log_id,
            event_type=event_type,
            timestamp=_utcnow(),
            run_id=run_id,
            directional_score=directional_score,
            decay_factor=decay_factor,
            keep_vs_close=keep_vs_close,
            decision_reason=decision_reason,
            event_details=event_details,
        )
        db.add(row)
        db.flush()
        return row.id

    @staticmethod
    def log_trade_close(
        db: Session,
        *,
        trade_log_id: int,
        close_timestamp: datetime,
        close_price: Optional[float] = None,
        close_trigger: Optional[str] = None,
        close_final_score: Optional[float] = None,
        realized_pnl: Optional[float] = None,
    ) -> None:
        """Mark a trade as closed."""
        row = db.query(DecisionLogTrade).filter(DecisionLogTrade.id == trade_log_id).first()
        if row is None:
            return
        row.close_timestamp = close_timestamp
        row.close_price = close_price
        row.close_trigger = close_trigger
        row.close_final_score = close_final_score
        row.realized_pnl = realized_pnl
        row.closed = 1

    # ── Decision diffs ───────────────────────────────────────────────

    @staticmethod
    def log_decision_diff(
        db: Session,
        *,
        trade_event_id: int,
        run_id: str,
        before_directional_score: Optional[float] = None,
        after_directional_score: Optional[float] = None,
        before_signal_type: Optional[str] = None,
        after_signal_type: Optional[str] = None,
        before_size_pct: Optional[float] = None,
        after_size_pct: Optional[float] = None,
        reason_code: Optional[str] = None,
    ) -> None:
        """Record a mid-position decision change."""
        db.add(DecisionLogDecisionDiff(
            trade_event_id=trade_event_id,
            run_id=run_id,
            before_directional_score=before_directional_score,
            after_directional_score=after_directional_score,
            before_signal_type=before_signal_type,
            after_signal_type=after_signal_type,
            before_size_pct=before_size_pct,
            after_size_pct=after_size_pct,
            reason_code=reason_code,
        ))


# ── Convenience ──────────────────────────────────────────────────────────

def ensure_decision_log_tables():
    """Create decision log tables if they don't exist."""
    DecisionLogBase.metadata.create_all(bind=_decision_log_engine)
    print(f"Decision log tables ready at {DECISION_LOG_PATH}")


# Singleton instance
logger = DecisionLogger()