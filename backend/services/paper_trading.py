"""
Paper trading simulation service.

Auto-executes a configurable paper trade for every directional signal fired during
extended market hours (4:00am–8:00pm ET, Mon–Fri).

Position lifecycle (mirrors what a real trader following every signal would do):
- Same ticker + same leverage + same direction → hold, no change
- Different ticker OR different leverage OR direction flip → close old, open new
- HOLD signal → close any open position (thesis gone), stay flat
"""

import types as _types
from datetime import datetime, timedelta, time as time_cls, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List, Tuple

from config.logic_loader import LOGIC as _L

_MARKET_TZ = ZoneInfo("America/New_York")

# ── OVERLAP COOLDOWN TRACKING (module-level, persists across runs) ────
# _cron_overlap_keys[(underlying, signal_type)] = utc datetime
#   Prevents duplicate orders when multiple analysis runs fire simultaneously.
# _cron_overlap_underlying_keys[underlying] = utc datetime
#   Prevents concurrent runs from opening OPPOSING positions for the same
#   underlying (e.g., SPXL long + SPXS short in the same dispatch cycle).
# _last_order_times[symbol] = utc datetime
#   Prevents manual+auto duplicate orders for the same symbol.
_cron_overlap_keys: Dict[Tuple[str, str], datetime] = {}
_cron_overlap_underlying_keys: Dict[str, datetime] = {}
_last_order_times: Dict[str, datetime] = {}

# 24/5 trading schedule (Alpaca: Sun 8 PM ET → Fri 8 PM ET)
_OVERNIGHT_OPEN  = time_cls(20, 0)   # 8:00 PM ET — overnight session start
_OVERNIGHT_CLOSE = time_cls(4, 0)    # 4:00 AM ET — overnight session end
_EXTENDED_OPEN   = time_cls(4, 0)    # 4:00 AM ET — pre-market open
_EXTENDED_CLOSE  = time_cls(20, 0)   # 8:00 PM ET — after-hours close
_REGULAR_OPEN    = time_cls(9, 30)   # 9:30 AM ET — regular session open
_REGULAR_CLOSE   = time_cls(16, 0)   # 4:00 PM ET — regular session close


def _allow_extended_hours_trading(db=None) -> bool:
    if db is None:
        return True
    try:
        from services.app_config import get_or_create_app_config
        config = get_or_create_app_config(db)
        return bool(getattr(config, "allow_extended_hours_trading", True))
    except Exception:
        return True


def _directional_return_pct(signal_type: str, entry_price: float, current_price: float) -> float:
    """Return percentage P&L with correct sign for long vs short paper trades."""
    if entry_price <= 0 or current_price <= 0:
        return 0.0

    raw_return = (current_price - entry_price) / entry_price
    if str(signal_type or "").upper() == "SHORT":
        raw_return *= -1

    return raw_return * 100.0


def _directional_pnl(signal_type: str, entry_price: float, current_price: float, amount: float) -> float:
    """Convert directional return into dollar P&L for the paper notional."""
    return amount * (_directional_return_pct(signal_type, entry_price, current_price) / 100.0)


def _calculate_adaptive_stop_loss(pos, quotes_by_symbol: Dict[str, Dict[str, Any]], logic_config: dict) -> float:
    """Calculate ATR-based adaptive stop loss instead of fixed percentage."""
    adaptive_cfg = logic_config.get("adaptive_stops", {})
    if not adaptive_cfg.get("enabled", False):
        return _stop_loss_pct_for_config(None)
    
    execution_ticker = str(getattr(pos, "execution_ticker", "") or "").upper()
    underlying = str(getattr(pos, "underlying", "") or "").upper()
    
    # Try to get ATR from quotes
    price_data = quotes_by_symbol.get(execution_ticker) or quotes_by_symbol.get(underlying) or {}
    indicators = price_data.get("technical_indicators", {})
    atr_pct = float(indicators.get("atr_14_pct", 0.0) or 0.0)
    
    if atr_pct <= 0:
        # Fall back to fixed stop if ATR not available
        return _stop_loss_pct_for_config(None)
    
    atr_multiplier = float(adaptive_cfg.get("atr_multiplier", 1.5))
    # Floor the adaptive stop at the configured minimum (2%) so a
    # low-ATR name can't get a sub-1% stop that fires on noise.
    min_stop = max(_min_stop_loss_pct(), float(adaptive_cfg.get("min_stop_pct", 1.0)))
    max_stop = float(adaptive_cfg.get("max_stop_pct", 5.0))
    
    adaptive_stop = atr_pct * atr_multiplier
    return max(min_stop, min(max_stop, adaptive_stop))


def _check_tiered_profit_scaling(pos, quotes_by_symbol: Dict[str, Dict[str, Any]], logic_config: dict, db) -> List[Dict[str, Any]]:
    """Check if any profit tiers have been hit and execute partial closes."""
    tier_cfg = logic_config.get("tiered_profit_scaling", {})
    if not tier_cfg.get("enabled", False):
        return []
    
    actions = []
    current_price = _resolve_position_market_price(pos, quotes_by_symbol)
    if current_price <= 0:
        return actions
    
    entry_price = float(getattr(pos, "entry_price", 0.0) or 0.0)
    if entry_price <= 0:
        return actions
    
    signal_type = str(getattr(pos, "signal_type", "") or "").upper()
    pnl_pct = _directional_return_pct(signal_type, entry_price, current_price)
    
    # Get or initialize taken tiers
    taken_tiers = getattr(pos, "profit_tiers_taken", None) or []
    
    for tier in tier_cfg.get("tiers", []):
        tier_pnl = float(tier.get("pnl_pct", 0.0))
        if pnl_pct >= tier_pnl and tier_pnl not in taken_tiers:
            # Execute partial close
            sell_fraction = float(tier.get("sell_fraction", 0.0))
            close_amount = float(pos.amount or 0.0) * sell_fraction
            
            if close_amount > 0:
                # Partial close logic
                remaining_amount = float(pos.amount or 0.0) - close_amount
                realized_pnl = _directional_pnl(signal_type, entry_price, current_price, close_amount)
                
                pos.amount = remaining_amount
                pos.realized_pnl = float(getattr(pos, "realized_pnl", 0.0) or 0.0) + realized_pnl
                
                taken_tiers.append(tier_pnl)
                pos.profit_tiers_taken = taken_tiers
                
                actions.append({
                    "action": "partial_close",
                    "tier_pnl_pct": tier_pnl,
                    "close_amount": close_amount,
                    "remaining_amount": remaining_amount,
                    "realized_pnl": realized_pnl
                })
                print(f"[paper] {getattr(pos, 'underlying', '')} tier {tier_pnl}% hit: closed {close_amount:.2f}, remaining {remaining_amount:.2f}")
    
    # If all tiers taken, set tight trailing stop on remainder
    if len(taken_tiers) >= len(tier_cfg.get("tiers", [])) and not getattr(pos, "trailing_stop_price", None):
        tighten = float(tier_cfg.get("remainder_trailing_stop_tighten", 0.3))
        stop_pct = float(logic_config.get("stop_loss_pct", 2.0)) * tighten
        
        if signal_type == "LONG":
            pos.trailing_stop_price = current_price * (1.0 - stop_pct / 100.0)
        else:
            pos.trailing_stop_price = current_price * (1.0 + stop_pct / 100.0)
        
        print(f"[paper] {getattr(pos, 'underlying', '')} all tiers taken: tight trailing stop set at {pos.trailing_stop_price:.2f}")
    
    return actions


def _resolve_position_market_price(open_pos, quotes_by_symbol: Dict[str, Dict[str, Any]]) -> float:
    """Price an existing position using its current execution ticker, not an incoming replacement ticker."""
    if open_pos is None:
        return 0.0
    execution_ticker = str(getattr(open_pos, "execution_ticker", "") or "").upper()
    underlying = str(getattr(open_pos, "underlying", "") or "").upper()
    price_data = quotes_by_symbol.get(execution_ticker) or {}
    if not price_data and execution_ticker != underlying:
        # execution_ticker not in quotes — try to fetch its price directly
        try:
            from services.data_ingestion.yfinance_client import PriceClient
            pc = PriceClient()
            price_df = pc.get_ohlcv_data_range(
                symbol=execution_ticker,
                start=datetime.now(timezone.utc) - timedelta(minutes=30),
                end=datetime.now(timezone.utc),
                interval="15m",
            )
            if price_df is not None and not price_df.empty and "Close" in price_df.columns:
                current_price = float(price_df["Close"].iloc[-1])
                price_data = {"current_price": current_price, "price": current_price}
        except Exception:
            pass
    if not price_data and execution_ticker == underlying:
        # Last resort: only if no ticker mapping (execution_ticker == underlying)
        price_data = quotes_by_symbol.get(underlying) or {}
    return float(price_data.get("current_price") or price_data.get("price") or 0.0)


