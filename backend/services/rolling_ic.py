"""
Rolling Information Coefficient service.

Computes the trailing Spearman IC between signal confidence and realized
forward return per symbol, plus a per-symbol 90th-percentile IC bar used by
the execution rules:

  - paper_trading sizes positions by `ic_score` (positive IC → larger size).
  - paper_trading's overnight de-risk exempts a 3x position only when the
    current trailing IC clears the symbol's own 90th-percentile bar
    (`ic_strong`), i.e. the edge is exceptional compared to recent history.

Reads the decision log (confidence) joined to trade_snapshots (realized
returns) — the same pairing the /api/alpha/ic endpoint reports.

All functions take sessions as arguments (caller-scoped, matching the
request-scoped convention in persistence_service) and never raise; a missing
decision log or empty history yields None so the caller can fall back.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.regime import effective_rule

_DEFAULT_HORIZON = "1d"
_MAX_PAIRS = 400


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    def _rank(vals: List[float]) -> List[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        for idx, pos in enumerate(order):
            ranks[pos] = idx + 1
        return ranks

    if len(xs) < 5 or len(xs) != len(ys):
        return None
    try:
        rx, ry = _rank(xs), _rank(ys)
        n = len(xs)
        mean_rx = sum(rx) / n
        mean_ry = sum(ry) / n
        cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
        var_x = sum((rx[i] - mean_rx) ** 2 for i in range(n))
        var_y = sum((ry[i] - mean_ry) ** 2 for i in range(n))
        if var_x == 0 or var_y == 0:
            return None
        return cov / ((var_x * var_y) ** 0.5)
    except Exception:
        return None


def _paired_series(
    dl_db: Session,
    main_db: Session,
    symbol: str,
    horizon: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """(run_id, symbol, confidence, started_at) rows joined to 1h/1d/3d returns."""
    sym = str(symbol or "").upper().strip()
    if not sym or dl_db is None or main_db is None:
        return []

    dl_rows = dl_db.execute(
        text(
            """
            SELECT dls.symbol, dls.blended_confidence_score, dlr.run_id, dlr.started_at
            FROM decision_log_symbol dls
            JOIN decision_log_run dlr ON dlr.run_id = dls.run_id
            WHERE dls.symbol = :sym
              AND dls.blended_confidence_score IS NOT NULL
            ORDER BY dlr.started_at ASC
            LIMIT :lim
            """
        ),
        {"sym": sym, "lim": limit},
    ).fetchall()
    if not dl_rows:
        return []
    run_ids = [r.run_id for r in dl_rows]
    ret_rows = main_db.execute(
        text(
            """
            SELECT t.request_id, t.underlying_symbol, ts.horizon_label, ts.raw_return_pct
            FROM trades t
            JOIN trade_snapshots ts ON ts.trade_id = t.id
            WHERE t.request_id IN :run_ids
              AND ts.horizon_label = :horizon
              AND ts.raw_return_pct IS NOT NULL
            """
        ).bindparams(bindparam("run_ids", expanding=True)),
        {"run_ids": run_ids, "horizon": horizon},
    ).fetchall()
    ret_by_run = {str(r.request_id): float(r.raw_return_pct) for r in ret_rows
                  if str(r.underlying_symbol or "").upper() == sym}

    pairs: List[Dict[str, Any]] = []
    for row in dl_rows:
        ret = ret_by_run.get(str(row.run_id))
        if ret is not None:
            pairs.append({"confidence": float(row.blended_confidence_score), "return": ret})
    return pairs


def trailing_ic(
    dl_db: Session,
    main_db: Session,
    symbol: str,
    horizon: str = _DEFAULT_HORIZON,
    window: int = 30,
) -> Optional[float]:
    """Spearman IC over the most recent `window` confidence/return pairs."""
    pairs = _paired_series(dl_db, main_db, symbol, horizon, _MAX_PAIRS)
    if len(pairs) < 5:
        return None
    recent = pairs[-window:]
    if len(recent) < 5:
        return None
    return _spearman([p["confidence"] for p in recent], [p["return"] for p in recent])


def ic_percentile(
    dl_db: Session,
    main_db: Session,
    symbol: str,
    pct: float = 90.0,
    horizon: str = _DEFAULT_HORIZON,
    window: int = 30,
    min_windows: int = 12,
) -> Optional[float]:
    """
    The `pct`-percentile of the trailing-IC series for this symbol.

    Computed over successive trailing windows of the recent history, so the
    bar is relative to this symbol's own recent edge distribution (a 3x
    overnight hold must clear the top decile of its own recent ICs).
    """
    pairs = _paired_series(dl_db, main_db, symbol, horizon, _MAX_PAIRS)
    if len(pairs) < min_windows + window:
        return None
    series: List[float] = []
    confs = [p["confidence"] for p in pairs]
    rets = [p["return"] for p in pairs]
    for i in range(len(pairs) - window):
        ic = _spearman(confs[i : i + window], rets[i : i + window])
        if ic is not None:
            series.append(ic)
    if len(series) < min_windows:
        return None
    series.sort(reverse=True)
    idx = max(0, min(len(series) - 1, int(round(len(series) * (100.0 - pct) / 100.0))))
    return series[idx]


def ic_quality(
    dl_db: Session,
    main_db: Session,
    symbol: str,
    window: int = 30,
    horizon: str = _DEFAULT_HORIZON,
) -> str:
    """
    "strong" if the current trailing IC clears the 90th-percentile bar,
    "weak" if the bar exists but the current IC misses it, "unknown" when
    there is not enough history to establish a bar (never exempts de-risking).
    """
    window = int(effective_rule("ic_scaling", "window_trades", window))
    horizon = str(effective_rule("ic_scaling", "horizon", horizon))
    pct = float(effective_rule("ic_scaling", "strong_pct", 90.0))
    try:
        current = trailing_ic(dl_db, main_db, symbol, horizon=horizon, window=window)
        bar = ic_percentile(dl_db, main_db, symbol, pct=pct, horizon=horizon, window=window)
    except Exception:
        return "unknown"
    if current is None or bar is None:
        return "unknown"
    return "strong" if current >= bar else "weak"


def fetch_sizing_ic(
    dl_db: Session,
    main_db: Session,
    symbol: str,
) -> Dict[str, Any]:
    """
    One-call package for persistence_service: {ic_score, ic_strong}.

    Never raises; every key defaults to a neutral value so sizing and the
    de-risk exemption degrade gracefully when the decision log is empty.
    """
    window = int(effective_rule("ic_scaling", "window_trades", 30))
    horizon = str(effective_rule("ic_scaling", "horizon", _DEFAULT_HORIZON))
    try:
        ic = trailing_ic(dl_db, main_db, symbol, horizon=horizon, window=window)
        quality = ic_quality(dl_db, main_db, symbol, window=window, horizon=horizon)
    except Exception:
        return {"ic_score": None, "ic_strong": False}
    return {"ic_score": ic, "ic_strong": quality == "strong"}