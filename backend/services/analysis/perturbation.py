"""
Perturbation / sensitivity analysis for signal thresholds.

Replays historical decision_log_symbol rows with a nudged entry_threshold
and reports how the signal count and realized P&L would have changed.

Usage (from the alpha router):
    from services.analysis.perturbation import run_perturbation
    result = run_perturbation(dl_db, main_db, nudge_pct=0.10, symbol=None)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from database.models import TradeSnapshot


def run_perturbation(
    dl_db: Session,
    main_db: Session,
    nudge_pct: float = 0.10,
    symbol: Optional[str] = None,
    horizons: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Replay historical signals with entry_threshold nudged by ±nudge_pct.

    For each historical run we know:
      - blended_directional_score  (the raw score produced)
      - entry_threshold_used        (the threshold that was live at the time)
      - final_signal_type           (LONG / SHORT / HOLD — what actually fired)

    We re-apply the threshold at baseline, +nudge, and -nudge and compare
    how many directional (non-HOLD) signals would have been emitted, then
    join to trade_snapshots to compute average return per scenario.

    Returns a dict with keys: baseline, nudge_up, nudge_down — each containing
    signal_count, avg_return_by_horizon, and blocked_count.
    """
    horizons = horizons or ["4h", "1d", "3d", "1w"]

    from database.engine import _decision_log_engine
    from sqlalchemy import text, bindparam

    sym_filter = "AND dls.symbol = :sym" if symbol else ""
    query = text(f"""
        SELECT
            dls.id              AS sym_log_id,
            dls.symbol,
            dls.blended_directional_score,
            dls.entry_threshold_used,
            dls.final_signal_type,
            dlr.run_id
        FROM decision_log_symbol dls
        JOIN decision_log_run dlr ON dlr.run_id = dls.run_id
        WHERE dls.entry_threshold_used IS NOT NULL
          AND dls.blended_directional_score IS NOT NULL
          {sym_filter}
        ORDER BY dlr.started_at DESC
        LIMIT 500
    """)
    params: Dict[str, Any] = {"sym": symbol} if symbol else {}
    rows = dl_db.execute(query, params).fetchall()

    if not rows:
        return {"error": "No historical rows with entry_threshold_used found", "rows_checked": 0}

    # Pull all trade_snapshots for any trade linked to these runs.
    # Link: trades.request_id = decision_log_run.run_id.
    # NOTE: trade_snapshots.trade_id is a FK into `trades` (the recommendation
    # log), not `paper_trades` — the two tables have independent id sequences.
    run_ids = list({r.run_id for r in rows})
    if run_ids:
        ts_rows = (
            main_db.query(TradeSnapshot)
            .filter(TradeSnapshot.horizon_label.in_(horizons))
            .all()
        )
        # Build lookup: trade_id → {horizon → raw_return_pct}
        returns_by_pt: Dict[int, Dict[str, float]] = {}
        for ts in ts_rows:
            returns_by_pt.setdefault(ts.trade_id, {})[ts.horizon_label] = ts.raw_return_pct
    else:
        returns_by_pt = {}

    # We also need trades.request_id → trade_id mapping.
    # We use a raw query against the main DB because TradeSnapshot is in main.
    pt_map_rows = main_db.execute(
        text("SELECT id, request_id, underlying_symbol FROM trades WHERE request_id IN :run_ids")
        .bindparams(bindparam("run_ids", expanding=True)),
        {"run_ids": run_ids},
    ).fetchall()

    # run_id + symbol → [trade_id, ...]
    from collections import defaultdict
    pt_by_run_sym: Dict[tuple, List[int]] = defaultdict(list)
    for pt in pt_map_rows:
        if not pt.underlying_symbol:
            continue
        pt_by_run_sym[(pt.request_id, pt.underlying_symbol.upper())].append(pt.id)

    def _would_fire(score: float, threshold: float) -> bool:
        return abs(score) >= threshold

    def _scenario(multiplier: float) -> Dict[str, Any]:
        fired = 0
        blocked = 0
        horizon_returns: Dict[str, List[float]] = {h: [] for h in horizons}

        for row in rows:
            threshold = row.entry_threshold_used * (1.0 + multiplier)
            fires = _would_fire(row.blended_directional_score, threshold)
            if fires:
                fired += 1
                pts = pt_by_run_sym.get((row.run_id, row.symbol.upper()), [])
                for pt_id in pts:
                    for h in horizons:
                        ret = (returns_by_pt.get(pt_id) or {}).get(h)
                        if ret is not None:
                            horizon_returns[h].append(ret)
            else:
                blocked += 1

        avg_returns = {
            h: round(sum(v) / len(v), 4) if v else None
            for h, v in horizon_returns.items()
        }
        return {
            "signal_count": fired,
            "blocked_count": blocked,
            "threshold_multiplier": round(1.0 + multiplier, 3),
            "avg_return_by_horizon": avg_returns,
        }

    return {
        "rows_analyzed": len(rows),
        "nudge_pct": nudge_pct,
        "symbol_filter": symbol,
        "baseline": _scenario(0.0),
        "nudge_up": _scenario(nudge_pct),
        "nudge_down": _scenario(-nudge_pct),
    }