def _resolve_underlying_conflicts(
    db,
    quotes_by_symbol: Dict[str, Dict[str, Any]],
    now: datetime,
    alpaca_pending: list,
) -> int:
    """
    Pre-scan all open positions and close any that are conflicting for the same
    underlying (e.g., SPXL bull + SPXS bear both open for SPY).

    Two positions conflict when they share the same `underlying` field but
    represent opposing market directions — either via opposite signal_type
    (LONG vs SHORT) or via execution tickers that map to opposite buckets
    of the same underlying in INSTRUMENT_SPECS.

    When a conflict is found, the older position is closed. Returns the
    count of positions closed.

    Runs unconditionally — even when the market is closed — to prevent
    positions from accumulating across weekend/overnight runs.
    """
    from database.models import PaperTrade
    from services.trading_instruments import build_ticker_bucket_map

    open_positions = (
        db.query(PaperTrade)
        .filter(PaperTrade.exited_at.is_(None))
        .order_by(PaperTrade.entered_at.asc())  # oldest first
        .all()
    )

    # ── Build bucket lookup: execution_ticker → (family underlying, direction bucket) ──
    # Includes both leveraged instrument buckets (INSTRUMENT_SPECS) and sector/
    # stock hedge ETFs (HEDGE_ETF_FAMILY, e.g. PSQ), so a hedge ETF opened
    # against a stock's `underlying` (e.g. PSQ opened for underlying=NVDA)
    # still groups with other positions on the same index family (e.g. TQQQ,
    # underlying=QQQ) for conflict detection.
    _ticker_to_bucket = build_ticker_bucket_map()

    def _conflict_group_key(pos) -> str:
        tick = str(getattr(pos, "execution_ticker", "") or "").upper()
        info = _ticker_to_bucket.get(tick)
        if info:
            return info[0]
        return str(getattr(pos, "underlying", "") or "").upper().strip()

    # Group by resolved family (falls back to the raw `underlying` field for
    # tickers that aren't a known leveraged/hedge instrument).
    by_underlying: Dict[str, List] = {}
    for pos in open_positions:
        key = _conflict_group_key(pos)
        if not key:
            continue
        by_underlying.setdefault(key, []).append(pos)

    def _positions_oppose(a, b) -> bool:
        """Return True if positions a and b are opposing market bets."""
        # Same execution ticker — not opposing
        if str(getattr(a, "execution_ticker", "") or "").upper() == str(getattr(b, "execution_ticker", "") or "").upper():
            return False
        # Check signal_type
        sig_a = str(getattr(a, "signal_type", "") or "").upper()
        sig_b = str(getattr(b, "signal_type", "") or "").upper()
        if sig_a in ("LONG", "SHORT") and sig_b in ("LONG", "SHORT") and sig_a != sig_b:
            return True
        # Check ticker buckets (catches cases where signal_type is wrong but tickers are opposite)
        tick_a = str(getattr(a, "execution_ticker", "") or "").upper()
        tick_b = str(getattr(b, "execution_ticker", "") or "").upper()
        info_a = _ticker_to_bucket.get(tick_a)
        info_b = _ticker_to_bucket.get(tick_b)
        if info_a and info_b and info_a[0] == info_b[0]:
            return info_a[1] != info_b[1]  # one bull, one bear
        return False

    closed_count = 0
    for underlying, positions in by_underlying.items():
        if len(positions) < 2:
            continue

        # Check for opposing pairs within this group
        to_close: set = set()
        for i in range(len(positions)):
            if id(positions[i]) in to_close:
                continue
            for j in range(i + 1, len(positions)):
                if id(positions[j]) in to_close:
                    continue
                if _positions_oppose(positions[i], positions[j]):
                    # Keep the newer one, close the older one
                    older = positions[i]
                    to_close.add(id(older))
                    print(
                        f"[paper] conflict: {underlying} has opposing positions "
                        f"{positions[i].execution_ticker} ({positions[i].signal_type}) vs "
                        f"{positions[j].execution_ticker} ({positions[j].signal_type}) — "
                        f"closing older {older.execution_ticker}"
                    )

        # Also cleanup: if there are >1 remaining for the same underlying, keep the newest
        if len(positions) - len(to_close) > 1:
            # Keep the most recently entered, close the rest
            remaining = [p for p in positions if id(p) not in to_close]
            remaining.sort(key=lambda p: _safe_utc(p.entered_at) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            for extra in remaining[1:]:
                to_close.add(id(extra))
                print(
                    f"[paper] cleanup: {underlying} has {len(remaining)} remaining positions after conflict resolution — "
                    f"closing excess {extra.execution_ticker} (entered {_safe_utc(extra.entered_at)})"
                )

        for pos in positions:
            if id(pos) not in to_close:
                continue
            exit_price = _resolve_position_market_price(pos, quotes_by_symbol)
            if exit_price <= 0:
                exit_price = float(getattr(pos, "entry_price", 0.0) or 0.0)
            if exit_price <= 0:
                print(f"[paper] conflict: cannot close {pos.execution_ticker} — no price available")
                continue
            _close_position(pos, exit_price, now, db, reason="conflict_resolution")
            alpaca_pending.append((pos, "close"))
            closed_count += 1

    if closed_count:
        db.commit()
        print(f"[paper] conflict resolution: closed {closed_count} conflicting position(s)")

    return closed_count


def market_status(allow_extended_hours: bool = True) -> Dict[str, Any]:
    """Return current market session for display and gate-keeping.

    Supports Alpaca's 24/5 schedule: Sunday 8 PM ET → Friday 8 PM ET.
    Sessions:
      - Overnight:  8:00 PM – 4:00 AM ET (wrap-around, starts Sunday evening)
      - Pre-Market: 4:00 AM – 9:30 AM ET
      - Regular:    9:30 AM – 4:00 PM ET
      - After-Hours: 4:00 PM – 8:00 PM ET
    """
    now_et = datetime.now(_MARKET_TZ)
    t = now_et.time()
    weekday = now_et.weekday()  # Mon=0 … Sun=6

    # ── Weekend: only tradeable during Sunday overnight session ──
    if weekday >= 5:
        # Sunday (6) 8 PM – midnight = overnight session for Monday
        if allow_extended_hours and weekday == 6 and t >= _OVERNIGHT_OPEN:
            return {"status": "overnight", "label": "Overnight (Sunday)", "tradeable": True}
        return {"status": "closed", "label": "Closed (Weekend)", "tradeable": False}

    # ── Weekday sessions — Alpaca is 24/5, always tradeable ──────
    if t >= _OVERNIGHT_OPEN or t < _OVERNIGHT_CLOSE:
        return {"status": "overnight", "label": "Overnight", "tradeable": True}
    if _EXTENDED_OPEN <= t < _REGULAR_OPEN:
        return {"status": "pre-market", "label": "Pre-Market", "tradeable": True}
    if _REGULAR_OPEN <= t <= _REGULAR_CLOSE:
        return {"status": "open", "label": "Market Open", "tradeable": True}
    if _REGULAR_CLOSE < t <= _EXTENDED_CLOSE:
        return {"status": "after-hours", "label": "After-Hours", "tradeable": True}
    return {"status": "overnight", "label": "Overnight", "tradeable": True}


def _window_active(pos, now: datetime) -> bool:
    """Return True if the position's conviction holding window has not yet expired."""
    win = _safe_utc(getattr(pos, "holding_window_until", None))
    if not win:
        return False
    return _safe_utc(now) < win


def _same_market_day(a: Optional[datetime], b: Optional[datetime]) -> bool:
    if a is None or b is None:
        return False
    return _safe_utc(a).astimezone(_MARKET_TZ).date() == _safe_utc(b).astimezone(_MARKET_TZ).date()


def _safe_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize any datetime to timezone-aware UTC. Returns None if dt is None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ── OPENING RANGE (ORB) GUARD ───────────────────────────────────────────
# _opening_range_cache[(symbol, YYYY-MM-DD)] = {high, low, bar_count} | None
# The range is fixed once the first `range_minutes` of the day have elapsed,
# so caching per (symbol, day) is correct for the rest of that session.
_opening_range_cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}


def _opening_range_data(symbol: str, now_et: datetime, range_minutes: float) -> Optional[Dict[str, Any]]:
    """Fetch today's opening range (first `range_minutes` of 5m bars) for a symbol.

    Returns {"high", "low", "bar_count"} or None when bars are unavailable or the
    range window has not fully elapsed — callers fail OPEN on None (no block).
    Successful results are cached per (symbol, day); failures are not cached so
    a later run retries.
    """
    day_key = now_et.strftime("%Y-%m-%d")
    cache_key = (str(symbol).upper(), day_key)
    if cache_key in _opening_range_cache:
        return _opening_range_cache[cache_key]

    result: Optional[Dict[str, Any]] = None
    try:
        from services.data_ingestion.yfinance_client import PriceClient
        opened = datetime.combine(now_et.date(), _REGULAR_OPEN, tzinfo=_MARKET_TZ)
        window_end = opened + timedelta(minutes=range_minutes)
        bar_interval = 5.0
        df = PriceClient().get_ohlcv_data_range(
            symbol, start=opened, end=window_end + timedelta(minutes=1), interval="5m"
        )
        if df is not None and not df.empty:
            last_bar_ts = df.index[-1]
            if hasattr(last_bar_ts, "tzinfo") and last_bar_ts.tzinfo is None:
                last_bar_ts = last_bar_ts.tz_localize(_MARKET_TZ)
            elif hasattr(last_bar_ts, "tzinfo"):
                last_bar_ts = last_bar_ts.tz_convert(_MARKET_TZ) if last_bar_ts.tzinfo else last_bar_ts
            # Require the range window to be (nearly) complete: last bar within
            # two bar-intervals of window end. Guards against caching a partial
            # range when the 9:45 bar hasn't printed yet.
            window_complete = (
                hasattr(last_bar_ts, "tzinfo")
                and last_bar_ts.tzinfo is not None
                and last_bar_ts >= window_end - timedelta(minutes=2 * bar_interval)
            )
            if window_complete:
                high = float(df["High"].max())
                low = float(df["Low"].min())
                if high > low > 0:
                    result = {"high": round(high, 4), "low": round(low, 4), "bar_count": int(len(df))}
    except Exception as exc:
        print(f"[paper] opening range fetch failed for {symbol}: {exc}")

    _opening_range_cache[cache_key] = result
    if result is None:
        print(f"[paper] opening range unavailable for {symbol} ({day_key}) — fail open")
    return result


