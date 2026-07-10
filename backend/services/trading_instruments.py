"""
Helpers for mapping analysis symbols to broker-friendly execution instruments.
"""

from __future__ import annotations

from typing import Any, Dict


INSTRUMENT_SPECS: Dict[str, Dict[str, Any]] = {
    "QQQ": {
        "max_leverage": 3,
        "bull": {1: "QQQ", 2: "QLD", 3: "TQQQ"},
        "bear": {1: "QQQ", 2: "QID", 3: "SQQQ"},
    },
    "SPY": {
        "max_leverage": 3,
        "bull": {1: "SPY", 2: "SSO", 3: "SPXL"},
        "bear": {1: "SPY", 2: "SDS", 3: "SPXS"},
    },
    "USO": {
        "max_leverage": 2,
        "bull": {1: "USO", 2: "UCO"},
        "bear": {1: "USO", 2: "SCO"},
    },
    "IBIT": {
        "max_leverage": 2,
        "bull": {1: "IBIT", 2: "BITU"},
        "bear": {1: "IBIT", 2: "SBIT"},
    },
    "BITO": {
        "max_leverage": 2,
        "bull": {1: "IBIT", 2: "BITU"},
        "bear": {1: "IBIT", 2: "SBIT"},
    },
}


# Sector/stock hedge ETFs (see alpaca_broker.INVERSE_ETF_MAP) that don't appear
# in INSTRUMENT_SPECS but still express a directional bet on an index family.
# Maps execution ticker -> (family underlying, "bull"/"bear") so conflict
# detection recognizes e.g. PSQ (short Nasdaq-100) as opposing TQQQ (long
# Nasdaq-100), even though PSQ gets opened against a stock's `underlying`
# (e.g. NVDA) rather than QQQ. SQQQ and SPXS are already covered via
# INSTRUMENT_SPECS bear buckets above and are intentionally omitted here.
HEDGE_ETF_FAMILY: Dict[str, tuple] = {
    "PSQ": ("QQQ", "bear"),
    "BITI": ("BITO", "bear"),
    "RWM": ("IWM", "bear"),
}


def build_ticker_bucket_map() -> Dict[str, tuple]:
    """Merge INSTRUMENT_SPECS leveraged buckets with HEDGE_ETF_FAMILY hedge
    ETFs into a single execution_ticker -> (family_underlying, bucket) map."""
    bucket_map: Dict[str, tuple] = {}
    for underlying, spec in INSTRUMENT_SPECS.items():
        for tickers in spec.get("bull", {}).values():
            bucket_map[str(tickers).upper()] = (underlying.upper(), "bull")
        for tickers in spec.get("bear", {}).values():
            bucket_map[str(tickers).upper()] = (underlying.upper(), "bear")
    for ticker, (family, direction) in HEDGE_ETF_FAMILY.items():
        bucket_map.setdefault(ticker.upper(), (family.upper(), direction))
    return bucket_map


def normalize_requested_leverage(label: str) -> int:
    normalized = str(label or "1x").strip().lower().replace("x", "")
    try:
        value = int(float(normalized))
        return value if value > 0 else 1
    except ValueError:
        return 1


def build_execution_recommendation(underlying_symbol: str, directional_action: str, leverage_label: str) -> Dict[str, str]:
    """
    Convert an analysis-side symbol/action/leverage into a broker-friendly recommendation.

    For leveraged bearish positions, we switch from `SELL base` to `BUY inverse ETF`
    so users can follow the recommendation in a standard brokerage account.

    The special leverage label "inverse" is used by the conservative risk profile: it routes
    bearish signals to the inverse ETF (e.g. SQQQ for QQQ) with 1x position sizing, so users
    get a broker-friendly bearish expression without leverage amplification.
    """
    underlying = str(underlying_symbol or "").upper().strip()
    action = str(directional_action or "").upper().strip()
    spec = INSTRUMENT_SPECS.get(underlying)

    use_inverse = str(leverage_label or "").lower() == "inverse"
    requested_leverage = normalize_requested_leverage(leverage_label)  # "inverse" parses to 1
    max_leverage = int(spec.get("max_leverage", 1)) if spec else 1
    actual_leverage = min(requested_leverage, max_leverage)
    if actual_leverage not in {1, 2, 3}:
        actual_leverage = 1

    # Conservative bearish: route to inverse ETF at 1x position sizing without shorting
    if use_inverse and action == "SELL" and spec:
        inverse_ticker = spec["bear"].get(max_leverage, spec["bear"][1])
        return {
            "action": "BUY",
            "symbol": inverse_ticker,
            "leverage": "1x",
            "underlying_symbol": underlying,
            "thesis": "SHORT",
        }

    if not spec or action not in {"BUY", "SELL"}:
        return {
            "action": action,
            "symbol": underlying,
            "leverage": f"{actual_leverage}x",
            "underlying_symbol": underlying,
            "thesis": "LONG" if action == "BUY" else "SHORT",
        }

    if action == "BUY":
        execution_symbol = spec["bull"].get(actual_leverage, spec["bull"][1])
        execution_action = "BUY"
        thesis = "LONG"
    else:
        execution_symbol = spec["bear"].get(actual_leverage, spec["bear"][1])
        execution_action = "SELL" if actual_leverage == 1 else "BUY"
        thesis = "SHORT"

    return {
        "action": execution_action,
        "symbol": execution_symbol,
        "leverage": f"{actual_leverage}x",
        "underlying_symbol": underlying,
        "thesis": thesis,
    }


def infer_underlying_symbol(execution_symbol: str) -> str:
    symbol = str(execution_symbol or "").upper().strip()
    for underlying, spec in INSTRUMENT_SPECS.items():
        if symbol == underlying:
            return underlying
        if symbol in spec.get("bull", {}).values():
            return underlying
        if symbol in spec.get("bear", {}).values():
            return underlying
    return symbol
