"""
Market regime classification and leverage throttling.

Pure helpers shared by the live signal path (signal_service), the paper
execution path (paper_trading), and the backtest harness (vectorbt_engine).

Regime vocabulary:
  - "trending":  price has a defined 50/200 MA spread or a strong, low-ATR
                 drift — leveraged exposure is acceptable.
  - "choppy":    MA cluster is tight (whipsaw zone) or ATR is elevated with no
                 trend — 3x entries get downshifted and overnight 3x exposure
                 triggers de-risking.
  - "unknown":   insufficient indicator data. Signals fail OPEN (no throttling)
                 so a missing indicator never blocks a trade; the *overnight*
                 de-risk path is fail CLOSED by design (see paper_trading).

Nothing here touches the database.
"""

from __future__ import annotations

import json
from datetime import datetime, time as time_cls
from typing import Any, Dict, Optional

from config.logic_loader import LOGIC

_REGIME_CFG = "regime_filter"

# ── Admin overrides (AppConfig.execution_rules_json) ────────────────────────
# Partial per-section overrides saved via the Admin UI. Merged over the
# logic_config.json defaults by refresh_rule_overrides() at the start of each
# analysis run (PipelineService init / process_signals), so a UI save takes
# effect on the next run without a restart.
_RULE_OVERRIDES: Dict[str, Dict[str, Any]] = {}
_RULE_SECTIONS = ("regime_filter", "overnight_derisk", "counter_trend_cooldown", "run_length_protection")


def refresh_rule_overrides(db=None) -> None:
    """
    Reload the Admin execution-rules overrides from AppConfig and merge them
    over the JSON defaults into the process-wide cache. Missing DB/session
    leaves the cache as-is (already-merged defaults).
    """
    global _RULE_OVERRIDES
    blob: Dict[str, Any] = {}
    if db is not None:
        try:
            from services.app_config import get_or_create_app_config
            cfg = get_or_create_app_config(db)
            raw = getattr(cfg, "execution_rules_json", None)
            if raw:
                blob = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            blob = {}
    merged: Dict[str, Dict[str, Any]] = {}
    for section in _RULE_SECTIONS:
        defaults = dict(LOGIC.get(section, {}) or {})
        defaults.update(blob.get(section) or {})
        merged[section] = defaults
    vs = dict(LOGIC.get("vol_sizing", {}) or {})
    ic_defaults = dict(vs.get("ic_scaling", {}) or {})
    ic_defaults.update(blob.get("ic_scaling") or {})
    merged["ic_scaling"] = ic_defaults
    _RULE_OVERRIDES = merged


# ponytail: process-wide cache + refresh-on-run is correct for the single-
# container deployment (Admin API and analysis worker share one process). If the
# pipeline ever moves to a separate worker process, read the blob per call or
# listen to a config-change signal instead.
def effective_rule(section: str, key: str, default: Any) -> Any:
    return (_RULE_OVERRIDES.get(section) or {}).get(key, default)


def _regime_cfg(key: str, default: Any) -> Any:
    return effective_rule(_REGIME_CFG, key, default)


def classify_regime_from_indicators(indicators: Optional[Dict[str, Any]]) -> str:
    """
    Classify regime from a single symbol's technical indicator blob
    (the `technical_indicators_<sym>` payloads in price_context).

    Choppy when the 50/200 MA spread is tight (trend undefined — Bollinger-band
    whipsaw zone) or when ATR% is elevated enough to signal range expansion
    without a trend. Returns "unknown" when neither MA is present.
    """
    if not indicators:
        return "unknown"
    try:
        sma_50 = float(indicators.get("sma_50") or 0.0)
        sma_200 = float(indicators.get("sma_200") or 0.0)
        atr_pct = float(indicators.get("atr_14_pct") or 0.0)
    except (TypeError, ValueError):
        return "unknown"

    chop_ma_spread = float(_regime_cfg("chop_ma_spread_pct", 1.0))
    chop_atr = float(_regime_cfg("chop_atr_pct", 2.5))

    # MA cluster → whipsaw zone: 50 and 200 sitting on top of each other.
    if sma_50 > 0 and sma_200 > 0:
        spread_pct = abs(sma_50 - sma_200) / sma_50 * 100.0
        if spread_pct < chop_ma_spread:
            return "choppy"
        # Wide spread with elevated ATR = volatile churn around a weak trend.
        if atr_pct >= chop_atr:
            return "choppy"
        return "trending"

    # Only ATR available (short history) — high ATR with no trend data is
    # treated as chop; low ATR fails open to trending (no throttling).
    if atr_pct >= chop_atr:
        return "choppy"
    return "unknown"