def _opening_range_block(
    underlying: str,
    execution_ticker: str,
    entry_price: float,
    signal_type: str,
    conviction_level: str,
    now: datetime,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Opening Range (ORB) guard for NEW exposure during the regular session.

    Returns (skip_reason, range_info) to BLOCK a new entry, or None to allow.
    - During the first `wait_minutes` after 9:30 ET → block everything.
    - After the range forms: block LOW/MEDIUM entries whose direction is AGAINST
      the opening break (price broke above range → no new SHORT, and vice
      versa). HIGH conviction (high_override) may still enter. Price inside the
      range → neutral, allow.
    - Fail-open: any data problem (no bars, fetch error, price <= 0) returns None.
    """
    or_cfg = _L.get("opening_range") or {}
    if not or_cfg.get("enabled", True):
        return None
    wait_minutes = float(or_cfg.get("wait_minutes", 15))
    range_minutes = float(or_cfg.get("range_minutes", 15))
    if wait_minutes <= 0 and range_minutes <= 0:
        return None
    if entry_price <= 0 or not execution_ticker or not underlying:
        return None

    now_et = _safe_utc(now).astimezone(_MARKET_TZ)
    t = now_et.time()
    if t < _REGULAR_OPEN or t >= _REGULAR_CLOSE or now_et.weekday() >= 5:
        return None  # regular-session only

    opened = datetime.combine(now_et.date(), _REGULAR_OPEN, tzinfo=_MARKET_TZ)
    mins_since_open = (now_et - opened).total_seconds() / 60.0
    if wait_minutes > 0 and mins_since_open < wait_minutes:
        return (
            "opening_wait",
            {"minutes_since_open": round(mins_since_open, 1), "wait_minutes": wait_minutes},
        )
    if range_minutes <= 0:
        return None

    rng = _opening_range_data(execution_ticker, now_et, range_minutes)
    if rng is None:
        return None
    high, low = float(rng["high"]), float(rng["low"])
    min_break = float(or_cfg.get("min_break_pct", 0.2)) / 100.0
    above = entry_price >= high * (1.0 + min_break)
    below = entry_price <= low * (1.0 - min_break)
    side = "above" if above else ("below" if below else "inside")
    rng["price_side"] = side
    rng["current_price"] = round(entry_price, 2)
    if side == "inside":
        return None

    is_long = str(signal_type or "").upper() == "LONG"
    with_break = (side == "above" and is_long) or (side == "below" and not is_long)
    if with_break:
        return None
    if str(conviction_level or "").upper() == "HIGH" and or_cfg.get("high_override", True):
        return None
    return ("opening_range_against", rng)


def _min_same_day_exit_edge_pct(app_config) -> float:
    try:
        override = getattr(app_config, "min_same_day_exit_edge_pct", None) if app_config is not None else None
        if override is not None:
            return max(0.0, float(override))
    except Exception:
        pass
    return max(0.0, float(_L.get("min_same_day_exit_edge_pct", 0.5)))


def _entry_threshold_for_session(session_status: str, app_config) -> float:
    """Return the minimum directional score required to enter a new position.

    Uses app_config entry_threshold override when set, else falls back to
    logic_config defaults.  pre-market/after-hours sessions use the
    closed_market threshold; open sessions use the normal threshold.
    """
    try:
        if app_config is not None:
            override = getattr(app_config, "entry_threshold", None)
            if override is not None:
                return max(0.0, float(override))
    except Exception:
        pass

    # Apply crazy profile entry thresholds if applicable
    is_crazy = False
    try:
        if app_config is not None:
            profile = str(getattr(app_config, "risk_profile", "") or "").strip().lower()
            is_crazy = profile == "crazy"
    except Exception:
        pass

    if is_crazy:
        crazy_cfg = _L.get("crazy", {})
        crazy_et = crazy_cfg.get("entry_thresholds", {})
        if isinstance(crazy_et, dict):
            if session_status in ("pre-market", "after-hours", "overnight"):
                return max(0.0, float(crazy_et.get("closed_market", 0.35)))
            return max(0.0, float(crazy_et.get("normal", 0.35)))

    thresholds = _L.get("entry_thresholds", {})
    if session_status in ("pre-market", "after-hours", "overnight"):
        return max(0.0, float(thresholds.get("closed_market", thresholds.get("normal", 0.42))))
    return max(0.0, float(thresholds.get("normal", 0.42)))


def _stop_loss_pct_for_config(app_config) -> float:
    """Return configured stop-loss percentage, floored at the 2% minimum."""
    try:
        if app_config is not None:
            override = getattr(app_config, "stop_loss_pct", None)
            if override is not None:
                return max(_min_stop_loss_pct(), float(override))
    except Exception:
        pass
    return max(_min_stop_loss_pct(), float(_L.get("stop_loss_pct", 2.0)))


def _take_profit_pct_for_config(app_config) -> float:
    """Return configured take-profit percentage."""
    try:
        if app_config is not None:
            override = getattr(app_config, "take_profit_pct", None)
            if override is not None:
                return max(0.0, float(override))
    except Exception:
        pass
    return max(0.0, float(_L.get("take_profit_pct", 3.0)))


def _same_day_exit_edge_blocks_close(open_pos, exit_price: float, now: datetime, threshold_pct: float) -> bool:
    """
    Block tiny same-day winners from being closed just to churn the account.
    Loss-cutting remains allowed.
    """
    if open_pos is None or threshold_pct <= 0 or exit_price <= 0:
        return False
    entered_at = getattr(open_pos, "entered_at", None)
    if not _same_market_day(entered_at, now):
        return False
    pnl_pct = _directional_return_pct(open_pos.signal_type, float(open_pos.entry_price or 0.0), exit_price)
    return pnl_pct > 0 and pnl_pct < threshold_pct


def _min_trade_size_usd() -> float:
    """Configured minimum notional for a new position; below this the entry is skipped."""
    try:
        return max(0.0, float(_L.get("min_trade_size_usd", 50.0)))
    except Exception:
        return 0.0


def _min_stop_loss_pct() -> float:
    """Configured floor for stop-loss percentages (2.0%)."""
    try:
        return max(0.0, float(_L.get("min_stop_loss_pct", 2.0)))
    except Exception:
        return 0.0


def _flip_should_early_close(conviction_level: str, rec: dict) -> bool:
    """
    Whether a direction flip should close the position early.

    Choice B: flips hold by default ("stay the course") and only close early
    when the NEW signal is HIGH conviction (config early_close_convictions) or
    when a MEDIUM conviction flip arrives with a strong directional score.
    """
    fm = _L.get("flip_management", {}) or {}
    early = {str(x).upper() for x in fm.get("early_close_convictions", ["HIGH"])}
    level = str(conviction_level or "").upper()
    if level in early:
        return True
    if level == "MEDIUM":
        score = float(rec.get("directional_score") or 0.0)
        threshold = float(fm.get("flip_early_close_med_score", 0.60))
        if abs(score) >= threshold:
            return True
    return False


def _apply_flip_hold(open_pos, conviction_level: str, trading_type: str,
                     holding_minutes: int, now: datetime, logic_config: dict) -> dict:
    """
    Refresh a held-through-flip position from the new thesis.

    Keeps the position open, updates its conviction/trading_type to the new
    signal, refreshes the holding window (capped by
    flip_hold_max_extension_minutes) and resets the trailing-stop baseline so
    it reflects the new thesis rather than the old entry.
    """
    fcfg = logic_config.get("flip_management", {}) or {}
    max_ext_min = int(fcfg.get("flip_hold_max_extension_minutes", 240))
    _cv = logic_config["conviction"]

    _type_rank = {"VOLATILE_EVENT": 0, "SCALP": 1, "SWING": 2, "POSITION": 3}
    old_rank = _type_rank.get(str(getattr(open_pos, "trading_type", "SWING") or "SWING").upper(), 2)
    new_rank = _type_rank.get(str(trading_type or "SWING").upper(), 2)
    _max_mins = _cv.get("max_holding_minutes", {}).get(trading_type, holding_minutes * 3)
    entered_naive = _safe_utc(getattr(open_pos, "entered_at", None))
    hard_cap = entered_naive + timedelta(minutes=_max_mins) if entered_naive else None
    proposed = now + timedelta(minutes=holding_minutes)
    if new_rank >= old_rank:
        new_window = min(proposed, hard_cap) if hard_cap else proposed
    else:
        cur_win = _safe_utc(getattr(open_pos, "holding_window_until", None))
        new_window = min(cur_win, proposed) if cur_win else proposed

    # Cap the extension so a position can't run forever on repeated flips.
    if max_ext_min > 0:
        cap = now + timedelta(minutes=max_ext_min)
        new_window = min(new_window, cap)

    open_pos.conviction_level = conviction_level
    open_pos.trading_type = trading_type
    open_pos.holding_window_until = new_window
    # Fresh trailing baseline from the new thesis.
    open_pos.trailing_stop_price = None
    open_pos.best_price_seen = None

    return {
        "reason": "flip_hold_window_refreshed",
        "holding_window_until": new_window,
        "held_as": conviction_level,
    }


def _portfolio_cap_for_config(app_config) -> Optional[float]:
    """Return the configured portfolio cap in USD, or None if uncapped."""
    try:
        if app_config is not None:
            override = getattr(app_config, "vol_sizing_portfolio_cap_usd", None)
            if override is not None:
                return max(0.0, float(override))
    except Exception:
        pass
    cap = _L.get("vol_sizing", {}).get("portfolio_cap_usd")
    return max(0.0, float(cap)) if cap is not None else None


def _get_alpaca_system_open_exposure(broker, db, app_config=None) -> Optional[float]:
    """Return total |market_value| of Alpaca positions in tickers this system trades.

    Covers both the fixed execution tickers from INSTRUMENT_SPECS (TQQQ, SPXL, etc.)
    and any custom symbols the user has added (NVDA, TSLA, etc.). Positions opened
    outside this system are still counted — the risk is real regardless of who opened
    them — and logged as a warning. Returns None on API error so the caller falls back
    to the DB total.
    """
    try:
        from services.trading_instruments import INSTRUMENT_SPECS
        from database.models import PaperTrade as _PT

        # Fixed execution tickers (leveraged ETF proxies for default symbols).
        _our_tickers: set = set()
        for spec in INSTRUMENT_SPECS.values():
            for direction in ("bull", "bear"):
                _our_tickers.update(spec.get(direction, {}).values())

        # Custom symbols trade directly under their own ticker.
        if app_config is not None:
            for sym in (getattr(app_config, "custom_symbols", None) or []):
                sym = str(sym or "").upper().strip()
                if sym:
                    _our_tickers.add(sym)

        _db_tickers = {
            str(p.execution_ticker or "").upper()
            for p in db.query(_PT).filter(_PT.exited_at.is_(None)).all()
            if p.execution_ticker
        }

        positions = broker.get_positions()
        total = 0.0
        for p in positions:
            sym = str(p.get("symbol") or "").upper()
            if sym not in _our_tickers:
                continue
            mv = abs(float(p.get("market_value") or 0))
            total += mv
            if sym not in _db_tickers:
                print(
                    f"[cap] external position in {sym} (${mv:.2f}) not opened by this system"
                    f" — counting against portfolio cap"
                )
        return total
    except Exception as exc:
        print(f"[cap] could not fetch Alpaca positions for exposure baseline: {exc}")
        return None


def _compute_vol_normalized_amount(
    base_amount: float,
    conviction_level: str,
    atr_pct: float,
) -> float:
    """
    Compute position size using volatility targeting.

    Formula: size = (target_daily_vol_pct/100 * base) / (atr_14d_pct/100)
    Scaled by conviction level, then clamped to [min_mult, max_mult] × base.

    When ATR is unavailable (0), falls back to conviction-scaled base amount.
    """
    cfg = _L.get("vol_sizing", {})
    if not cfg.get("enabled", True):
        return base_amount

    target_vol = float(cfg.get("target_daily_vol_pct", 1.0)) / 100.0
    scalars = cfg.get("conviction_scalars", {"HIGH": 1.5, "MEDIUM": 1.0, "LOW": 0.5})
    conviction_scalar = float(scalars.get(str(conviction_level).upper(), 1.0))
    min_mult = float(cfg.get("min_size_multiple", 0.25))
    max_mult = float(cfg.get("max_size_multiple", 5.0))

    if atr_pct > 0:
        vol_size = (target_vol * base_amount) / (atr_pct / 100.0)
    else:
        vol_size = base_amount

    scaled = vol_size * conviction_scalar
    return round(max(base_amount * min_mult, min(base_amount * max_mult, scaled)), 2)


def close_expired_positions(db, alpaca_pending: Optional[list] = None) -> List[Dict[str, Any]]:
    """
    Close any open positions whose conviction window has expired.
    Called at the start of each analysis run and from process_signals.
    Respects logic_config: close_on_window_expiry and close_expired_during_closed_hours.

    alpaca_pending: if provided, (trade_obj, "close") tuples for actual closes
    (not trailing activations) are appended so the caller can forward them to Alpaca.
    """
    from database.models import PaperTrade
    from services.data_ingestion.yfinance_client import PriceClient

    _cv = _L["conviction"]
    if not _cv.get("close_on_window_expiry", True):
        return []

    session = market_status(_allow_extended_hours_trading(db))
    _hold_overnight = False
    _cfg = None
    try:
        from services.app_config import get_or_create_app_config as _get_cfg
        _cfg = _get_cfg(db)
        _hold_overnight = bool(getattr(_cfg, "hold_overnight", False))
    except Exception:
        pass
    if not session["tradeable"] and (_hold_overnight or not _cv.get("close_expired_during_closed_hours", True)):
        return []

    _trail_on_expiry = bool(_cv.get("trail_on_window_expiry", True))
    if _cfg is not None:
        try:
            _trail_on_expiry = bool(getattr(_cfg, "trail_on_window_expiry", _trail_on_expiry))
        except Exception:
            pass

    now_utc = _safe_utc(datetime.now(timezone.utc))

    open_positions = (
        db.query(PaperTrade)
        .filter(PaperTrade.exited_at.is_(None), PaperTrade.holding_window_until.isnot(None))
        .all()
    )

    expired = []
    for pos in open_positions:
        win = _safe_utc(pos.holding_window_until)
        if win is None:
            continue
        if now_utc >= win:
            expired.append(pos)

    if not expired:
        return []

    price_client = PriceClient()
    closed = []
    _ts_cfg = _L.get("trailing_stop", {})
    _tight_pct = float(_ts_cfg.get("tighten_factor", 0.5)) * float(_L["stop_loss_pct"]) / 100.0
    for pos in expired:
        exit_price = 0.0
        try:
            quote = price_client.get_realtime_quote(pos.execution_ticker)
            exit_price = float((quote or {}).get("current_price") or 0.0)
        except Exception:
            exit_price = 0.0
        if exit_price <= 0:
            exit_price = float(pos.entry_price or 0.0)
        if exit_price <= 0:
            continue
        if _trail_on_expiry:
            # Activate trailing stop instead of closing — lets winners run
            if pos.signal_type == "LONG":
                cur_best = float(pos.best_price_seen or 0) or float(pos.entry_price or 0)
                best = max(cur_best, exit_price)
                new_stop = round(best * (1.0 - _tight_pct), 4)
            else:
                cur_best = float(pos.best_price_seen or 0)
                best = min(cur_best, exit_price) if cur_best > 0 else exit_price
                new_stop = round(best * (1.0 + _tight_pct), 4)
            # Extend the holding window far into the future instead of setting to None.
            # Setting to None breaks _window_active() which returns False for None,
            # causing subsequent HOLD signals in the same run to mishandle the position.
            # Using 999 days effectively makes the window "never expire" while keeping
            # _window_active() returning True so trailing-stop logic still works.
            pos.best_price_seen = best
            pos.trailing_stop_price = new_stop
            pos.holding_window_until = now_utc + timedelta(days=999)  # effectively permanent
            closed.append({
                "underlying": pos.underlying,
                "execution_ticker": pos.execution_ticker,
                "signal_type": pos.signal_type,
                "exit_price": exit_price,
                "realized_pnl": None,
                "reason": "trailing_activated",
                "trailing_stop_price": new_stop,
            })
        else:
            _close_position(pos, exit_price, now_utc, db, reason="window_expired")
            if alpaca_pending is not None:
                alpaca_pending.append((pos, "close"))
            closed.append({
                "underlying": pos.underlying,
                "execution_ticker": pos.execution_ticker,
                "signal_type": pos.signal_type,
                "exit_price": exit_price,
                "realized_pnl": pos.realized_pnl,
                "reason": "window_expired",
            })

    if closed:
        db.commit()
    return closed


def process_signals(
    db,
    recommendations: List[Dict[str, Any]],
    quotes_by_symbol: Dict[str, Dict[str, Any]],
    request_id: str,
    trade_amount: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Process all per-symbol recommendations from one analysis run.

    recommendations: list of dicts with keys:
        underlying, execution_ticker, signal_type (LONG/SHORT/HOLD), leverage,
        conviction_level (HIGH/MEDIUM/LOW), trading_type, holding_minutes

    Position lifecycle:
    - Same ticker + same leverage + same direction → hold (no change)
    - Direction flip → always close old and open new (overrides conviction window)
    - HOLD signal + active conviction window → keep position (window protects it)
    - HOLD signal + expired/no window → close position, go flat
    """
    from database.models import PaperTrade

    _cv = _L["conviction"]

    # Load app config once — used for re-entry cooldown and Alpaca dispatch
    _app_config = None
    try:
        from services.app_config import get_or_create_app_config as _get_cfg_rc
        _app_config = _get_cfg_rc(db)
    except Exception:
        pass

    # Re-entry cooldown: same-direction re-entry blocked for this many minutes after a close
    _reentry_cooldown = int(_L.get("reentry_cooldown_minutes", 0))
    if _app_config is not None:
        _rc_override = getattr(_app_config, "reentry_cooldown_minutes", None)
        if _rc_override is not None:
            _reentry_cooldown = int(_rc_override)
    _min_same_day_edge_pct = _min_same_day_exit_edge_pct(_app_config)

    # Collect (paper_trade_obj, "open"|"close") for Alpaca dispatch after commit
    _alpaca_pending: list = []

    # ── CONFLICT RESOLUTION (runs unconditionally, even when market is closed) ──
    # Scan all open positions and close any that share the same underlying with
    # opposing directions (e.g., SPXL bull + SPXS bear for SPY). This prevents
    # positions from accumulating across weekend/overnight runs.
    now = datetime.now(timezone.utc)
    _resolve_underlying_conflicts(db, quotes_by_symbol, now, _alpaca_pending)

    # ── INTRA-RUN CONFLICT RESOLUTION ─────────────────────────────────────
    # Detect opposing signals (LONG + SHORT) for the same underlying within
    # this same analysis run BEFORE any new positions are created. Multiple
    # concurrent analysis runs may have already populated _alpaca_pending with
    # conflicting PaperTrade records; we also detect signals against each other
    # to prevent the case where Run A opens LONG and Run B opens SHORT for the
    # same underlying in the same dispatch cycle.
    _signals_by_underlying: Dict[str, List[Dict[str, Any]]] = {}
    for rec in recommendations:
        _rec_underlying = str(rec.get("underlying") or rec.get("symbol") or "").upper()
        if not _rec_underlying:
            continue
        _signals_by_underlying.setdefault(_rec_underlying, []).append(rec)

    _opposing_underlyings: set = set()
    for _rec_underlying, _sigs in _signals_by_underlying.items():
        _has_long = any(str(s.get("signal_type") or "HOLD").upper() == "LONG" for s in _sigs)
        _has_short = any(str(s.get("signal_type") or "HOLD").upper() == "SHORT" for s in _sigs)
        if _has_long and _has_short:
            _opposing_underlyings.add(_rec_underlying)
            print(
                f"[paper] INTRA-RUN CONFLICT: opposing signals for {_rec_underlying} "
                f"(LONG + SHORT) — keeping LONG, blocking SHORT"
            )

    # Filter out SHORT signals for underlyings that also have LONG signals
    _filtered_recommendations = []
    for rec in recommendations:
        _rec_underlying = str(rec.get("underlying") or rec.get("symbol") or "").upper()
        _signal_type = str(rec.get("signal_type") or "HOLD").upper()
        if _rec_underlying in _opposing_underlyings and _signal_type == "SHORT":
            print(f"[paper] BLOCKED: SHORT signal for {_rec_underlying} due to intra-run conflict")
            continue
        _filtered_recommendations.append(rec)

    recommendations = _filtered_recommendations
    # ─────────────────────────────────────────────────────────────────────────────

    # Always check for expired windows first, even if market is closed.
    # Pass _alpaca_pending so actual window-expired closes are queued for Alpaca
    # dispatch at the end of this function alongside all other lifecycle events.
    expired_actions = close_expired_positions(db, alpaca_pending=_alpaca_pending)

    def _expired_action(ea: Dict[str, Any]) -> Dict[str, Any]:
        action = "trailing" if ea.get("reason") == "trailing_activated" else "closed"
        return {**ea, "action": action, "auto_expired": True}

    session = market_status(_allow_extended_hours_trading(db))
    if not session["tradeable"]:
        print(f"[paper] signals skipped — market not tradeable ({session['label']})")
        return [_expired_action(ea) for ea in expired_actions] or [
            {"skipped": True, "reason": "market_closed", "session": session["label"]}
        ]

    actions: List[Dict[str, Any]] = [_expired_action(ea) for ea in expired_actions]

    # Portfolio cap — seed running exposure from Alpaca when connected so external
    # positions in our execution tickers count against the cap. Falls back to DB.
    _portfolio_cap = _portfolio_cap_for_config(_app_config)
    _open_exposure = 0.0
    if _portfolio_cap is not None:
        _alpaca_mode = (getattr(_app_config, "alpaca_execution_mode", None) or "off")
        _alpaca_exposure: Optional[float] = None
        if _alpaca_mode != "off":
            try:
                from services.alpaca_broker import get_broker_from_keychain as _gbfk
                _cap_broker = _gbfk(mode=_alpaca_mode)
                if _cap_broker:
                    _alpaca_exposure = _get_alpaca_system_open_exposure(_cap_broker, db, _app_config)
            except Exception:
                pass
        if _alpaca_exposure is not None:
            _open_exposure = _alpaca_exposure
        else:
            try:
                from database.models import PaperTrade as _PT
                _open_exposure = sum(
                    float(p.amount or 0)
                    for p in db.query(_PT).filter(_PT.exited_at.is_(None)).all()
                )
            except Exception:
                _open_exposure = 0.0

    for rec in recommendations:
        underlying = str(rec.get("underlying") or rec.get("symbol") or "").upper()
        execution_ticker = str(rec.get("execution_ticker") or rec.get("entry_symbol") or "").upper()
        signal_type = str(rec.get("signal_type") or "HOLD").upper()
        leverage = str(rec.get("leverage") or "1x")

        # ── SHORT → inverse ETF mapping ────────────────────────────────
        # When signal_type is SHORT and execution_ticker == underlying,
        # no inverse ETF was pre-mapped by the recommendation engine.
        # Look up the inverse ETF now so paper trades record the actual
        # ticker being traded and the dashboard can display it.
        if signal_type == "SHORT" and execution_ticker == underlying:
            from services.alpaca_broker import INVERSE_ETF_MAP
            inverse_etf = INVERSE_ETF_MAP.get(underlying)
            if inverse_etf:
                execution_ticker = inverse_etf
                print(
                    f"[paper] SHORT→inverse ETF: {underlying} → {execution_ticker} "
                    f"(underlying tracked, ETF used for execution)"
                )

        conviction_level = str(rec.get("conviction_level") or "MEDIUM").upper()
        trading_type = str(rec.get("trading_type") or "SWING").upper()
        holding_minutes = int(rec.get("holding_minutes") or _cv["holding_minutes"].get(trading_type, 720))

        if not underlying:
            continue

        # ── CRON OVERLAP GUARD ──────────────────────────────────────────
        # Prevents duplicate orders when multiple analysis runs fire
        # simultaneously (cron overlap).  Dedup key is (underlying,
        # normalized_signal) so that only the FIRST run for this signal
        # type creates the PaperTrade; subsequent runs within the grace
        # window skip silently.
        #
        # CRITICAL: Also blocks ANY signal for the same underlying within
        # the grace window to prevent opposing positions (SPXL long +
        # SPXS short) from being opened by concurrent runs.  This is
        # the fix for the race condition where two runs could
        # simultaneously open opposing positions before either commits.
        _OVERLAP_GRACE_MINUTES = 5
        _now_utc = _safe_utc(datetime.now(timezone.utc))
        
        # Check underlying-level lock first (blocks LONG+SHORT concurrently)
        _underlying_key = underlying
        _underlying_prev = _cron_overlap_underlying_keys.get(_underlying_key)
        if _underlying_prev is not None and _now_utc < _underlying_prev + timedelta(minutes=_OVERLAP_GRACE_MINUTES):
            _skip_summary = {
                "underlying": underlying,
                "execution_ticker": execution_ticker,
                "signal_type": signal_type,
                "leverage": leverage,
                "conviction_level": conviction_level,
                "trading_type": trading_type,
                "session": session["label"],
                "action": "skipped",
                "reason": "cron_overlap_guard_underlying",
            }
            print(
                f"[paper] {underlying} {signal_type}: skipped — cron overlap guard "
                f"(underlying already processed within {_OVERLAP_GRACE_MINUTES}min)"
            )
            actions.append(_skip_summary)
            continue
        
        # Check signal-type-level lock (original behavior)
        _overlap_key = (underlying, signal_type)
        _prev = _cron_overlap_keys.get(_overlap_key)
        if _prev is not None and _now_utc < _prev + timedelta(minutes=_OVERLAP_GRACE_MINUTES):
            _skip_summary = {
                "underlying": underlying,
                "execution_ticker": execution_ticker,
                "signal_type": signal_type,
                "leverage": leverage,
                "conviction_level": conviction_level,
                "trading_type": trading_type,
                "session": session["label"],
                "action": "skipped",
                "reason": "cron_overlap_guard",
            }
            print(
                f"[paper] {underlying} {signal_type}: skipped — cron overlap guard "
                f"(same signal within {_OVERLAP_GRACE_MINUTES}min)"
            )
            actions.append(_skip_summary)
            continue
        
        # Set BOTH locks: underlying (blocks concurrent opposing signals)
        # and signal-type (blocks duplicate same-signal entries)
        _cron_overlap_underlying_keys[_underlying_key] = _now_utc
        _cron_overlap_keys[_overlap_key] = _now_utc

        # Get price for the execution ticker (the actual security being traded).
        # If execution_ticker differs from underlying (e.g. PSQ for NVDA SHORT),
        # we MUST use execution_ticker's price, not underlying's price.
        price_data = quotes_by_symbol.get(execution_ticker) or {}
        if not price_data:
            # execution_ticker not in quotes_by_symbol — try to fetch its price directly
            try:
                from services.data_ingestion.yfinance_client import PriceClient
                pc = PriceClient()
                price_df = pc.get_ohlcv_data_range(
                    symbol=execution_ticker,
                    start=datetime.now(timezone.utc) - timedelta(minutes=30),
                    end=datetime.now(timezone.utc),
                    interval="15m",
                )
                if price_df is not None and not price_df.empty and "Close" in price_df.columns:
                    current_price = float(price_df["Close"].iloc[-1])
                    price_data = {"current_price": current_price, "price": current_price}
                    print(f"[paper] Fetched price for {execution_ticker}: ${current_price:.2f}")
            except Exception as _price_err:
                print(f"[paper] Warning: could not fetch price for {execution_ticker}: {_price_err}")
        if not price_data:
            # Last resort: only if execution_ticker == underlying (no mapping)
            if execution_ticker == underlying:
                price_data = quotes_by_symbol.get(underlying) or {}
        entry_price = float(price_data.get("current_price") or price_data.get("price") or 0.0)

        open_positions = (
            db.query(PaperTrade)
            .filter(PaperTrade.underlying == underlying, PaperTrade.exited_at.is_(None))
            .order_by(PaperTrade.entered_at.desc())
            .all()
        )
        
        open_pos = open_positions[0] if open_positions else None
        
        # Clean up any rogue simultaneous positions for the same underlying
        if len(open_positions) > 1:
            for p in open_positions[1:]:
                p_price = _resolve_position_market_price(p, quotes_by_symbol)
                if p_price > 0:
                    _close_position(p, p_price, now, db, reason="Simultaneous position cleanup")
                    _alpaca_pending.append((p, "close"))

        action_summary: Dict[str, Any] = {
            "underlying": underlying,
            "execution_ticker": execution_ticker,
            "signal_type": signal_type,
            "leverage": leverage,
            "conviction_level": conviction_level,
            "trading_type": trading_type,
            "session": session["label"],
        }

        # ── Trailing stop check (before signal processing) ────────────────────
        _prev_signal_type = open_pos.signal_type if open_pos else None
        _trailing_stop_hit = False
        existing_pos_price = _resolve_position_market_price(open_pos, quotes_by_symbol)

        if open_pos and open_pos.trailing_stop_price is not None and existing_pos_price > 0:
            stop_px = float(open_pos.trailing_stop_price or 0)
            if stop_px > 0:
                _trailing_stop_hit = (
                    (open_pos.signal_type == "LONG" and existing_pos_price <= stop_px) or
                    (open_pos.signal_type == "SHORT" and existing_pos_price >= stop_px)
                )
            if _trailing_stop_hit:
                _close_position(open_pos, existing_pos_price, now, db, reason="trailing_stop_hit")
                action_summary["closed_pnl"] = open_pos.realized_pnl
                action_summary["exit_price"] = existing_pos_price
                _alpaca_pending.append((open_pos, "close"))
                open_pos = None
                # If new signal is HOLD or same direction, stay flat this run
                if signal_type == "HOLD" or signal_type == _prev_signal_type:
                    action_summary["action"] = "closed"
                    action_summary["reason"] = "trailing_stop_hit"
                    actions.append(action_summary)
                    continue
                # Direction flip after stop: fall through to open new position below
                action_summary["reason"] = "trailing_stop_hit_then_flip"

        # ── Tiered profit scaling check ─────────────────────────────────
        if open_pos and open_pos.amount and float(open_pos.amount) > 0:
            _tier_actions = _check_tiered_profit_scaling(open_pos, quotes_by_symbol, _L, db)
            if _tier_actions:
                for _tier_action in _tier_actions:
                    actions.append({
                        "underlying": underlying,
                        "execution_ticker": execution_ticker,
                        "action": "partial_close",
                        "tier_pnl_pct": _tier_action.get("tier_pnl_pct"),
                        "close_amount": _tier_action.get("close_amount"),
                        "remaining_amount": _tier_action.get("remaining_amount"),
                        "realized_pnl": _tier_action.get("realized_pnl"),
                        "session": session["label"]
                    })
                db.commit()

        # ── HOLD signal ───────────────────────────────────────────────────────
        if signal_type == "HOLD":
            # Data gap protection: when article count dropped significantly,
            # don't close positions — preserve them until adequate data returns.
            data_gap_hold = str(rec.get("data_gap_hold") or "").lower() == "true"
            if data_gap_hold and open_pos:
                action_summary["action"] = "held"
                action_summary["reason"] = "data_gap_hold"
                print(f"[paper] {underlying}: HOLD (data gap — preserving position)")
            elif (
                open_pos
                and _cv.get("hold_signal_respects_window", True)
                and _window_active(open_pos, now)
            ):
                action_summary["action"] = "held"
                action_summary["reason"] = "conviction_window_active"
                action_summary["holding_window_until"] = _utc_iso(open_pos.holding_window_until)
            elif open_pos:
                # HOLD with no active window — set trailing stop instead of forcing close
                _pos_prices = quotes_by_symbol.get(open_pos.execution_ticker) or quotes_by_symbol.get(underlying) or {}
                current_px = float(_pos_prices.get("current_price") or _pos_prices.get("price") or 0.0)
                if current_px > 0:
                    _ts_cfg = _L.get("trailing_stop", {})
                    _tight_pct = float(_ts_cfg.get("tighten_factor", 0.5)) * float(_L["stop_loss_pct"]) / 100.0
                    if open_pos.signal_type == "LONG":
                        cur_best = float(open_pos.best_price_seen or 0) or float(open_pos.entry_price or 0)
                        best = max(cur_best, current_px)
                        new_stop = round(best * (1.0 - _tight_pct), 4)
                    else:
                        cur_best = float(open_pos.best_price_seen or 0)
                        best = min(cur_best, current_px) if cur_best > 0 else current_px
                        new_stop = round(best * (1.0 + _tight_pct), 4)
                    open_pos.best_price_seen = best
                    open_pos.trailing_stop_price = new_stop
                    action_summary["action"] = "trailing"
                    action_summary["reason"] = "hold_signal_trailing_stop"
                    action_summary["trailing_stop_price"] = new_stop

                    # ── Decision Log: trailing stop event ──────────────────
                    try:
                        from database.engine import DecisionLogSessionLocal
                        from database.models import DecisionLogTrade
                        from services.decision_logger import logger as _dl2
                        _ddb2 = DecisionLogSessionLocal()
                        try:
                            _tl2 = _ddb2.query(DecisionLogTrade).filter(
                                DecisionLogTrade.paper_trade_id == open_pos.id
                            ).first()
                            if _tl2:
                                _dl2.log_trade_event(
                                    _ddb2,
                                    trade_log_id=_tl2.id,
                                    event_type="trailing_stop_set",
                                    run_id=request_id if 'request_id' in dir() else None,
                                    keep_vs_close="hold_with_trailing_stop",
                                    decision_reason=(
                                        f"HOLD signal, trailing stop set: best={best:.2f}, "
                                        f"stop={new_stop:.2f}, tighten={_tight_pct:.4f}"
                                    ),
                                    event_details={
                                        "best_price_seen": best,
                                        "trailing_stop_price": new_stop,
                                        "tighten_factor_pct": _tight_pct,
                                        "current_price": current_px,
                                    },
                                )
                                _ddb2.commit()
                        except Exception as _dlx2:
                            _ddb2.rollback()
                        finally:
                            _ddb2.close()
                    except Exception:
                        pass
                else:
                    action_summary["action"] = "held"
                    action_summary["reason"] = "hold_signal_no_price"
            else:
                action_summary["action"] = "no_change"
                action_summary["reason"] = "hold_signal_no_position"
            actions.append(action_summary)
            continue

        # ── Directional signal ────────────────────────────────────────────────
        position_unchanged = (
            open_pos is not None
            and open_pos.execution_ticker == execution_ticker
            and open_pos.leverage == leverage
            and open_pos.signal_type == signal_type
        )

        _base_amount = trade_amount if trade_amount and trade_amount > 0 else _L["paper_trade_amount"]
        _atr_pct = float(rec.get("atr_pct") or 0.0)

        if position_unchanged:
            # ── Opening range guard: no NEW exposure (accumulation) during
            # the opening wait / against the opening break. Existing position
            # is left untouched when blocked (window just isn't refreshed).
            _or_block = _opening_range_block(
                underlying, execution_ticker, entry_price, signal_type, conviction_level, now
            )
            if _or_block:
                _or_reason, _or_info = _or_block
                action_summary["action"] = "skipped"
                action_summary["reason"] = _or_reason
                action_summary["opening_range"] = _or_info
                print(
                    f"[paper] {underlying} {signal_type}: skipped — {_or_reason} "
                    f"(no accumulation {_or_info})"
                )
                actions.append(action_summary)
                continue

            # ── Accumulation on re-confirmation ────────────────────────
            # When enabled, re-confirmed signals add additional shares up to
            # max_multiplier × original_amount. The entry price is blended.
            _accumulate_enabled = True
            _accum_max_mult = 5.0
            _ac_cfg = _L.get("accumulate_on_confirmation", {})
            if _ac_cfg:
                _accumulate_enabled = bool(_ac_cfg.get("enabled", True))
                _accum_max_mult = float(_ac_cfg.get("max_multiplier", 5.0))
            if _app_config is not None:
                try:
                    _ac_override = getattr(_app_config, "accumulate_on_confirmation_enabled", None)
                    if _ac_override is not None:
                        _accumulate_enabled = bool(_ac_override)
                    _ac_mult_override = getattr(_app_config, "accumulate_max_multiplier", None)
                    if _ac_mult_override is not None:
                        _accum_max_mult = max(1.0, float(_ac_mult_override))
                except Exception:
                    pass

            if _accumulate_enabled and entry_price > 0:
                # Ensure original_amount is set (first time or legacy position)
                _orig = float(getattr(open_pos, "original_amount", None) or open_pos.amount or 0)
                if open_pos.original_amount is None:
                    open_pos.original_amount = _orig
                _cur_amount = float(open_pos.amount or 0)
                _max_amount = _orig * _accum_max_mult

                # Compute the new desired size from the signal's size_pct
                _new_size_pct = float(rec.get("size_pct", "100.0") or "100.0") / 100.0
                _new_suggested = _compute_vol_normalized_amount(
                    _base_amount, conviction_level, _atr_pct
                ) * _new_size_pct

                # Apply ramp stage cap; also update position's stage if promoted
                _rec_ramp_stage = str(rec.get("ramp_stage") or "probe")
                _pos_ramp_stage = str(getattr(open_pos, "ramp_stage", None) or "probe")
                _stage_order = {"probe": 0, "building": 1, "full": 2}
                _rec_rank = _stage_order.get(_rec_ramp_stage, 0)
                _pos_rank = _stage_order.get(_pos_ramp_stage, 0)
                if _rec_rank > _pos_rank:
                    open_pos.ramp_stage = _rec_ramp_stage
                    open_pos.ramp_promotion_count = (int(getattr(open_pos, "ramp_promotion_count", None) or 0) + 1)
                    print(f"[paper] {underlying}: ramp promoted {_pos_ramp_stage}→{_rec_ramp_stage} (×{open_pos.ramp_promotion_count})")
                _effective_ramp_stage = str(getattr(open_pos, "ramp_stage", None) or "probe")
                _ramp_cap = {"probe": 0.25, "building": 0.60, "full": 1.0}.get(_effective_ramp_stage, 1.0)

                # Keep _new_suggested uncapped for comparison against _cur_amount.
                # Only apply the ramp cap to the actual accumulation delta so we
                # don't accumulate when the full-size signal would exceed capacity.
                _accum_amount = 0.0
                if _new_suggested > _cur_amount and _cur_amount < _max_amount:
                    _raw_accum = min(_new_suggested - _cur_amount, _max_amount - _cur_amount)
                    _accum_amount = max(_raw_accum * _ramp_cap, 1.0)

                    # Cap by alpaca_max_position_usd if configured
                    _max_pos_usd = None
                    if _app_config is not None:
                        try:
                            _max_pos_usd = getattr(_app_config, "alpaca_max_position_usd", None)
                            if _max_pos_usd is not None:
                                _max_pos_usd = float(_max_pos_usd)
                        except Exception:
                            _max_pos_usd = None
                    if _max_pos_usd is not None and _max_pos_usd > 0:
                        _accum_amount = min(_accum_amount, max(0.0, _max_pos_usd - _cur_amount))

                    # Cap by portfolio cap remaining
                    if _portfolio_cap is not None:
                        _cap_remaining = max(0.0, _portfolio_cap - _open_exposure)
                        _accum_amount = min(_accum_amount, _cap_remaining)

                    if _accum_amount > 0:
                        _accum_shares = round(_accum_amount / entry_price, 6)
                        # Blend entry price: (old_amount * old_price + new_amount * new_price) / (old_amount + new_amount)
                        _new_total_amount = _cur_amount + _accum_amount
                        _new_entry_price = round(
                            (_cur_amount * open_pos.entry_price + _accum_amount * entry_price) / _new_total_amount, 4
                        ) if _new_total_amount > 0 else entry_price

                        open_pos.amount = _new_total_amount
                        open_pos.shares = round(open_pos.shares + _accum_shares, 6)
                        open_pos.entry_price = _new_entry_price

                        if _portfolio_cap is not None:
                            _open_exposure += _accum_amount

                        action_summary["accumulated_amount"] = round(_accum_amount, 2)
                        action_summary["accumulated_shares"] = round(_accum_shares, 6)
                        action_summary["total_amount"] = round(_new_total_amount, 2)
                        action_summary["blended_entry_price"] = _new_entry_price
                        print(
                            f"[paper] {underlying} {signal_type}: accumulated "
                            f"${_accum_amount:.2f} ({_accum_shares:.6f} shares) "
                            f"→ total ${_new_total_amount:.2f}, blended entry ${_new_entry_price:.4f}"
                        )
                        # Queue the delta as an Alpaca open so the live account
                        # also adds to the position when accumulation happens.
                        _alpaca_pending.append((_types.SimpleNamespace(
                            execution_ticker=open_pos.execution_ticker,
                            underlying=open_pos.underlying,
                            signal_type=open_pos.signal_type,
                            conviction_level=conviction_level,
                            amount=_accum_amount,
                            shares=_accum_shares,
                            entry_price=entry_price,
                            id=open_pos.id,
                        ), "open"))

            # Optionally reset the holding window when the thesis is re-confirmed
            if _cv.get("reset_window_on_confirmation", True):
                _type_rank = {"VOLATILE_EVENT": 0, "SCALP": 1, "SWING": 2, "POSITION": 3}
                old_rank = _type_rank.get((open_pos.trading_type or "SWING").upper(), 2)
                new_rank = _type_rank.get(trading_type.upper(), 2)
                _max_mins = _cv.get("max_holding_minutes", {}).get(trading_type, holding_minutes * 3)
                entered_naive = _safe_utc(open_pos.entered_at)
                hard_cap = entered_naive + timedelta(minutes=_max_mins) if entered_naive else None
                proposed = now + timedelta(minutes=holding_minutes)
                if new_rank >= old_rank:
                    new_window = min(proposed, hard_cap) if hard_cap else proposed
                else:
                    cur_win = _safe_utc(open_pos.holding_window_until)
                    new_window = min(cur_win, proposed) if cur_win else proposed
                open_pos.holding_window_until = new_window
                open_pos.conviction_level = conviction_level
                open_pos.trading_type = trading_type
                # Thesis re-confirmed: clear any trailing stop
                open_pos.trailing_stop_price = None
                open_pos.best_price_seen = None
                action_summary["action"] = "accumulated" if action_summary.get("accumulated_amount", 0) > 0 else "held"
                action_summary["reason"] = "window_reset" if new_rank >= old_rank else "window_shortened"
                action_summary["holding_window_until"] = _utc_iso(new_window)
                if action_summary.get("accumulated_amount", 0) > 0:
                    action_summary["reason"] = "accumulated_" + action_summary["reason"]
            else:
                action_summary["action"] = "accumulated" if action_summary.get("accumulated_amount", 0) > 0 else "held"
                action_summary["reason"] = "accumulated" if action_summary.get("accumulated_amount", 0) > 0 else "same_ticker_leverage_direction"
            actions.append(action_summary)
            continue

        # Re-entry cooldown: skip same-direction re-entry if too soon after a close
        if entry_price > 0 and _reentry_cooldown > 0:
            _recent = (
                db.query(PaperTrade)
                .filter(
                    PaperTrade.underlying == underlying,
                    PaperTrade.exited_at.isnot(None),
                    PaperTrade.signal_type == signal_type,
                )
                .order_by(PaperTrade.exited_at.desc())
                .first()
            )
            if _recent and _recent.exited_at:
                _exited = _safe_utc(_recent.exited_at)
                if _safe_utc(now) < _exited + timedelta(minutes=_reentry_cooldown):
                    action_summary["action"] = "skipped"
                    action_summary["reason"] = "reentry_cooldown"
                    print(f"[paper] {underlying} {signal_type}: skipped — reentry cooldown active ({_reentry_cooldown}min since last exit)")
                    actions.append(action_summary)
                    continue

        # ── Stop-loss / Take-profit check on existing position ──
        # These checks are INDEPENDENT of signal direction — a losing position
        # should be cut (or winner taken) regardless of what the sentiment model
        # currently says.  Previously the guard `signal_type == open_pos.signal_type`
        # meant that when sentiment flipped (e.g. LONG→SHORT) the stop-loss was
        # never re-checked, allowing losses to compound.  Running this BEFORE
        # flip handling means a held-through-flip position is still protected by
        # its stop-loss / take-profit.
        if open_pos is not None and existing_pos_price > 0:
            # Use adaptive stop loss if enabled, otherwise use fixed
            _adaptive_cfg = _L.get("adaptive_stops", {})
            if _adaptive_cfg.get("enabled", False):
                _stop_loss = _calculate_adaptive_stop_loss(open_pos, quotes_by_symbol, _L)
            else:
                _stop_loss = _stop_loss_pct_for_config(_app_config)
            _take_profit = _take_profit_pct_for_config(_app_config)
            if _stop_loss > 0 or _take_profit > 0:
                _pnl_pct = _directional_return_pct(open_pos.signal_type, float(open_pos.entry_price or 0), existing_pos_price)
                if _stop_loss > 0 and _pnl_pct <= -_stop_loss:
                    _close_position(open_pos, existing_pos_price, now, db, reason="stop_loss_hit")
                    action_summary["closed_pnl"] = open_pos.realized_pnl
                    action_summary["exit_price"] = existing_pos_price
                    action_summary["pnl_pct"] = round(_pnl_pct, 4)
                    action_summary["action"] = "closed"
                    action_summary["reason"] = "stop_loss_hit"
                    _alpaca_pending.append((open_pos, "close"))
                    actions.append(action_summary)
                    continue
                if _take_profit > 0 and _pnl_pct >= _take_profit:
                    _close_position(open_pos, existing_pos_price, now, db, reason="take_profit_hit")
                    action_summary["closed_pnl"] = open_pos.realized_pnl
                    action_summary["exit_price"] = existing_pos_price
                    action_summary["pnl_pct"] = round(_pnl_pct, 4)
                    action_summary["action"] = "closed"
                    action_summary["reason"] = "take_profit_hit"
                    _alpaca_pending.append((open_pos, "close"))
                    actions.append(action_summary)
                    continue

        # ── Direction flip / ticker-leverage change ──
        # Flip management (Choice B): a LOW/MEDIUM conviction flip HOLDS the
        # position ("stay the course") instead of churning out at sub-dollar
        # P&L.  Only a HIGH conviction flip (or a strong-score MEDIUM flip)
        # closes early and opens the opposite side.  Same-direction
        # ticker/leverage changes stay gated by the same-day exit edge.
        # Stop-loss / take-profit above always take precedence.
        is_direction_flip = open_pos is not None and open_pos.signal_type != signal_type
        _flip_early_close = is_direction_flip and _flip_should_early_close(conviction_level, rec)
        window_blocks_close = (
            open_pos is not None
            and is_direction_flip
            and not _flip_early_close
            and not _cv.get("flip_overrides_window", True)
            and _window_active(open_pos, now)
        )
        if open_pos and existing_pos_price > 0 and not window_blocks_close:
            if is_direction_flip and not _flip_early_close:
                # Hold through a LOW/MEDIUM flip — stay the course. Refresh the
                # holding window from the new thesis (capped) and reset the
                # trailing baseline; do NOT open the opposite position.
                _fh_res = _apply_flip_hold(open_pos, conviction_level, trading_type, holding_minutes, now, _L)
                action_summary["action"] = "held"
                action_summary["reason"] = _fh_res["reason"]
                action_summary["holding_window_until"] = _utc_iso(_fh_res["holding_window_until"])
                action_summary["flip_held_as"] = _fh_res["held_as"]
                print(
                    f"[paper] {underlying}: held through {_prev_signal_type}→{signal_type} flip "
                    f"({conviction_level}) — refreshed window to {_utc_iso(_fh_res['holding_window_until'])}"
                )
                actions.append(action_summary)
                continue

            # Direction flips close — thesis has fundamentally changed.
            # Same-day exit edge only applies to ticker/leverage changes within the same direction.
            if not is_direction_flip and _same_day_exit_edge_blocks_close(open_pos, existing_pos_price, now, _min_same_day_edge_pct):
                action_summary["action"] = "held"
                action_summary["reason"] = "min_same_day_exit_edge"
                print(f"[paper] {underlying}: held — min same-day exit edge not reached (need {_min_same_day_edge_pct:.1f}%)")
                action_summary["exit_edge_pct"] = round(
                    _directional_return_pct(open_pos.signal_type, open_pos.entry_price, existing_pos_price),
                    4,
                )
                action_summary["min_same_day_exit_edge_pct"] = _min_same_day_edge_pct
                actions.append(action_summary)
                continue
            _close_position(
                open_pos, existing_pos_price, now, db,
                reason="direction_flip" if is_direction_flip else "ticker_leverage_change",
            )
            action_summary["closed_pnl"] = open_pos.realized_pnl
            action_summary["exit_price"] = existing_pos_price
            action_summary["flip_early_closed"] = bool(is_direction_flip)
            _alpaca_pending.append((open_pos, "close"))
            if _portfolio_cap is not None:
                _open_exposure = max(0.0, _open_exposure - float(open_pos.amount or 0))
        elif window_blocks_close:
            action_summary["action"] = "held"
            action_summary["reason"] = "conviction_window_blocks_flip"
            action_summary["holding_window_until"] = _utc_iso(open_pos.holding_window_until)
            print(f"[paper] {underlying} {signal_type}: held — conviction window blocks direction flip until {_utc_iso(open_pos.holding_window_until)}")
            actions.append(action_summary)
            continue

        # ── Opening range guard: no NEW exposure before the range forms /
        # against the opening break. Runs after stop-loss / take-profit /
        # flip-close above so lifecycle exits keep working during the wait.
        _or_block = _opening_range_block(
            underlying, execution_ticker, entry_price, signal_type, conviction_level, now
        )
        if _or_block:
            _or_reason, _or_info = _or_block
            action_summary["action"] = "skipped"
            action_summary["reason"] = _or_reason
            action_summary["opening_range"] = _or_info
            print(
                f"[paper] {underlying} {signal_type}: skipped — {_or_reason} "
                f"({_or_info})"
            )
            actions.append(action_summary)
            continue

        # ── Entry threshold gate ──
        # (directional signals only — HOLD signals skip this)
        # We gate on conviction_level: only HIGH conviction gets an automatic pass.
        # MEDIUM requires the configured entry threshold; LOW is always blocked.
        # EXCEPTION: crazy profile allows LOW conviction entries.
        # LOW conviction trades are allowed at vol-scaled size (0.5× conviction scalar).
        # The vol-sizing formula in _compute_vol_normalized_amount handles the size reduction.
        # Open new position — size using volatility targeting, then apply portfolio cap
        if getattr(_app_config, "alpaca_fixed_order_size", False):
            _amount = _base_amount
        else:
            _amount = _compute_vol_normalized_amount(_base_amount, conviction_level, _atr_pct)

        # Apply continuous entry size_pct scaling (sigmoid allocation)
        _size_pct = float(rec.get("size_pct", "100.0") or "100.0") / 100.0
        _amount *= _size_pct

        # ── Minimum initial entry floor ─────────────────────────────
        # The initial entry must be at least 25% of the base amount to
        # avoid wasting fees on sub-penny dust positions.  Ramp stage
        # controls accumulation (how much you add on), NOT the first
        # entry.  Only apply ramp_cap when we are adding to an existing
        # position (handled in the accumulation block below).
        _MIN_INITIAL_ENTRY_PCT = 0.25
        _min_initial = max(_base_amount * _MIN_INITIAL_ENTRY_PCT, _min_trade_size_usd())
        _amount = max(_amount, _min_initial)

        # ── Ramp stage tracking (stored for accumulation logic) ──────
        _ramp_stage_entry = str(rec.get("ramp_stage") or "full")

        if _portfolio_cap is not None:
            _remaining = max(0.0, _portfolio_cap - _open_exposure)
            if _remaining <= 0.0:
                action_summary["action"] = "skipped"
                action_summary["reason"] = "portfolio_cap_reached"
                action_summary["portfolio_cap_usd"] = _portfolio_cap
                action_summary["open_exposure_usd"] = round(_open_exposure, 2)
                print(f"[paper] {underlying} {signal_type}: skipped — portfolio cap reached (${_open_exposure:.2f} / ${_portfolio_cap:.2f})")
                actions.append(action_summary)
                continue
            _amount = min(_amount, _remaining)

            # A trade that can't reach the minimum size after the portfolio
            # cap isn't worth the fees/churn — skip it.
            _min_size = _min_trade_size_usd()
            if _min_size > 0 and _amount < _min_size:
                action_summary["action"] = "skipped"
                action_summary["reason"] = "min_trade_size"
                action_summary["min_trade_size_usd"] = round(_min_size, 2)
                print(f"[paper] {underlying} {signal_type}: skipped — ${_amount:.2f} below min trade size ${_min_size:.2f}")
                actions.append(action_summary)
                continue

        if entry_price > 0:
            window_until = datetime.now(timezone.utc) + timedelta(minutes=holding_minutes)
            shares = round(_amount / entry_price, 6)
            new_trade = PaperTrade(
                underlying=underlying,
                execution_ticker=execution_ticker,
                signal_type=signal_type,
                leverage=leverage,
                market_session=session["status"],
                amount=_amount,
                shares=shares,
                entry_price=entry_price,
                entered_at=now,
                analysis_request_id=request_id,
                conviction_level=conviction_level,
                trading_type=trading_type,
                holding_period_hours=round(holding_minutes / 60, 2),
                holding_window_until=window_until,
                original_amount=_amount,
                ramp_stage=_ramp_stage_entry,
                ramp_promotion_count=0,
            )
            db.add(new_trade)
            db.flush()  # get new_trade.id

            # ── Decision Log: trade entry ───────────────────────────────
            try:
                from database.engine import DecisionLogSessionLocal
                from services.decision_logger import logger as _dl
                _ddb = DecisionLogSessionLocal()
                try:
                    _trade_log_id = _dl.log_trade_entry(
                        _ddb,
                        paper_trade_id=new_trade.id,
                        symbol=underlying,
                        direction=signal_type,
                        entry_timestamp=now,
                        entry_price=entry_price,
                        entry_directional_score=rec.get("directional_score"),
                        entry_confidence=rec.get("confidence"),
                        entry_trade_size=_amount,
                        entry_size_reasoning=(
                            f"vol_sizing with ATR based on {conviction_level} conviction"
                        ),
                        entry_leverage=leverage,
                        entry_leverage_reasoning=(
                            f"risk_profile based, conviction {conviction_level}"
                        ),
                        holding_window_hours=round(holding_minutes / 60, 2),
                    )
                    _dl.log_trade_event(
                        _ddb,
                        trade_log_id=_trade_log_id,
                        event_type="open",
                        run_id=request_id,
                        directional_score=rec.get("directional_score"),
                        keep_vs_close="open",
                        decision_reason=f"Opened {signal_type} {execution_ticker} @ ${entry_price:.2f} (${_amount:.2f}, {conviction_level})",
                        event_details={
                            "entry_price": entry_price,
                            "amount": _amount,
                            "conviction": conviction_level,
                            "leverage": leverage,
                            "trading_type": trading_type,
                            "holding_window_until": _utc_iso(window_until),
                        },
                    )
                    _ddb.commit()
                except Exception as _dlx:
                    _ddb.rollback()
                    print(f"[decision-log] trade entry error: {_dlx}")
                finally:
                    _ddb.close()
            except Exception as _dlx:
                print(f"[decision-log] trade entry error (non-fatal): {_dlx}")
            _alpaca_pending.append((new_trade, "open"))
            if _portfolio_cap is not None:
                _open_exposure += _amount
            action_summary["action"] = "opened"
            action_summary["entry_price"] = entry_price
            action_summary["amount"] = round(_amount, 2)
            action_summary["holding_window_until"] = _utc_iso(window_until)
            print(f"[paper] {underlying} {signal_type}: opened {execution_ticker} @ ${entry_price:.2f} (${_amount:.2f}, {conviction_level})")
        else:
            action_summary["action"] = "skipped"
            action_summary["reason"] = "no_price_available"
            print(f"[paper] {underlying} {signal_type}: skipped — no price available for {execution_ticker}")

        actions.append(action_summary)

    # Close any open position whose underlying was not covered by this run.
    # Absence of a recommendation = thesis gone; treat the same as a HOLD with no window.
    covered_underlyings = {
        str(rec.get("underlying") or rec.get("symbol") or "").upper()
        for rec in recommendations
        if str(rec.get("underlying") or rec.get("symbol") or "").strip()
    }
    orphaned = (
        db.query(PaperTrade)
        .filter(PaperTrade.exited_at.is_(None))
        .all()
    )
    for pos in orphaned:
        if pos.underlying in covered_underlyings:
            continue
        price_data = quotes_by_symbol.get(pos.execution_ticker) or quotes_by_symbol.get(pos.underlying) or {}
        exit_price = float(price_data.get("current_price") or price_data.get("price") or pos.entry_price or 0.0)
        if exit_price > 0:
            if _same_day_exit_edge_blocks_close(pos, exit_price, now, _min_same_day_edge_pct):
                actions.append({
                    "underlying": pos.underlying,
                    "execution_ticker": pos.execution_ticker,
                    "signal_type": pos.signal_type,
                    "action": "held",
                    "reason": "min_same_day_exit_edge",
                    "exit_edge_pct": round(_directional_return_pct(pos.signal_type, pos.entry_price, exit_price), 4),
                    "min_same_day_exit_edge_pct": _min_same_day_edge_pct,
                    "session": session["label"],
                })
                continue
            _close_position(pos, exit_price, now, db, reason="no_recommendation")
            _alpaca_pending.append((pos, "close"))
            actions.append({
                "underlying": pos.underlying,
                "execution_ticker": pos.execution_ticker,
                "signal_type": pos.signal_type,
                "action": "closed",
                "reason": "no_recommendation",
                "exit_price": exit_price,
                "closed_pnl": pos.realized_pnl,
                "session": session["label"],
            })

    db.commit()
    _dispatch_alpaca_orders(db, _alpaca_pending, _app_config)
    return actions


def close_positions_for_removed_symbols(db, removed_symbols: List[str]) -> List[Dict[str, Any]]:
    """Close open paper trades for symbols removed from custom tracking."""
    from database.models import PaperTrade
    from services.data_ingestion.yfinance_client import PriceClient

    normalized_symbols = sorted({str(symbol or "").upper().strip() for symbol in removed_symbols if str(symbol or "").strip()})
    if not normalized_symbols:
        return []

    open_positions = (
        db.query(PaperTrade)
        .filter(PaperTrade.underlying.in_(normalized_symbols), PaperTrade.exited_at.is_(None))
        .all()
    )
    if not open_positions:
        return []

    now = datetime.now(timezone.utc)
    price_client = PriceClient()
    closed_positions: List[Dict[str, Any]] = []

    for pos in open_positions:
        exit_price = 0.0
        try:
            quote = price_client.get_realtime_quote(pos.execution_ticker)
            exit_price = float((quote or {}).get("current_price") or 0.0)
        except Exception:
            exit_price = 0.0

        if exit_price <= 0:
            exit_price = float(pos.entry_price or 0.0)
        if exit_price <= 0:
            continue

        _close_position(pos, exit_price, now, db, reason="symbol_removed_from_config")
        closed_positions.append({
            "underlying": pos.underlying,
            "execution_ticker": pos.execution_ticker,
            "signal_type": pos.signal_type,
            "exit_price": exit_price,
            "realized_pnl": pos.realized_pnl,
            "reason": "symbol_removed_from_config",
        })

    if closed_positions:
        db.commit()

    return closed_positions


def _dispatch_alpaca_orders(db, pending: list, config) -> None:
    """Fire-and-forget Alpaca order dispatch after paper trades are committed.

    Closes are dispatched FIRST, then opens. This ensures that when a direction
    flip occurs, the old position is fully closed before the new one is opened,
    preventing simultaneous opposing positions in the Alpaca account.

    DEDUPLICATION: When multiple analysis runs fire simultaneously (e.g., cron
    overlap), they each create independent PaperTrade records that produce
    identical orders. This function deduplicates by (symbol, event) within the
    dispatch cycle, keeping only the first occurrence per unique key.

    The deduplication key is (execution_ticker, event) — not trade_id — because
    duplicate analysis runs produce independent PaperTrade records with the same
    symbol and event. We keep the first (newest) open and skip later duplicates.

    MANUAL+AUTO COOLDOWN: After ANY order is dispatched for a symbol, skip
    subsequent orders for the same symbol within _ORDER_COOLDOWN_MINUTES
    (default: 30). This prevents the auto system from firing a duplicate order
    shortly after a manual order (or vice versa).
    """
    if not pending or config is None:
        return
    try:
        from services.alpaca_broker import maybe_execute_alpaca_order, retry_window_skipped_orders
        retry_window_skipped_orders(db, config)
    except Exception as _retry_exc:
        print(f"[alpaca] retry_window_skipped_orders failed (non-fatal): {_retry_exc}")
    try:
        from services.alpaca_broker import maybe_execute_alpaca_order

        # ── DEDUPLICATION PASS ──────────────────────────────────────────
        # Group by (symbol, event) and keep only the first occurrence.
        # This prevents duplicate orders when multiple analysis runs fire
        # in the same 30-minute cadence window (or overlap due to slow execution).
        _ORDER_COOLDOWN_MINUTES = 30
        _now_utc_dispatch = _safe_utc(datetime.now(timezone.utc))

        seen_keys: Dict[str, tuple] = {}
        unique_closes: list = []
        unique_opens: list = []

        # ── MANUAL+AUTO COOLDOWN PASS ───────────────────────────────────
        # Before dedup, filter out orders that fall within the any-order
        # cooldown window for the same symbol. This prevents the auto system
        # from firing a duplicate order shortly after a manual order (or
        # vice versa).  This is NOT signal-type-specific — if the user
        # manually bought SPXL 20 minutes ago, the auto system won't buy
        # SPXL again regardless of signal type.
        cooldown_filtered: list = []
        for trade, event in pending:
            symbol = str(
                getattr(trade, "execution_ticker", "") or
                getattr(trade, "underlying", "")
            ).upper()
            _prev_order = _last_order_times.get(symbol)
            if _prev_order is not None and _now_utc_dispatch < _prev_order + timedelta(minutes=_ORDER_COOLDOWN_MINUTES):
                print(
                    f"[alpaca] COOLDOWN: skipping {event} for {symbol} "
                    f"(order dispatched {_safe_utc(_prev_order).strftime('%H:%M')} ago, "
                    f"cooldown={_ORDER_COOLDOWN_MINUTES}min)"
                )
                continue
            cooldown_filtered.append((trade, event))

        # Then deduplicate within the cooldown-filtered list

        for trade, event in cooldown_filtered:
            symbol = str(
                getattr(trade, "execution_ticker", "") or
                getattr(trade, "underlying", "")
            ).upper()
            key = f"{symbol}::{event}"

            if key in seen_keys:
                # Duplicate: skip this trade but preserve it for logging
                print(
                    f"[alpaca] DEDUP: skipping duplicate {event} for {symbol} "
                    f"(trade_id={getattr(trade, 'id', '?')}, "
                    f"entered={getattr(trade, 'entered_at', '?')})"
                )
                continue

            seen_keys[key] = (trade, event)
            if event == "close":
                unique_closes.append((trade, event))
            else:
                unique_opens.append((trade, event))

        # Compute counts before dispatch summary
        _cooldown_skipped = len(pending) - len(cooldown_filtered)
        _dup_count = len(cooldown_filtered) - len(seen_keys)

        # ── DISPATCH: closes first, then opens ──────────────────────────
        _dispatch_summary = {"total": len(pending), "unique": len(seen_keys), "closes": len(unique_closes), "opens": len(unique_opens)}
        print(f"[alpaca] dispatch: {_dispatch_summary['total']} total → {_dispatch_summary['unique']} unique ({_dup_count} dupes, {_cooldown_skipped} cooldown)")

        # ── Phase 1: Dispatch all closes ────────────────────────────────
        for trade, event in unique_closes:
            _sym = str(getattr(trade, "execution_ticker", "") or getattr(trade, "underlying", "?")).upper()
            print(f"[alpaca] dispatching close: {_sym} (paper_id={getattr(trade, 'id', '?')})")
            try:
                maybe_execute_alpaca_order(db, trade, event, config)
            except Exception as _exc:
                print(f"[alpaca] dispatch ERROR close {_sym}: {_exc}")

        # ── Phase 2: Pre-open live position check + retry on conflict ──
        # Before dispatching any opens, check Alpaca's live positions for
        # conflicts with the positions we're about to open.  If a conflicting
        # position exists (e.g. TQQQ still open when we want to open SQQQ),
        # retry the close up to 3 times with delays before giving up.
        # This prevents simultaneous opposing positions in the live account.
        _OPEN_RETRY_MAX = 3
        _OPEN_RETRY_DELAY = 5.0  # seconds

        try:
            from services.alpaca_broker import (
                get_broker_from_keychain,
                _has_conflicting_live_position,
                _dispatch_close_with_retry,
            )
            _alpaca_mode = str(getattr(config, "alpaca_execution_mode", "off") or "off")
            _live_broker = get_broker_from_keychain(mode=_alpaca_mode) if _alpaca_mode != "off" else None
        except Exception:
            _live_broker = None

        # ── Cancel pending conflicting orders ─────────────────────────────
        # Before dispatching opens, check for pending orders from previous runs
        # that conflict with the current run's intentions.  For example,
        # if a previous run submitted a MARKET order for SPXS (SHORT SPY)
        # that hasn't filled yet, and the current run wants to buy SPXL
        # (LONG SPY), we must CANCEL the pending SPXS order before
        # submitting the SPXL order to prevent both from filling.
        if _live_broker and unique_opens:
            try:
                _pending_orders = _live_broker.get_pending_orders()
                if _pending_orders:
                    # Build a map of what the current run wants
                    _intended_positions: Dict[str, str] = {}  # underlying -> signal_type
                    for trade, event in unique_opens:
                        _sym = str(getattr(trade, "underlying", "") or "").upper()
                        _sig = str(getattr(trade, "signal_type", "") or "").upper()
                        if _sym:
                            _intended_positions[_sym] = _sig
                    
                    # Check each pending order for conflict
                    from services.trading_instruments import build_ticker_bucket_map
                    _bucket_map = build_ticker_bucket_map()
                    
                    for _order in _pending_orders:
                        _order_symbol = str(_order.get("symbol", "") or "").upper()
                        _order_side = str(_order.get("side", "") or "").lower()
                        _order_id = str(_order.get("id", "") or "")
                        
                        # Determine the underlying for this pending order
                        _order_info = _bucket_map.get(_order_symbol)
                        if _order_info:
                            _order_underlying = _order_info[0]
                            _order_bucket = _order_info[1]  # "bull" or "bear"
                        else:
                            _order_underlying = _order_symbol
                            _order_bucket = "bull" if _order_side == "buy" else "bear"
                        
                        # Check if the current run intends to open a position for this underlying
                        _intended_signal = _intended_positions.get(_order_underlying)
                        if not _intended_signal:
                            continue  # No conflict, current run doesn't touch this underlying
                        
                        # Determine if there's a conflict
                        _order_is_long = (_order_side == "buy" and _order_bucket == "bull") or \
                                         (_order_side == "sell" and _order_bucket == "bear")
                        _intended_is_long = (_intended_signal == "LONG")
                        
                        if _order_is_long != _intended_is_long:
                            # CONFLICT! Cancel the pending order
                            try:
                                _live_broker.cancel_order(_order_id)
                                print(
                                    f"[alpaca] CANCELLED pending conflicting order: "
                                    f"{_order_symbol} (underlying={_order_underlying}, "
                                    f"side={_order_side}, order_id={_order_id}) "
                                    f"— current run wants {_intended_signal}"
                                )
                            except Exception as _cancel_err:
                                print(f"[alpaca] FAILED to cancel pending order {_order_id}: {_cancel_err}")
            except Exception as _pending_err:
                print(f"[alpaca] pending order check failed (non-fatal): {_pending_err}")

        _skipped_opens = 0
        for trade, event in unique_opens:
            _sym = str(getattr(trade, "execution_ticker", "") or getattr(trade, "underlying", "?")).upper()
            _signal_type = str(getattr(trade, "signal_type", "") or "").upper()
            _underlying = str(getattr(trade, "underlying", "") or "").upper()

            # Check for conflicting live position
            _conflict = None
            if _live_broker:
                _conflict = _has_conflicting_live_position(_live_broker, _sym, _signal_type)

            if _conflict:
                _conflict_sym = _conflict.get("symbol", "unknown")
                _conflict_qty = _conflict.get("qty", 0)
                
                # ── Dust position handling ────────────────────────────────
                # If the conflicting position has dust-level quantity (essentially zero),
                # treat it as already closed and skip the close attempt.
                _DUST_QTY_THRESHOLD = 0.001  # shares below this are considered dust
                _conflict_qty_float = float(_conflict_qty) if _conflict_qty != "unknown" else 0.0
                
                if _conflict_qty_float < _DUST_QTY_THRESHOLD:
                    print(
                        f"[alpaca] DUST CONFLICT: {_sym} ({_signal_type}) has dust-level "
                        f"conflict with {_conflict_sym} (qty={_conflict_qty}) — "
                        f"treating as already closed, proceeding with open"
                    )
                    _conflict = None  # Clear the conflict, proceed with open
                else:
                    print(
                        f"[alpaca] CONFLICT: {_sym} ({_signal_type}) conflicts with live "
                        f"position {_conflict_sym} (qty={_conflict_qty}) — "
                        f"retrying close before open (max {_OPEN_RETRY_MAX} attempts)"
                    )

                if _conflict:
                    # Find the paper trade to close (the one with the conflicting ticker)
                    _close_trade = None
                    for ct, ce in unique_closes:
                        ct_sym = str(getattr(ct, "execution_ticker", "") or "").upper()
                        if ct_sym == _conflict_sym:
                            _close_trade = ct
                            break

                    if _close_trade is None:
                        # No close in this dispatch cycle — try to find it in pending
                        for pt, pe in pending:
                            pt_sym = str(getattr(pt, "execution_ticker", "") or "").upper()
                            if pt_sym == _conflict_sym and pe == "close":
                                _close_trade = pt
                                break

                    if _close_trade is None:
                        print(
                            f"[alpaca] SKIP open {_sym}: conflict with {_conflict_sym} "
                            f"but no close order found to retry"
                        )
                        _skipped_opens += 1
                        continue

                    # Retry the close with exponential backoff
                    _close_success = False
                    for attempt in range(1, _OPEN_RETRY_MAX + 1):
                        try:
                            _dispatch_close_with_retry(
                                db, _close_trade, _live_broker, config,
                                max_retries=1, retry_delay_seconds=0,
                            )
                            # Verify the conflict is resolved
                            _conflict = _has_conflicting_live_position(_live_broker, _sym, _signal_type)
                            if not _conflict:
                                _close_success = True
                                print(f"[alpaca] close retry {attempt} succeeded for {_conflict_sym}")
                                break
                            else:
                                print(
                                    f"[alpaca] close retry {attempt}/{_OPEN_RETRY_MAX} for "
                                    f"{_conflict_sym} — conflict still present, retrying in {_OPEN_RETRY_DELAY}s"
                                )
                        except Exception as _retry_exc:
                            print(f"[alpaca] close retry {attempt}/{_OPEN_RETRY_MAX} for {_conflict_sym} failed: {_retry_exc}")

                        if attempt < _OPEN_RETRY_MAX:
                            print(f"[alpaca] waiting {_OPEN_RETRY_DELAY}s before next retry...")
                            import time as _time
                            _time.sleep(_OPEN_RETRY_DELAY)

                    if not _close_success:
                        print(
                            f"[alpaca] SKIP open {_sym}: all {_OPEN_RETRY_MAX} close retries failed "
                            f"for {_conflict_sym} — dispatch error recorded"
                        )
                        _skipped_opens += 1
                        continue
                # else: dust conflict already handled, proceed to open

            # No conflict (or conflict resolved) — dispatch the open
            print(f"[alpaca] dispatching open: {_sym} (paper_id={getattr(trade, 'id', '?')})")
            try:
                maybe_execute_alpaca_order(db, trade, event, config)
            except Exception as _exc:
                print(f"[alpaca] dispatch ERROR open {_sym}: {_exc}")

        if _skipped_opens > 0:
            print(f"[alpaca] skipped {_skipped_opens} open(s) due to unresolved conflicts")

        print(f"[alpaca] dispatch complete: {_dispatch_summary['closes']} closes, {_dispatch_summary['opens'] - _skipped_opens} opens attempted ({_skipped_opens} skipped)")

        # ── RECORD LAST ORDER TIMES ─────────────────────────────────────
        for trade, event in seen_keys.values():
            symbol = str(
                getattr(trade, "execution_ticker", "") or
                getattr(trade, "underlying", "")
            ).upper()
            _last_order_times[symbol] = _now_utc_dispatch

        if len(cooldown_filtered) != len(seen_keys):
            print(f"[alpaca] dedup summary: {len(pending)} total → {len(cooldown_filtered)} after cooldown → {len(seen_keys)} unique ({_dup_count} duplicates skipped, {_cooldown_skipped} cooldown skipped)")

    except ImportError:
        pass
    except Exception as exc:
        print(f"[alpaca] order dispatch error: {exc}")


def _close_position(pos, exit_price: float, now: datetime, db, reason: Optional[str] = None) -> None:
    pnl_pct = _directional_return_pct(pos.signal_type, pos.entry_price, exit_price)
    pos.exit_price = exit_price
    pos.exited_at = now
    pos.realized_pnl = round(_directional_pnl(pos.signal_type, pos.entry_price, exit_price, pos.amount), 4)
    pos.realized_pnl_pct = round(pnl_pct, 4)
    if reason:
        pos.close_reason = reason

    # ── Decision Log: trade close ───────────────────────────────────────
    try:
        from database.engine import DecisionLogSessionLocal
        from database.models import DecisionLogTrade
        from services.decision_logger import logger as _dl
        _ddb = DecisionLogSessionLocal()
        try:
            _trade_log = _ddb.query(DecisionLogTrade).filter(
                DecisionLogTrade.paper_trade_id == pos.id
            ).first()
            if _trade_log:
                _dl.log_trade_close(
                    _ddb,
                    trade_log_id=_trade_log.id,
                    close_timestamp=now,
                    close_price=exit_price,
                    close_trigger=reason or "unknown",
                    realized_pnl=pos.realized_pnl,
                )
                _dl.log_trade_event(
                    _ddb,
                    trade_log_id=_trade_log.id,
                    event_type="close",
                    run_id=None,
                    keep_vs_close="close",
                    decision_reason=f"Position closed: {reason or 'unknown'}. Exit price={exit_price}, P&L={pos.realized_pnl}",
                    event_details={
                        "exit_price": exit_price,
                        "realized_pnl": pos.realized_pnl,
                        "reason": reason,
                    },
                )
                _ddb.commit()
        except Exception as _dlx:
            _ddb.rollback()
            print(f"[decision-log] close error: {_dlx}")
        finally:
            _ddb.close()
    except Exception as _dlx:
        print(f"[decision-log] close error (non-fatal): {_dlx}")


def get_summary(db) -> Dict[str, Any]:
    """Build the full paper trading summary with live unrealized P&L."""
    from database.models import PaperTrade
    from services.data_ingestion.yfinance_client import PriceClient

    trades = db.query(PaperTrade).order_by(PaperTrade.entered_at.asc()).all()
    price_client = PriceClient()

    closed = [t for t in trades if t.exited_at is not None]
    open_positions_raw = [t for t in trades if t.exited_at is None]

    closed_metrics = []
    for t in closed:
        pnl = _directional_pnl(t.signal_type, t.entry_price, float(t.exit_price or t.entry_price), t.amount)
        pnl_pct = _directional_return_pct(t.signal_type, t.entry_price, float(t.exit_price or t.entry_price))
        closed_metrics.append({
            "trade": t,
            "realized_pnl": round(pnl, 4),
            "realized_pnl_pct": round(pnl_pct, 4),
        })

    realized_pnl = sum(item["realized_pnl"] for item in closed_metrics)
    wins = [item for item in closed_metrics if item["realized_pnl"] > 0]
    losses = [item for item in closed_metrics if item["realized_pnl"] <= 0]

    open_pnl = 0.0
    open_positions = []
    for t in open_positions_raw:
        try:
            q = price_client.get_realtime_quote(t.execution_ticker)
            current = float(q.get("current_price") or t.entry_price) if q else t.entry_price
        except Exception:
            current = t.entry_price
        unrealized = round(_directional_pnl(t.signal_type, t.entry_price, current, t.amount), 4)
        unrealized_pct = round(_directional_return_pct(t.signal_type, t.entry_price, current), 4)
        open_pnl += unrealized
        now_utc = _safe_utc(datetime.now(timezone.utc))
        win = _safe_utc(t.holding_window_until)
        window_active = bool(win and now_utc < win)
        window_remaining_minutes = (
            round((win - now_utc).total_seconds() / 60) if window_active else None
        )
        open_positions.append({
            "id": t.id,
            "underlying": t.underlying,
            "execution_ticker": t.execution_ticker,
            "signal_type": t.signal_type,
            "leverage": t.leverage,
            "amount": t.amount,
            "shares": t.shares,
            "entry_price": t.entry_price,
            "current_price": current,
            "entered_at": _utc_iso(t.entered_at),
            "market_session": t.market_session,
            "unrealized_pnl": unrealized,
            "unrealized_pnl_pct": unrealized_pct,
            "conviction_level": t.conviction_level,
            "trading_type": t.trading_type,
            "holding_period_hours": t.holding_period_hours,
            "holding_window_until": _utc_iso(t.holding_window_until),
            "window_active": window_active,
            "window_remaining_minutes": window_remaining_minutes,
            "trailing_stop_price": t.trailing_stop_price,
            "best_price_seen": t.best_price_seen,
        })

    total_deployed = sum(float(t.amount or _L["paper_trade_amount"]) for t in trades)
    total_pnl = realized_pnl + open_pnl
    configured_trade_amount = float(_L["paper_trade_amount"])
    try:
        from services.app_config import get_or_create_app_config
        config = get_or_create_app_config(db)
        configured_trade_amount = float(getattr(config, "paper_trade_amount", None) or configured_trade_amount)
    except Exception:
        pass

    # Equity curve: cumulative realized P&L per closed trade
    equity_curve = []
    running = 0.0
    for item in closed_metrics:
        t = item["trade"]
        running += item["realized_pnl"]
        equity_curve.append({
            "at": _utc_iso(t.exited_at),
            "cumulative_pnl": round(running, 4),
            "trade_pnl": item["realized_pnl"],
            "trade_pnl_pct": item["realized_pnl_pct"],
            "ticker": t.execution_ticker,
            "underlying": t.underlying,
        })

    # Filter closed trades to last 4 days for the main display
    now_utc = _safe_utc(datetime.now(timezone.utc))
    cutoff_date = now_utc - timedelta(days=4)

    closed_trades = []
    for item in reversed(closed_metrics):
        t = item["trade"]
        exited = _safe_utc(t.exited_at)
        if exited is not None and exited < cutoff_date:
            continue
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
            "realized_pnl": item["realized_pnl"],
            "realized_pnl_pct": item["realized_pnl_pct"],
            "market_session": t.market_session,
            "conviction_level": t.conviction_level,
            "trading_type": t.trading_type,
            "holding_period_hours": t.holding_period_hours,
            "close_reason": t.close_reason,
        })

    return {
        "market": market_status(_allow_extended_hours_trading(db)),
        "paper_trade_amount": round(configured_trade_amount, 2),
        "summary": {
            "total_trades": len(trades),
            "open_positions": len(open_positions),
            "closed_trades": len(closed),
            "total_deployed": total_deployed,
            "realized_pnl": round(realized_pnl, 4),
            "open_pnl": round(open_pnl, 4),
            "total_pnl": round(total_pnl, 4),
            "total_pnl_pct": round((total_pnl / max(total_deployed, 1)) * 100, 2) if total_deployed else 0,
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(len(wins) / max(len(closed), 1) * 100, 1) if closed else 0,
            "avg_win": round(sum(item["realized_pnl"] for item in wins) / max(len(wins), 1), 4) if wins else 0,
            "avg_loss": round(sum(item["realized_pnl"] for item in losses) / max(len(losses), 1), 4) if losses else 0,
        },
        "open_positions": open_positions,
        "closed_trades": closed_trades,
        "equity_curve": equity_curve,
    }


def _utc_iso(dt: Optional[datetime]) -> Optional[str]:
    normalized = _safe_utc(dt)
    return normalized.isoformat() if normalized is not None else None