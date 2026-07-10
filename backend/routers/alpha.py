"""
Alpha Analytics Router.

Endpoints:
- GET  /api/v1/alpha/ic           — Rolling Information Coefficient (signal confidence vs forward return)
- GET  /api/v1/alpha/attribution  — Feature attribution breakdown by event_type and keyword source
- POST /api/v1/alpha/perturbation — Sensitivity test: how P&L changes when entry_threshold is nudged
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text, bindparam
from sqlalchemy.orm import Session

from database.engine import get_db, get_decision_log_db

router = APIRouter(prefix="/api/v1/alpha", tags=["alpha"])

_VALID_HORIZONS = {"4h", "1d", "3d", "1w"}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    """Spearman rank correlation — no scipy dependency."""
    n = len(xs)
    if n < 4:
        return None

    def _rank(vals: List[float]) -> List[float]:
        sorted_vals = sorted(enumerate(vals), key=lambda iv: iv[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and sorted_vals[j + 1][1] == sorted_vals[j][1]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[sorted_vals[k][0]] = avg_rank
            i = j + 1
        return ranks

    rx, ry = _rank(xs), _rank(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)))
    dy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(n)))
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 4)


# ── IC endpoint ───────────────────────────────────────────────────────────────


@router.get("/ic")
def get_information_coefficient(
    symbol: Optional[str] = Query(None, description="Filter to one symbol (e.g. USO). Omit for all."),
    horizons: str = Query("4h,1d,3d,1w", description="Comma-separated horizon labels"),
    window: int = Query(30, ge=5, le=200, description="Rolling window size in trades"),
    main_db: Session = Depends(get_db),
    dl_db: Session = Depends(get_decision_log_db),
) -> Dict[str, Any]:
    """
    Rolling Spearman IC between blended_confidence_score and raw_return_pct.

    Joins decision_log_symbol → paper_trades (via analysis_request_id + underlying)
    → trade_snapshots to pair each signal's confidence with its realized return
    at each requested horizon.
    """
    requested = [h.strip() for h in horizons.split(",") if h.strip() in _VALID_HORIZONS]
    if not requested:
        return {"error": f"No valid horizons. Choose from {sorted(_VALID_HORIZONS)}"}

    sym_filter = "AND dls.symbol = :sym" if symbol else ""
    query = text(f"""
        SELECT
            dls.symbol,
            dls.blended_confidence_score,
            dls.blended_directional_score,
            dls.final_signal_type,
            dls.event_type,
            dlr.run_id,
            dlr.started_at
        FROM decision_log_symbol dls
        JOIN decision_log_run dlr ON dlr.run_id = dls.run_id
        WHERE dls.blended_confidence_score IS NOT NULL
          {sym_filter}
        ORDER BY dlr.started_at ASC
    """)
    params: Dict[str, Any] = {"sym": symbol.upper()} if symbol else {}
    dl_rows = dl_db.execute(query, params).fetchall()

    if not dl_rows:
        return {"ic_by_horizon": {}, "pairs_count": 0, "message": "No data yet — run analyses to populate."}

    run_ids = list({r.run_id for r in dl_rows})
    # Batch query paper_trades + trade_snapshots from main DB using expanding IN
    ts_query = text("""
        SELECT
            pt.analysis_request_id,
            pt.underlying,
            ts.horizon_label,
            ts.raw_return_pct
        FROM paper_trades pt
        JOIN trade_snapshots ts ON ts.trade_id = pt.id
        WHERE pt.analysis_request_id IN :run_ids
          AND ts.horizon_label IN :horizons
    """).bindparams(
        bindparam("run_ids", expanding=True),
        bindparam("horizons", expanding=True),
    )
    ts_rows = main_db.execute(ts_query, {"run_ids": run_ids, "horizons": requested}).fetchall()

    # returns[(run_id, symbol, horizon)] = raw_return_pct
    returns: Dict[tuple, float] = {}
    for ts in ts_rows:
        key = (ts.analysis_request_id, ts.underlying.upper(), ts.horizon_label)
        returns[key] = ts.raw_return_pct

    # Build paired (confidence, return) series per horizon
    pairs_by_horizon: Dict[str, List[tuple]] = {h: [] for h in requested}
    for row in dl_rows:
        for h in requested:
            ret = returns.get((row.run_id, row.symbol.upper(), h))
            if ret is not None and row.blended_confidence_score is not None:
                pairs_by_horizon[h].append((
                    float(row.blended_confidence_score),
                    float(ret),
                    str(row.symbol),
                    str(row.event_type or ""),
                    str(row.started_at or ""),
                ))

    # Rolling IC per horizon
    ic_by_horizon: Dict[str, Any] = {}
    for h, pairs in pairs_by_horizon.items():
        if len(pairs) < 4:
            ic_by_horizon[h] = {"overall": None, "rolling": [], "pairs": len(pairs)}
            continue
        confidences = [p[0] for p in pairs]
        rets = [p[1] for p in pairs]
        overall_ic = _spearman(confidences, rets)
        rolling: List[Dict[str, Any]] = []
        for i in range(window - 1, len(pairs)):
            window_pairs = pairs[i - window + 1 : i + 1]
            wc = [p[0] for p in window_pairs]
            wr = [p[1] for p in window_pairs]
            ic_val = _spearman(wc, wr)
            last = window_pairs[-1]
            rolling.append({
                "trade_index": i,
                "timestamp": last[4],
                "symbol": last[2],
                "ic": ic_val,
            })
        ic_by_horizon[h] = {
            "overall": overall_ic,
            "rolling": rolling,
            "pairs": len(pairs),
        }

    # Scatter data: confidence vs return (all horizons, for frontend chart)
    scatter: List[Dict[str, Any]] = []
    for h in requested:
        for p in pairs_by_horizon.get(h, []):
            scatter.append({
                "horizon": h,
                "symbol": p[2],
                "event_type": p[3],
                "confidence": round(p[0], 4),
                "return_pct": round(p[1], 4),
                "timestamp": p[4],
            })

    return {
        "ic_by_horizon": ic_by_horizon,
        "scatter": scatter,
        "pairs_count": sum(len(v) for v in pairs_by_horizon.values()),
        "window": window,
        "symbol_filter": symbol,
    }


# ── Attribution endpoint ──────────────────────────────────────────────────────


@router.get("/attribution")
def get_attribution(
    symbol: Optional[str] = Query(None),
    limit: int = Query(100, ge=10, le=500),
    dl_db: Session = Depends(get_decision_log_db),
) -> Dict[str, Any]:
    """
    Breakdown of which event_types and keyword sources drove signals.
    Returns aggregated counts and average directional scores per category.
    """
    sym_filter = "AND dls.symbol = :sym" if symbol else ""
    query = text(f"""
        SELECT
            dls.symbol,
            dls.event_type,
            dls.final_signal_type,
            dls.blended_directional_score,
            dls.blended_confidence_score,
            dls.keyword_attribution,
            dlr.started_at
        FROM decision_log_symbol dls
        JOIN decision_log_run dlr ON dlr.run_id = dls.run_id
        WHERE dls.event_type IS NOT NULL
          {sym_filter}
        ORDER BY dlr.started_at DESC
        LIMIT :lim
    """)
    params: Dict[str, Any] = {"lim": limit}
    if symbol:
        params["sym"] = symbol.upper()
    rows = dl_db.execute(query, params).fetchall()

    # Aggregate by event_type
    by_event: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "count": 0, "directional_scores": [], "long_count": 0, "short_count": 0, "hold_count": 0,
    })
    top_sources: Dict[str, int] = defaultdict(int)
    top_terms: Dict[str, int] = defaultdict(int)

    import json as _json
    for row in rows:
        et = row.event_type or "unknown"
        by_event[et]["count"] += 1
        if row.blended_directional_score is not None:
            by_event[et]["directional_scores"].append(float(row.blended_directional_score))
        sig = str(row.final_signal_type or "").upper()
        if sig == "LONG":
            by_event[et]["long_count"] += 1
        elif sig == "SHORT":
            by_event[et]["short_count"] += 1
        else:
            by_event[et]["hold_count"] += 1

        # Keyword attribution aggregation
        kw_attr = row.keyword_attribution
        if isinstance(kw_attr, str):
            try:
                kw_attr = _json.loads(kw_attr)
            except Exception:
                kw_attr = {}
        if isinstance(kw_attr, dict):
            for src, cnt in (kw_attr.get("source_breakdown") or {}).items():
                top_sources[src] += cnt
            for term_obj in kw_attr.get("top_terms") or []:
                t = term_obj.get("term", "")
                c = term_obj.get("count", 0)
                if t:
                    top_terms[t] += c

    # Summarize
    event_summary = []
    for et, data in sorted(by_event.items(), key=lambda x: -x[1]["count"]):
        scores = data["directional_scores"]
        event_summary.append({
            "event_type": et,
            "count": data["count"],
            "avg_directional_score": round(sum(scores) / len(scores), 4) if scores else None,
            "long_count": data["long_count"],
            "short_count": data["short_count"],
            "hold_count": data["hold_count"],
        })

    return {
        "rows_analyzed": len(rows),
        "symbol_filter": symbol,
        "by_event_type": event_summary,
        "top_sources": sorted(top_sources.items(), key=lambda x: -x[1])[:15],
        "top_terms": [{"term": t, "count": c} for t, c in sorted(top_terms.items(), key=lambda x: -x[1])[:20]],
    }


# ── Perturbation endpoint ─────────────────────────────────────────────────────


class PerturbationRequest(BaseModel):
    nudge_pct: float = Field(0.10, ge=0.01, le=0.50, description="Fractional nudge applied to entry_threshold (e.g. 0.10 = ±10%)")
    symbol: Optional[str] = Field(None, description="Filter to one underlying symbol")
    horizons: List[str] = Field(default=["4h", "1d", "3d", "1w"])


@router.post("/perturbation")
def run_perturbation_test(
    req: PerturbationRequest,
    main_db: Session = Depends(get_db),
    dl_db: Session = Depends(get_decision_log_db),
) -> Dict[str, Any]:
    """
    Replay historical signals with entry_threshold nudged by ±nudge_pct.

    Returns signal counts and average returns per scenario so you can see
    whether your threshold is curve-fit or genuinely robust.
    """
    valid_horizons = [h for h in req.horizons if h in _VALID_HORIZONS]
    if not valid_horizons:
        return {"error": f"No valid horizons. Choose from {sorted(_VALID_HORIZONS)}"}

    from services.analysis.perturbation import run_perturbation
    return run_perturbation(
        dl_db=dl_db,
        main_db=main_db,
        nudge_pct=req.nudge_pct,
        symbol=req.symbol.upper() if req.symbol else None,
        horizons=valid_horizons,
    )