def market_regime_from_price_context(price_context: Optional[Dict[str, Any]]) -> str:
    """
    Aggregate regime across the primary market symbols (QQQ/SPY).

    Any primary reporting choppy wins (most restrictive). Returns "unknown"
    when no primary has indicator data.
    """
    if not price_context:
        return "unknown"
    primaries = [str(s).upper() for s in (_regime_cfg("primary_market_symbols", ["QQQ", "SPY"]) or ["QQQ", "SPY"])]
    verdicts = []
    for sym in primaries:
        indicators = price_context.get(f"technical_indicators_{sym.lower()}") or {}
        if indicators:
            verdicts.append(classify_regime_from_indicators(indicators))
    if not verdicts:
        return "unknown"
    if "choppy" in verdicts:
        return "choppy"
    if "trending" in verdicts:
        return "trending"
    return "unknown"


def classify_regime_from_prices(prices, window: int = 30) -> str:
    """
    Backtest entry point: classify the trailing `window` closes of a series.

    Ports the RollingWindowOptimizer.evaluate_regime_mix logic to a per-window
    verdict so the backtest can mask entries during chop. Falls back to
    "unknown" (no masking) on insufficient data.
    """
    import numpy as np

    if prices is None or len(prices) < 20:
        return "unknown"
    closes = np.asarray(prices[-window:], dtype=float)
    if len(closes) < 20:
        return "unknown"
    returns = np.diff(closes) / closes[:-1]
    if len(returns) < 20:
        return "unknown"
    mu = float(np.mean(returns))
    sigma = float(np.std(returns))
    if abs(mu) <= 0.0007:  # flat drift = chop
        return "choppy"
    if sigma > 0.015 and abs(mu) < 0.003:  # high vol, weak trend = chop
        return "choppy"
    return "trending"


def regime_leverage_cap(regime: str, raw_cap: int) -> int:
    """
    Throttle the leverage cap in a choppy regime.

    raw_cap comes from the existing ATR caps; a choppy regime imposes the
    configured ceiling on top (default 2x, i.e. QLD/SSO instead of TQQQ/SPXL).
    """
    if str(regime or "").lower() == "choppy":
        chop_cap = int(_regime_cfg("choppy_leverage_cap", 2))
        return max(1, min(int(raw_cap), chop_cap))
    return int(raw_cap)


# ── Overnight de-risking (mandatory time-stop for 3x) ─────────────────────

def _parse_et_time(value: str) -> time_cls:
    hh, mm = (str(value or "15:00")).split(":")
    return time_cls(int(hh), int(mm))


def overnight_derisk_required(
    now_utc: datetime,
    position_leverage: str,
    conviction_level: str,
    ic_strong: Optional[bool],
    market_tz,
) -> bool:
    """
    Should a 3x position be liquidated before today's close?

    Rule: any 3x position after `overnight_derisk.start_et` ET is force-closed
    unless BOTH the conviction is in `exempt_convictions` (HIGH by default)
    AND the rolling IC is above its 90th-percentile bar (ic_strong).

    Fail-closed: missing IC evidence (None) never exempts — a 3x scalp that
    can't prove exceptional edge is cut before close.
    """
    if not effective_rule("overnight_derisk", "enabled", True):
        return False

    lev = str(position_leverage or "1x").lower().replace("x", "")
    try:
        if int(float(lev)) < 3:
            return False
    except ValueError:
        return False

    now_et = now_utc.astimezone(market_tz)
    start = _parse_et_time(effective_rule("overnight_derisk", "start_et", "15:00"))
    if now_et.time() < start:
        return False

    # Session-time sanity: only force closes after the regular session opens
    # (a Saturday/Sunday pre-market run shouldn't liquidate).
    if now_et.time() < _parse_et_time("09:30"):
        return False

    exempt_convs = {str(c).upper() for c in effective_rule("overnight_derisk", "exempt_convictions", ["HIGH"])}
    conviction_ok = str(conviction_level or "").upper() in exempt_convs
    require_ic = effective_rule("overnight_derisk", "require_ic_strong", True)
    ic_ok = bool(ic_strong) if require_ic else True  # None/False → not exempt (fail-closed)
    return not (conviction_ok and ic_ok)