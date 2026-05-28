"""
Weekly Performance Feedback Analysis Service.

Compares projected vs actual trade outcomes, identifies patterns,
and suggests parameter adjustments. Results are presented to the user
for approval — no automatic adjustments.

Runs every Friday at 4pm CT (10pm UTC).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

_CT = ZoneInfo("America/Chicago")


def _next_friday_4pm_ct() -> datetime:
    """Return the next Friday at 4:00 PM CT."""
    now_ct = datetime.now(_CT)
    # Friday = 4
    days_ahead = 4 - now_ct.weekday()
    if days_ahead < 0:
        days_ahead += 7
    target = now_ct.replace(hour=16, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    # If it's already past 4pm on Friday, schedule for next Friday
    if now_ct.weekday() == 4 and now_ct >= target:
        target += timedelta(days=7)
    return target


def _last_run_timestamp(db: Session) -> Optional[str]:
    """Get the timestamp of the last feedback analysis run."""
    from database.models import DecisionLogFeedback

    row = db.query(DecisionLogFeedback).order_by(
        DecisionLogFeedback.created_at.desc()
    ).first()
    if row:
        return row.created_at.isoformat() if row.created_at else None
    return None


def _set_last_run_timestamp(db: Session, ts: str) -> None:
    """Record the timestamp of the latest feedback analysis run."""
    from database.models import DecisionLogFeedback

    # Use a special system entry
    existing = db.query(DecisionLogFeedback).filter(
        DecisionLogFeedback.feedback_type == "system_run_timestamp"
    ).first()
    if existing:
        existing.timestamp_data = ts
        existing.created_at = datetime.now(timezone.utc)
    else:
        entry = DecisionLogFeedback(
            feedback_type="system_run_timestamp",
            timestamp_data=ts,
            created_at=datetime.now(timezone.utc),
        )
        db.add(entry)
    db.commit()


def analyze_trade_performance(db: Session) -> Dict[str, Any]:
    """
    Analyze closed trades and generate performance feedback.

    Returns a dict with:
    - summary: overall P&L metrics
    - pattern_analysis: identified patterns
    - suggestions: parameter adjustment suggestions
    - confidence: confidence in the suggestions (0-1)
    """
    from database.models import PaperTrade

    _now = datetime.now(timezone.utc)
    # Analyze trades from the last 7 days (or all trades if fewer than 7 days of data)
    _cutoff = _now - timedelta(days=7)

    closed_trades = (
        db.query(PaperTrade)
        .filter(
            PaperTrade.exited_at.isnot(None),
            PaperTrade.exited_at >= _cutoff,
            PaperTrade.realized_pnl.isnot(None),
        )
        .order_by(PaperTrade.exited_at.asc())
        .all()
    )

    if not closed_trades:
        return {
            "status": "no_data",
            "message": "No closed trades in the last 7 days to analyze.",
            "summary": {},
            "patterns": [],
            "suggestions": [],
            "confidence": 0.0,
        }

    # ── Overall summary ───────────────────────────────────────────────
    pnls = [float(t.realized_pnl or 0) for t in closed_trades]
    pnl_pcts = [float(t.realized_pnl_pct or 0) for t in closed_trades]
    wins = [t for t in closed_trades if float(t.realized_pnl or 0) > 0]
    losses = [t for t in closed_trades if float(t.realized_pnl or 0) <= 0]

    total_pnl = sum(pnls)
    avg_win = sum(float(t.realized_pnl or 0) for t in wins) / max(len(wins), 1)
    avg_loss = sum(float(t.realized_pnl or 0) for t in losses) / max(len(losses), 1)
    win_rate = len(wins) / max(len(closed_trades), 1)

    summary = {
        "period_days": 7,
        "total_trades": len(closed_trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(win_rate * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / max(len(closed_trades), 1), 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(
            abs(sum(float(t.realized_pnl or 0) for t in wins) /
                max(abs(sum(float(t.realized_pnl or 0) for t in losses)), 0.01)),
            2,
        ),
    }

    # ── Pattern Analysis ──────────────────────────────────────────────
    patterns = []

    # 1. Direction performance (LONG vs SHORT)
    long_trades = [t for t in closed_trades if str(t.signal_type or "").upper() == "LONG"]
    short_trades = [t for t in closed_trades if str(t.signal_type or "").upper() == "SHORT"]

    if long_trades and short_trades:
        long_pnl = sum(float(t.realized_pnl or 0) for t in long_trades)
        short_pnl = sum(float(t.realized_pnl or 0) for t in short_trades)
        long_wr = len([t for t in long_trades if float(t.realized_pnl or 0) > 0]) / len(long_trades)
        short_wr = len([t for t in short_trades if float(t.realized_pnl or 0) > 0]) / len(short_trades)

        if short_pnl < long_pnl and short_wr < 0.5:
            patterns.append({
                "type": "direction_bias",
                "severity": "high" if short_wr < 0.3 else "medium",
                "finding": f"SHORT trades underperforming: {len(short_trades)} SHORTs with {short_wr*100:.0f}% win rate vs {len(long_trades)} LONGs at {long_wr*100:.0f}%",
                "detail": f"SHORT P&L: ${short_pnl:.2f} vs LONG P&L: ${long_pnl:.2f}",
            })
        elif long_pnl < short_pnl and long_wr < 0.5:
            patterns.append({
                "type": "direction_bias",
                "severity": "high" if long_wr < 0.3 else "medium",
                "finding": f"LONG trades underperforming: {len(long_trades)} LONGs with {long_wr*100:.0f}% win rate vs {len(short_trades)} SHORTs at {short_wr*100:.0f}%",
                "detail": f"LONG P&L: ${long_pnl:.2f} vs SHORT P&L: ${short_pnl:.2f}",
            })

    # 2. Symbol performance
    by_symbol: Dict[str, List] = {}
    for t in closed_trades:
        sym = str(t.underlying or "").upper()
        by_symbol.setdefault(sym, []).append(t)

    for sym, trades in by_symbol.items():
        sym_pnls = [float(t.realized_pnl or 0) for t in trades]
        sym_wr = len([p for p in sym_pnls if p > 0]) / len(sym_pnls)
        sym_total = sum(sym_pnls)

        if len(trades) >= 3:  # Need at least 3 trades for statistical significance
            if sym_wr < 0.4 and sym_total < 0:
                patterns.append({
                    "type": "symbol_underperformance",
                    "severity": "high" if sym_wr < 0.3 else "medium",
                    "finding": f"{sym} shows consistent losses: {len(trades)} trades, {sym_wr*100:.0f}% win rate, ${sym_total:.2f} total P&L",
                    "detail": f"Avg trade: ${sum(sym_pnls)/len(sym_pnls):.2f}",
                })
            elif sym_wr > 0.7 and sym_total > 0:
                patterns.append({
                    "type": "symbol_outperformance",
                    "severity": "info",
                    "finding": f"{sym} is a strong performer: {len(trades)} trades, {sym_wr*100:.0f}% win rate, ${sym_total:.2f} total P&L",
                    "detail": f"Avg trade: ${sum(sym_pnls)/len(sym_pnls):.2f}",
                })

    # 3. Conviction level performance
    by_conviction: Dict[str, List] = {}
    for t in closed_trades:
        conv = str(t.conviction_level or "MEDIUM").upper()
        by_conviction.setdefault(conv, []).append(t)

    for conv, trades in by_conviction.items():
        conv_pnls = [float(t.realized_pnl or 0) for t in trades]
        conv_wr = len([p for p in conv_pnls if p > 0]) / len(conv_pnls)
        conv_total = sum(conv_pnls)

        if conv == "HIGH" and conv_wr < 0.5:
            patterns.append({
                "type": "conviction_mismatch",
                "severity": "high",
                "finding": f"HIGH conviction trades have low win rate: {conv_wr*100:.0f}% ({len(trades)} trades)",
                "detail": f"HIGH conviction P&L: ${conv_total:.2f}, avg: ${conv_total/len(conv_pnls):.2f}",
            })
        elif conv == "LOW" and conv_wr > 0.6:
            patterns.append({
                "type": "conviction_mismatch",
                "severity": "medium",
                "finding": f"LOW conviction trades outperform expectations: {conv_wr*100:.0f}% win rate",
                "detail": f"LOW conviction P&L: ${conv_total:.2f}",
            })

    # 4. Holding period analysis
    by_type: Dict[str, List] = {}
    for t in closed_trades:
        tt = str(t.trading_type or "SWING").upper()
        by_type.setdefault(tt, []).append(t)

    for tt, trades in by_type.items():
        type_pnls = [float(t.realized_pnl or 0) for t in trades]
        type_wr = len([p for p in type_pnls if p > 0]) / len(type_pnls)
        type_avg_pnl = sum(type_pnls) / len(type_pnls)

        if type_avg_pnl < -5 and len(trades) >= 2:
            patterns.append({
                "type": "holding_period_loss",
                "severity": "medium",
                "finding": f"{tt} trades losing money on average: ${type_avg_pnl:.2f}/trade",
                "detail": f"{len(trades)} {tt} trades, {type_wr*100:.0f}% win rate",
            })

    # 5. Close reason analysis
    by_close_reason: Dict[str, List] = {}
    for t in closed_trades:
        reason = str(t.close_reason or "unknown").upper()
        by_close_reason.setdefault(reason, []).append(t)

    for reason, trades in by_close_reason.items():
        reason_pnls = [float(t.realized_pnl or 0) for t in trades]
        reason_wr = len([p for p in reason_pnls if p > 0]) / len(reason_pnls)
        reason_total = sum(reason_pnls)

        if reason in ("STOP_LOSS_HIT", "TAKE_PROFIT_HIT") and len(trades) >= 2:
            patterns.append({
                "type": "exit_trigger_analysis",
                "severity": "info",
                "finding": f"{reason}: {len(trades)} trades, {reason_wr*100:.0f}% win rate, ${reason_total:.2f} P&L",
                "detail": f"Avg P&L per {reason.lower()}: ${reason_total/max(len(trades),1):.2f}",
            })

    # ── Suggestions ───────────────────────────────────────────────────
    suggestions = []

    # Suggestion 1: Adjust conviction thresholds
    high_perf = by_conviction.get("HIGH", [])
    med_perf = by_conviction.get("MEDIUM", [])
    low_perf = by_conviction.get("LOW", [])

    if high_perf and med_perf:
        high_wr = len([t for t in high_perf if float(t.realized_pnl or 0) > 0]) / len(high_perf)
        med_wr = len([t for t in med_perf if float(t.realized_pnl or 0) > 0]) / len(med_perf)

        if high_wr < med_wr - 0.15:
            suggestions.append({
                "category": "conviction_thresholds",
                "action": "relax_high_conviction_threshold",
                "description": "HIGH conviction trades underperform MEDIUM — consider relaxing the confidence threshold for HIGH conviction",
                "parameters": {
                    "current_high_conf_threshold": 0.80,
                    "suggested_high_conf_threshold": 0.75,
                    "reason": f"HIGH conviction win rate ({high_wr*100:.0f}%) < MEDIUM ({med_wr*100:.0f}%)",
                },
                "impact": "medium",
            })

    # Suggestion 2: Adjust entry thresholds based on symbol performance
    for pattern in patterns:
        if pattern["type"] == "symbol_underperformance":
            sym = pattern["finding"].split()[0]
            suggestions.append({
                "category": "symbol_filtering",
                "action": "increase_entry_threshold",
                "description": f"Consider raising the entry threshold for {sym} to reduce losing trades",
                "parameters": {
                    "symbol": sym,
                    "current_threshold": 0.42,
                    "suggested_threshold": 0.50,
                    "reason": f"{sym} has {pattern['finding'].split()[-2]}% win rate",
                },
                "impact": "medium",
            })

    # Suggestion 3: Adjust stop-loss/take-profit
    stop_losses = by_close_reason.get("STOP_LOSS_HIT", [])
    take_profits = by_close_reason.get("TAKE_PROFIT_HIT", [])

    if stop_losses and take_profits:
        sl_pnls = [float(t.realized_pnl or 0) for t in stop_losses]
        tp_pnls = [float(t.realized_pnl or 0) for t in take_profits]
        avg_sl = sum(sl_pnls) / len(sl_pnls)
        avg_tp = sum(tp_pnls) / len(tp_pnls)

        if abs(avg_sl) > abs(avg_tp) * 1.5:
            suggestions.append({
                "category": "exit_levels",
                "action": "adjust_stop_loss",
                "description": "Stop losses are hitting with larger losses than take profits are gaining — consider tightening the stop-loss",
                "parameters": {
                    "current_stop_loss_pct": 2.0,
                    "suggested_stop_loss_pct": 1.5,
                    "current_take_profit_pct": 3.0,
                    "reason": f"Avg stop-loss hit: ${avg_sl:.2f}, Avg take-profit: ${avg_tp:.2f}",
                },
                "impact": "high",
            })

    # Suggestion 4: Adjust SHORT bias
    short_patterns = [p for p in patterns if p["type"] == "direction_bias" and "SHORT" in p.get("finding", "")]
    if short_patterns:
        for sp in short_patterns:
            if sp["severity"] == "high":
                suggestions.append({
                    "category": "signal_bias",
                    "action": "adjust_scoring_thresholds",
                    "description": "SHORT signals are significantly underperforming — consider adjusting sentiment scoring thresholds",
                    "parameters": {
                        "current_bluster_short_threshold": -0.70,
                        "suggested_bluster_short_threshold": -0.75,
                        "current_policy_signal_threshold": 0.45,
                        "suggested_policy_signal_threshold": 0.50,
                        "reason": sp["finding"],
                    },
                    "impact": "high",
                })

    # ── Overall confidence ────────────────────────────────────────────
    # Confidence in suggestions based on sample size
    min_trades_for_confidence = 5
    confidence = min(1.0, len(closed_trades) / min_trades_for_confidence)
    if len(closed_trades) < 3:
        confidence = 0.0

    return {
        "status": "success",
        "summary": summary,
        "patterns": patterns,
        "suggestions": suggestions,
        "confidence": round(confidence, 2),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def log_feedback_to_decision_log(db: Session, feedback: Dict[str, Any], request_id: str = "") -> None:
    """Write feedback analysis results to the decision log."""
    from database.models import DecisionLogTrade, DecisionLogFeedback

    # Create a system trade log entry for the feedback analysis
    now = datetime.now(timezone.utc)

    feedback_entry = DecisionLogFeedback(
        feedback_type="weekly_analysis",
        analysis_data=json.dumps(feedback, default=str),
        created_at=now,
    )
    db.add(feedback_entry)
    db.commit()

    print(f"[feedback] analysis logged (patterns={len(feedback.get('patterns', []))}, suggestions={len(feedback.get('suggestions', []))})")


def should_run_analysis(db: Session) -> bool:
    """Check if the weekly analysis should run now (Friday 4pm CT)."""
    now_ct = datetime.now(_CT)
    # Only run on Fridays
    if now_ct.weekday() != 4:  # Friday = 4
        return False
    # Only run between 4pm and 6pm CT (2-hour window)
    if not (16 <= now_ct.hour < 18):
        return False
    # Check if already run this week
    last_run = _last_run_timestamp(db)
    if last_run:
        try:
            last_dt = datetime.fromisoformat(last_run)
            last_ct = last_dt.astimezone(_CT) if last_dt.tzinfo else last_dt.replace(tzinfo=_CT)
            # If last run was this week (same ISO week), skip
            if last_ct.isocalendar() == now_ct.isocalendar():
                return False
        except (ValueError, TypeError):
            pass
    return True


def run_weekly_analysis(db: Session, request_id: str = "") -> Dict[str, Any]:
    """Run the full weekly feedback analysis pipeline."""
    feedback = analyze_trade_performance(db)
    if feedback["status"] == "success":
        log_feedback_to_decision_log(db, feedback, request_id)
        _set_last_run_timestamp(db, datetime.now(timezone.utc).isoformat())
    return feedback
