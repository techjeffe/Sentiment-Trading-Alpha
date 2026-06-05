"""
Alpaca brokerage integration.
Routes real orders to Alpaca paper-api or live api based on trading_mode stored
in the OS keychain. All order attempts (success and failure) are written to the
alpaca_orders table so there is always a complete audit trail.
"""
from __future__ import annotations

import math as _math
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

import httpx
from zoneinfo import ZoneInfo

PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE  = "https://api.alpaca.markets"
_ET = ZoneInfo("America/New_York")

# ── Inverse ETF mapping ────────────────────────────────────────────────────────
# Maps stock/sector tickers to their corresponding inverse ETFs.
# When a SHORT signal is generated for a stock we can't short, we instead buy
# the inverse ETF to express the bearish view.
INVERSE_ETF_MAP: Dict[str, str] = {
    # Tech / semiconductors
    "NVDA": "PSQ",
    "AMD": "PSQ",
    "SMCI": "PSQ",
    "AVGO": "PSQ",
    "INTC": "PSQ",
    # Broad tech sector
    "AAPL": "PSQ",
    "MSFT": "PSQ",
    "GOOGL": "PSQ",
    "META": "PSQ",
    "TSLA": "SQQQ",  # High beta, map to Nasdaq inverse
    # Crypto-related
    "COIN": "BITI",
    "MSTR": "BITI",
    # Market-beta hedges
    "SPY": "SPXS",   # 3x daily short S&P 500
    "QQQ": "SPXS",   # 3x daily short Nasdaq-100 (also covered by SPXS)
    "DIA": "SPXS",   # 3x daily short Dow
    # Sector-specific inverses
    "XLF": "SPXS",   # Financials -> broad market hedge
    "XLE": "RWM",    # Energy -> Russell 2000 inverse
}

_TERMINAL_STATUSES = frozenset({"filled", "cancelled", "expired", "rejected", "error"})


class CircuitBreakerError(Exception):
    """Raised when a safety limit would be breached; live trading is auto-disabled."""


# ── Broker client ─────────────────────────────────────────────────────────────

class AlpacaBroker:
    def __init__(self, api_key: str, secret_key: str, mode: str = "paper") -> None:
        self.mode    = mode  # "paper" | "live"
        self._base   = PAPER_BASE if mode == "paper" else LIVE_BASE
        self._headers: Dict[str, str] = {
            "APCA-API-KEY-ID":     api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Content-Type":        "application/json",
        }

    def get_account(self) -> Dict[str, Any]:
        return self._get("/v2/account")

    def get_positions(self) -> List[Dict[str, Any]]:
        result = self._get("/v2/positions")
        return result if isinstance(result, list) else []

    def _qty_str(self, qty: float) -> str:
        """Serialize quantity to string, always flooring to 6 decimal places.

        Python's round() can round UP (e.g. round(2.09402494, 6) → 2.094025),
        which overshoots Alpaca's available quantity and causes a 403 rejection.
        Using math.floor guarantees we never request more shares than available.
        """
        return str(_math.floor(qty * 1_000_000) / 1_000_000)

    def place_order(
        self,
        symbol: str,
        side: str,
        notional: Optional[float] = None,
        qty: Optional[float] = None,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: Optional[float] = None,
        extended_hours: bool = False,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "symbol":        symbol.upper(),
            "side":          side,
            "type":          order_type,
            "time_in_force": time_in_force,
        }
        if extended_hours:
            # Alpaca requires limit orders + explicit qty for extended hours.
            # Notional/fractional is not supported outside regular hours.
            payload["extended_hours"] = True
            payload["type"] = "limit"
            payload["time_in_force"] = "day"
            if limit_price:
                payload["limit_price"] = str(round(limit_price, 2))
            if qty:
                payload["qty"] = self._qty_str(qty)
        else:
            if notional is not None:
                payload["notional"] = str(round(notional, 2))
            elif qty is not None:
                payload["qty"] = self._qty_str(qty)
        if limit_price is not None and order_type == "limit":
            payload["limit_price"] = str(round(limit_price, 2))
        if client_order_id:
            payload["client_order_id"] = client_order_id
        return self._post("/v2/orders", payload)

    def cancel_order(self, order_id: str) -> None:
        self._delete(f"/v2/orders/{order_id}")

    def cancel_all_orders(self) -> List[Dict[str, Any]]:
        """Cancel every open order. Returns list of cancellation responses."""
        result = self._delete("/v2/orders")
        return result if isinstance(result, list) else []

    def close_position(self, symbol: str) -> Dict[str, Any]:
        symbol = symbol.upper()
        r = httpx.delete(self._base + f"/v2/positions/{symbol}", headers=self._headers, timeout=10)
        # If no position is open, treat as success (nothing to close)
        if r.status_code == 404:
            return {"message": f"No open position for {symbol}", "already_closed": True}
        r.raise_for_status()
        return r.json()

    def get_position(self, symbol: str) -> Dict[str, Any]:
        return self._get(f"/v2/positions/{symbol.upper()}")

    def get_order(self, order_id: str) -> Dict[str, Any]:
        return self._get(f"/v2/orders/{order_id}")

    def get_order_by_client_id(self, client_order_id: str) -> Dict[str, Any]:
        # URL-encode the client_order_id to handle any special characters safely
        encoded_id = quote(client_order_id, safe="")
        return self._get(f"/v2/orders:by_client_order_id?client_order_id={encoded_id}")

    def modify_order(
        self,
        order_id: str,
        qty: Optional[float] = None,
        limit_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if qty is not None:
            payload["qty"] = self._qty_str(qty)
        if limit_price is not None:
            payload["limit_price"] = str(round(limit_price, 2))
        r = httpx.patch(self._base + f"/v2/orders/{order_id}", headers=self._headers, json=payload, timeout=10)
        r.raise_for_status()
        return r.json()

    def list_orders(self, status: str = "open", limit: int = 50) -> List[Dict[str, Any]]:
        result = self._get(f"/v2/orders?status={status}&limit={limit}")
        return result if isinstance(result, list) else []

    def get_account_configurations(self) -> Dict[str, Any]:
        return self._get("/v2/account/configurations")

    def patch_account_configurations(self, **kwargs: Any) -> Dict[str, Any]:
        r = httpx.patch(self._base + "/v2/account/configurations", headers=self._headers, json=kwargs, timeout=10)
        r.raise_for_status()
        return r.json()

    def get_portfolio_history(
        self,
        period: str = "1M",
        timeframe: str = "1D",
        extended_hours: bool = False,
    ) -> Dict[str, Any]:
        params = f"period={period}&timeframe={timeframe}&extended_hours={'true' if extended_hours else 'false'}"
        return self._get(f"/v2/account/portfolio/history?{params}")

    def get_account_activities(
        self,
        activity_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        path = "/v2/account/activities"
        if activity_type:
            path += f"/{activity_type}"
        path += f"?page_size={limit}"
        try:
            result = self._get(path)
            return result if isinstance(result, list) else []
        except Exception:
            # Some Alpaca environments may reject page_size; try without it
            alt_path = "/v2/account/activities"
            if activity_type:
                alt_path += f"/{activity_type}"
            try:
                result = self._get(alt_path)
                return result if isinstance(result, list) else []
            except Exception:
                return []

    def _get(self, path: str) -> Any:
        r = httpx.get(self._base + path, headers=self._headers, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: Dict) -> Any:
        r = httpx.post(self._base + path, headers=self._headers, json=body, timeout=10)
        if r.status_code >= 400:
            detail = f"{r.status_code} {r.text[:2000]}"
            raise httpx.HTTPStatusError(detail, request=r.request, response=r)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> Any:
        r = httpx.delete(self._base + path, headers=self._headers, timeout=10)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {}


# ── Keychain helpers ──────────────────────────────────────────────────────────

def get_broker_from_keychain(mode: Optional[str] = None) -> Optional[AlpacaBroker]:
    """
    Load credentials from OS keychain.
    mode='paper' | 'live' selects a specific credential set.
    If None, uses live credentials when configured, else paper.
    """
    try:
        from services.secret_store import get_alpaca_credentials, get_alpaca_credentials_for_mode
        creds = get_alpaca_credentials_for_mode(mode) if mode else get_alpaca_credentials()
        if not creds.get("api_key") or not creds.get("secret_key"):
            return None
        return AlpacaBroker(
            api_key=creds["api_key"],
            secret_key=creds["secret_key"],
            mode=creds.get("mode", "paper"),
        )
    except Exception:
        return None


def is_alpaca_configured() -> bool:
    try:
        from services.secret_store import get_alpaca_secret_status
        return bool(get_alpaca_secret_status().get("configured"))
    except Exception:
        return False


# ── Position helpers ──────────────────────────────────────────────────────────

def _is_direct_short(paper_trade) -> bool:
    """
    True when signal_type is SHORT but execution_ticker == underlying,
    meaning no inverse ETF was mapped and a real short-sell is required.
    """
    return (
        str(getattr(paper_trade, "signal_type", "")).upper() == "SHORT"
        and str(getattr(paper_trade, "execution_ticker", "")).upper()
        == str(getattr(paper_trade, "underlying", "")).upper()
    )


def _has_live_open_order(db, paper_trade_id) -> bool:
    """
    Return True if a non-error AlpacaOrder exists for this paper trade, meaning
    the open leg was actually submitted to Alpaca (regardless of fill status).
    Error-status rows indicate a skipped/failed open (e.g. short selling disabled,
    circuit breaker fired) — those should not trigger a close.
    """
    if paper_trade_id is None:
        return False
    from database.models import AlpacaOrder
    return (
        db.query(AlpacaOrder)
        .filter(
            AlpacaOrder.paper_trade_id == paper_trade_id,
            AlpacaOrder.status != "error",
        )
        .first()
    ) is not None


def _is_extended_hours_now(config=None) -> bool:
    """Return True during Alpaca-supported pre/post-market windows when enabled."""
    from datetime import time as time_cls
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return False
    t = now_et.time()
    allow_ext = True
    if config is not None:
        allow_ext = bool(getattr(config, "allow_extended_hours_trading", True))
    if not allow_ext:
        return False
    reg_open  = time_cls(9, 30)
    reg_close = time_cls(16, 0)
    ext_open  = time_cls(4, 0)
    ext_close = time_cls(20, 0)
    return ext_open <= t < reg_open or reg_close < t <= ext_close


def _is_regular_market_hours_now() -> bool:
    """Return True during regular Alpaca market hours (9:30 AM – 4:00 PM ET, weekdays)."""
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return False
    h, m = now_et.hour, now_et.minute
    return (h > 9 or (h == 9 and m >= 30)) and h < 16


# ── Circuit breakers ──────────────────────────────────────────────────────────

def _get_alpaca_live_open_exposure(broker: "AlpacaBroker") -> Optional[float]:
    """Return net open exposure for live positions.

    LONG and SHORT positions offset each other — the larger side drives risk.
    E.g., a $15k LONG and $10k SHORT have $15k net exposure (not $25k gross).
    Returns None on error.
    """
    try:
        positions = broker.get_positions()
        long_exposure = 0.0
        short_exposure = 0.0
        for p in positions:
            mv = float(p.get("market_value") or 0)
            side = str(p.get("side") or "").lower()
            if side == "long":
                long_exposure += mv
            elif side == "short":
                short_exposure += mv
        # Net exposure = max(long, short) — the larger side drives risk
        return max(long_exposure, short_exposure)
    except Exception as exc:
        print(f"[alpaca] could not fetch live positions for exposure check: {exc}")
        return None


def _get_alpaca_live_daily_pnl(broker: "AlpacaBroker") -> Optional[float]:
    """Return today's P&L from the live account (equity − last_equity). Returns None on error."""
    try:
        account = broker.get_account()
        equity = float(account.get("equity") or 0)
        last_equity = float(account.get("last_equity") or 0)
        return equity - last_equity
    except Exception as exc:
        print(f"[alpaca] could not fetch account for daily P&L check: {exc}")
        return None


def _get_alpaca_live_recent_pnls(db, n: int) -> Optional[List[float]]:
    """Return P&L for the last n completed live round-trips from AlpacaOrder records.

    Only looks at fills from the last 30 days to keep the query small and
    focused on recent performance. Returns None on error (caller skips the check).
    """
    from database.models import AlpacaOrder

    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    _cutoff = datetime.now(timezone.utc)
    try:
        orders = (
            db.query(AlpacaOrder)
            .filter(
                AlpacaOrder.trading_mode == "live",
                AlpacaOrder.status == "filled",
                AlpacaOrder.filled_avg_price.isnot(None),
                AlpacaOrder.filled_qty.isnot(None),
                AlpacaOrder.paper_trade_id.isnot(None),
                AlpacaOrder.filled_at >= _cutoff,
            )
            .order_by(AlpacaOrder.filled_at.desc())
            .limit(n * 4)  # fetch extra to account for incomplete round-trips
            .all()
        )
        by_trade: Dict[int, List] = {}
        for o in orders:
            by_trade.setdefault(o.paper_trade_id, []).append(o)

        completed: List[tuple] = []
        for trade_orders in by_trade.values():
            buys = [o for o in trade_orders if o.side == "buy"]
            sells = [o for o in trade_orders if o.side == "sell"]
            if not (buys and sells):
                continue
            buy = max(buys, key=lambda o: o.filled_at or _epoch)
            sell = max(sells, key=lambda o: o.filled_at or _epoch)
            qty = min(float(buy.filled_qty), float(sell.filled_qty))
            if qty <= 0:
                continue
            pnl = (float(sell.filled_avg_price) - float(buy.filled_avg_price)) * qty
            close_time = max(buy.filled_at or _epoch, sell.filled_at or _epoch)
            completed.append((close_time, pnl))

        completed.sort(key=lambda x: x[0], reverse=True)
        return [pnl for _, pnl in completed[:n]]
    except Exception as exc:
        print(f"[alpaca] could not compute live consecutive P&L: {exc}")
        return None


def _check_circuit_breakers(db, config, pending_notional: float = 0.0) -> None:
    """
    Raise CircuitBreakerError and auto-disable live trading if a limit is breached.
    Checks: max total open exposure, daily loss limit, consecutive loss streak.

    In live mode every check reads from the real Alpaca account / AlpacaOrder
    audit table. In paper/sim mode every check reads from the PaperTrade ledger.
    The two data sources are never mixed.

    pending_notional: notional of the order about to be placed; added to the
    current open exposure so a single order cannot overshoot the configured max.
    """
    from database.models import PaperTrade

    is_live = getattr(config, "alpaca_execution_mode", None) == "live"
    live_broker = get_broker_from_keychain(mode="live") if is_live else None

    # ── Max total open exposure ──────────────────────────────────────────────
    max_exposure = getattr(config, "alpaca_max_total_exposure_usd", None)
    if max_exposure and max_exposure > 0:
        if is_live:
            if live_broker is None:
                print("[alpaca] exposure check: no live broker configured, skipping")
            else:
                live_exposure = _get_alpaca_live_open_exposure(live_broker)
                if live_exposure is None:
                    print("[alpaca] exposure check: live position fetch failed, skipping")
                else:
                    open_exposure = live_exposure + pending_notional
                    if open_exposure >= max_exposure:
                        _disable_live_trading(db, config, f"max total exposure ${max_exposure:.0f} reached (current ${open_exposure:.0f})")
                        raise CircuitBreakerError(f"Max total exposure ${max_exposure:.0f} reached")
        else:
            open_exposure = (
                sum(float(t.amount or 0) for t in db.query(PaperTrade).filter(PaperTrade.exited_at.is_(None)).all())
                + pending_notional
            )
            if open_exposure >= max_exposure:
                _disable_live_trading(db, config, f"max total exposure ${max_exposure:.0f} reached (current ${open_exposure:.0f})")
                raise CircuitBreakerError(f"Max total exposure ${max_exposure:.0f} reached")

    # ── Daily loss limit ─────────────────────────────────────────────────────
    daily_limit = getattr(config, "alpaca_daily_loss_limit_usd", None)
    if daily_limit and daily_limit > 0:
        if is_live:
            if live_broker is None:
                print("[alpaca] daily loss check: no live broker configured, skipping")
            else:
                today_pnl = _get_alpaca_live_daily_pnl(live_broker)
                if today_pnl is None:
                    print("[alpaca] daily loss check: account fetch failed, skipping")
                elif today_pnl <= -daily_limit:
                    _disable_live_trading(db, config, f"daily loss limit ${daily_limit:.0f} hit (P&L ${today_pnl:.2f})")
                    raise CircuitBreakerError(f"Daily loss limit ${daily_limit:.0f} hit")
        else:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            today_pnl = sum(
                float(t.realized_pnl or 0)
                for t in db.query(PaperTrade)
                .filter(PaperTrade.exited_at >= today_start, PaperTrade.realized_pnl.isnot(None))
                .all()
            )
            if today_pnl <= -daily_limit:
                _disable_live_trading(db, config, f"daily loss limit ${daily_limit:.0f} hit (P&L ${today_pnl:.2f})")
                raise CircuitBreakerError(f"Daily loss limit ${daily_limit:.0f} hit")

    # ── Consecutive loss streak ──────────────────────────────────────────────
    max_consec = getattr(config, "alpaca_max_consecutive_losses", None)
    if max_consec and max_consec > 0:
        if is_live:
            recent_pnls = _get_alpaca_live_recent_pnls(db, max_consec)
            if recent_pnls is None:
                print("[alpaca] consecutive loss check: could not compute live P&L, skipping")
            elif len(recent_pnls) >= max_consec and all(pnl < 0 for pnl in recent_pnls):
                _disable_live_trading(db, config, f"{max_consec} consecutive losses")
                raise CircuitBreakerError(f"{max_consec} consecutive losses reached")
        else:
            recent = (
                db.query(PaperTrade)
                .filter(PaperTrade.exited_at.isnot(None), PaperTrade.realized_pnl.isnot(None))
                .order_by(PaperTrade.exited_at.desc())
                .limit(max_consec)
                .all()
            )
            if len(recent) >= max_consec and all(float(t.realized_pnl or 0) < 0 for t in recent):
                _disable_live_trading(db, config, f"{max_consec} consecutive losses")
                raise CircuitBreakerError(f"{max_consec} consecutive losses reached")


def _disable_live_trading(db, config, reason: str) -> None:
    try:
        config.alpaca_execution_mode = "off"
        config.alpaca_live_trading_enabled = False
        db.add(config)
        db.commit()
        print(f"[alpaca] CIRCUIT BREAKER — live trading auto-disabled: {reason}")
    except Exception as exc:
        print(f"[alpaca] failed to auto-disable live trading: {exc}")
    # Best-effort cancel all open orders so no in-flight exposure remains
    try:
        broker = get_broker_from_keychain(mode="live")
        if broker:
            cancelled = broker.cancel_all_orders()
            if cancelled:
                print(f"[alpaca] circuit breaker: cancelled {len(cancelled)} open order(s)")
    except Exception as exc:
        print(f"[alpaca] circuit breaker: cancel_all_orders failed (non-fatal): {exc}")


# ── DB record helpers ─────────────────────────────────────────────────────────

def _record_alpaca_order(
    db,
    paper_trade_id: Optional[int],
    side: str,
    symbol: str,
    notional: Optional[float],
    qty: Optional[float],
    response: Dict[str, Any],
    trading_mode: str,
    extended_hours: bool = False,
    limit_price: Optional[float] = None,
    raw_context: Optional[Dict[str, Any]] = None,
) -> None:
    from database.models import AlpacaOrder
    from sqlalchemy.exc import IntegrityError

    def _parse_dt(raw: Any) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return None

    raw_response: Dict[str, Any] = dict(response or {})
    if raw_context:
        raw_response["_managed_context"] = raw_context

    order = AlpacaOrder(
        paper_trade_id   = paper_trade_id,
        alpaca_order_id  = response.get("id"),
        client_order_id  = response.get("client_order_id"),
        symbol           = symbol.upper(),
        side             = side,
        notional         = notional,
        qty              = qty or (float(response.get("qty") or 0) or None),
        order_type       = response.get("type", "market"),
        time_in_force    = response.get("time_in_force", "day"),
        limit_price      = limit_price,
        extended_hours   = extended_hours,
        status           = response.get("status"),
        filled_qty       = float(response.get("filled_qty") or 0) or None,
        filled_avg_price = float(response.get("filled_avg_price") or 0) or None,
        submitted_at     = _parse_dt(response.get("submitted_at")),
        filled_at        = _parse_dt(response.get("filled_at")),
        trading_mode     = trading_mode,
        raw_response     = raw_response,
    )
    try:
        db.add(order)
        db.commit()
    except IntegrityError:
        db.rollback()


def _record_alpaca_order_error(
    db,
    paper_trade_id: Optional[int],
    side: str,
    symbol: str,
    notional: Optional[float],
    error_msg: str,
    trading_mode: str,
    client_order_id: Optional[str] = None,
) -> None:
    from database.models import AlpacaOrder
    order = AlpacaOrder(
        paper_trade_id  = paper_trade_id,
        client_order_id = client_order_id,
        symbol          = symbol.upper(),
        side            = side,
        notional        = notional,
        status          = "error",
        trading_mode    = trading_mode,
        error_message   = error_msg,
    )
    try:
        db.add(order)
        db.commit()
    except Exception:
        db.rollback()


def _record_alpaca_order_skip(
    db,
    paper_trade_id: Optional[int],
    side: str,
    symbol: str,
    notional: Optional[float],
    trading_mode: str,
    reason: str,
    client_order_id: Optional[str] = None,
) -> None:
    from database.models import AlpacaOrder
    order = AlpacaOrder(
        paper_trade_id  = paper_trade_id,
        client_order_id = client_order_id,
        symbol          = symbol.upper(),
        side            = side,
        notional        = notional,
        status          = "skipped",
        trading_mode    = trading_mode,
        error_message   = reason,
        raw_response    = {"reason": reason},
    )
    try:
        db.add(order)
        db.commit()
    except Exception:
        db.rollback()


def _get_live_symbol_position(broker: "AlpacaBroker", symbol: str) -> Optional[Dict[str, Any]]:
    """Return current live position details for one symbol, or None if flat/unavailable."""
    try:
        pos = broker.get_position(symbol)
    except Exception:
        return None

    if not isinstance(pos, dict) or not pos:
        return None

    market_value = None
    try:
        raw_mv = pos.get("market_value")
        if raw_mv is not None:
            market_value = abs(float(raw_mv))
    except Exception:
        market_value = None

    qty = None
    try:
        raw_qty = pos.get("qty") or pos.get("available_shares")
        if raw_qty is not None:
            qty = float(raw_qty)
    except Exception:
        qty = None

    side = str(pos.get("side") or "").strip().lower()
    if side not in {"long", "short"}:
        if qty is not None:
            if qty > 0:
                side = "long"
            elif qty < 0:
                side = "short"
            else:
                side = ""

    if market_value is None and qty is not None:
        try:
            price = float(pos.get("current_price") or pos.get("avg_entry_price") or 0.0)
            if price > 0:
                market_value = abs(qty) * price
        except Exception:
            market_value = None

    return {
        "market_value": market_value,
        "qty": abs(qty) if qty is not None else None,
        "side": side or None,
    }


def _get_all_live_positions(broker: "AlpacaBroker") -> List[Dict[str, Any]]:
    """Return all live positions from Alpaca as a list of dicts."""
    try:
        positions = broker.get_positions()
        if not isinstance(positions, list):
            return []
        result = []
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            qty_raw = pos.get("qty") or pos.get("available_shares")
            qty = None
            try:
                qty = float(qty_raw) if qty_raw is not None else None
            except (TypeError, ValueError):
                qty = None
            if qty is not None and abs(qty) > 0:
                result.append({
                    "symbol": str(pos.get("symbol", "")).upper().strip(),
                    "qty": abs(qty),
                    "side": str(pos.get("side") or "").strip().lower(),
                    "market_value": abs(float(pos.get("market_value") or 0)),
                })
        return result
    except Exception:
        return []


def _has_conflicting_live_position(
    broker: "AlpacaBroker",
    execution_ticker: str,
    signal_type: str,
) -> Optional[Dict[str, Any]]:
    """Check if there's a conflicting live position for the same underlying.

    Returns the conflicting position dict if found, None otherwise.
    A conflict exists when:
    - The execution ticker itself is a conflicting position (same underlying, opposite direction)
    - The signal is SHORT but there's a LONG position in the same underlying's bull ETF
    - The signal is LONG but there's a SHORT position in the same underlying's bear ETF
    """
    from services.trading_instruments import INSTRUMENT_SPECS

    ticker = str(execution_ticker or "").upper().strip()
    stype = str(signal_type or "").upper()

    # Find which underlying this ticker belongs to
    underlying = None
    ticker_bucket = None  # "bull" or "bear"
    for _under, _spec in INSTRUMENT_SPECS.items():
        _under = _under.upper()
        for _bucket, _tickers in _spec.get("bull", {}).items():
            if str(_tickers).upper() == ticker:
                underlying = _under
                ticker_bucket = "bull"
                break
        if underlying:
            break
        for _bucket, _tickers in _spec.get("bear", {}).items():
            if str(_tickers).upper() == ticker:
                underlying = _under
                ticker_bucket = "bear"
                break
        if underlying:
            break

    if not underlying:
        # Not a known instrument — check if the ticker itself is open
        live_pos = _get_live_symbol_position(broker, ticker)
        if live_pos and live_pos.get("qty", 0) > 0:
            return live_pos
        return None

    # Get all live positions
    all_positions = _get_all_live_positions(broker)

    # Check each live position for conflict
    for pos in all_positions:
        pos_ticker = pos["symbol"]
        if pos_ticker == ticker:
            continue  # Skip the position we're about to open

        # Find which underlying this position belongs to
        pos_underlying = None
        pos_bucket = None
        for _under, _spec in INSTRUMENT_SPECS.items():
            _under = _under.upper()
            for _bucket, _tickers in _spec.get("bull", {}).items():
                if str(_tickers).upper() == pos_ticker:
                    pos_underlying = _under
                    pos_bucket = "bull"
                    break
            if pos_underlying:
                break
            for _bucket, _tickers in _spec.get("bear", {}).items():
                if str(_tickers).upper() == pos_ticker:
                    pos_underlying = _under
                    pos_bucket = "bear"
                    break
            if pos_underlying:
                break

        if pos_underlying != underlying:
            continue  # Different underlying — no conflict

        # Same underlying — check for opposing direction
        if ticker_bucket and pos_bucket and ticker_bucket != pos_bucket:
            return pos  # Opposing bull/bear for same underlying

    return None


def _dispatch_close_with_retry(
    db,
    paper_trade,
    broker,
    config,
    max_retries: int = 3,
    retry_delay_seconds: float = 5.0,
) -> bool:
    """Dispatch a close order with retry logic.

    Returns True if the close was successfully dispatched, False otherwise.
    Records a dispatch error in the database if all retries fail.
    """
    from database.models import AlpacaDispatchError

    symbol = str(getattr(paper_trade, "execution_ticker", "") or getattr(paper_trade, "underlying", "")).upper()
    paper_id = getattr(paper_trade, "id", None)

    for attempt in range(1, max_retries + 1):
        try:
            maybe_execute_alpaca_order(db, paper_trade, "close", config)
            # Check if the order was actually recorded
            from database.models import AlpacaOrder as _AO
            order = db.query(_AO).filter(
                _AO.paper_trade_id == paper_id,
                _AO.symbol == symbol,
                _AO.side == "sell",
            ).order_by(_AO.created_at.desc()).first()
            if order:
                print(f"[alpaca] close dispatched for {symbol} (attempt {attempt}, order_id={order.id})")
                return True
            # If no order recorded, the close may have been skipped (dust, etc.)
            # Check if the position is actually closed
            live_pos = _get_live_symbol_position(broker, symbol)
            if live_pos and live_pos.get("qty", 0) <= 0:
                print(f"[alpaca] close confirmed for {symbol} (position already flat, attempt {attempt})")
                return True
        except Exception as exc:
            print(f"[alpaca] close attempt {attempt}/{max_retries} for {symbol} failed: {exc}")

        if attempt < max_retries:
            print(f"[alpaca] retrying close for {symbol} in {retry_delay_seconds}s (attempt {attempt + 1})...")
            import time as _time
            _time.sleep(retry_delay_seconds)

    # All retries failed — record dispatch error
    try:
        live_pos = _get_live_symbol_position(broker, symbol)
        qty_info = f" (live qty={live_pos.get('qty') if live_pos else 'unknown'})" if live_pos else ""
        dispatch_error = AlpacaDispatchError(
            symbol=symbol,
            underlying=str(getattr(paper_trade, "underlying", "") or ""),
            error_type="close_failed",
            error_message=f"Failed to close {symbol} after {max_retries} attempts{qty_info}",
            paper_trade_id=paper_id,
            trading_mode=str(getattr(config, "alpaca_execution_mode", "live") or "live"),
        )
        db.add(dispatch_error)
        db.commit()
        print(f"[alpaca] DISPATCH ERROR recorded: {dispatch_error.error_message}")
    except Exception as db_exc:
        print(f"[alpaca] failed to record dispatch error: {db_exc}")

    return False


def _configured_live_execution_symbols(config) -> Set[str]:
    """Return execution tickers this app is allowed to trade for the current config.

    Includes tracked symbols, custom symbols, and all inverse ETFs from the
    mapping so SHORT signals can be routed to them.
    """
    from services.trading_instruments import INSTRUMENT_SPECS

    allowed: Set[str] = set()

    # Always include inverse ETFs so SHORT→inverse mapping works
    allowed.update(INVERSE_ETF_MAP.values())

    tracked_symbols = getattr(config, "tracked_symbols", None) or ["USO", "IBIT", "QQQ", "SPY"]
    for raw_symbol in tracked_symbols:
        symbol = str(raw_symbol or "").upper().strip()
        if not symbol:
            continue
        spec = INSTRUMENT_SPECS.get(symbol)
        if spec:
            allowed.add(symbol)
            for direction in ("bull", "bear"):
                allowed.update(
                    str(ticker).upper().strip()
                    for ticker in spec.get(direction, {}).values()
                    if str(ticker).strip()
                )
        else:
            allowed.add(symbol)

    custom_symbols = getattr(config, "custom_symbols", None) or []
    for raw_symbol in custom_symbols:
        symbol = str(raw_symbol or "").upper().strip()
        if symbol:
            allowed.add(symbol)

    return allowed


def _is_live_symbol_configured(config, paper_trade) -> bool:
    """Return True when this trade resolves to a symbol the user has configured.

    For SHORT signals where execution_ticker == underlying (no ETF mapped),
    also check if an inverse ETF exists in the mapping — those will be
    allowed through and executed as inverse ETF buys.
    """
    symbol = str(getattr(paper_trade, "execution_ticker", "") or getattr(paper_trade, "underlying", "")).upper().strip()
    underlying = str(getattr(paper_trade, "underlying", "") or "").upper().strip()
    if not symbol and not underlying:
        return False
    allowed = _configured_live_execution_symbols(config)
    if symbol and symbol in allowed:
        return True
    if underlying and underlying in allowed:
        return True
    # Allow inverse ETFs for SHORT signals even if the ETF itself isn't in tracked symbols
    signal_type = str(getattr(paper_trade, "signal_type", "")).upper().strip()
    if signal_type == "SHORT" and symbol and symbol.upper() == underlying.upper():
        # Direct short — check if an inverse ETF is mapped
        inverse_etf = INVERSE_ETF_MAP.get(symbol)
        if inverse_etf and inverse_etf in allowed:
            return True
    return False


def _alpaca_order_effective_qty(order) -> float:
    """Best-effort executed or submitted quantity for one AlpacaOrder row."""
    try:
        filled_qty = float(getattr(order, "filled_qty", 0.0) or 0.0)
    except Exception:
        filled_qty = 0.0
    if filled_qty > 0:
        return filled_qty

    status = str(getattr(order, "status", "") or "").strip().lower()
    if status in {"error", "skipped", "cancelled", "expired", "rejected"}:
        return 0.0

    try:
        qty = float(getattr(order, "qty", 0.0) or 0.0)
    except Exception:
        qty = 0.0
    return qty if qty > 0 else 0.0


def _get_managed_trade_qty(db, paper_trade_id: Optional[int], trading_mode: str) -> float:
    """Return the app-managed net quantity still associated with one paper trade."""
    if paper_trade_id is None:
        return 0.0

    from database.models import AlpacaOrder

    rows = (
        db.query(AlpacaOrder)
        .filter(
            AlpacaOrder.paper_trade_id == paper_trade_id,
            AlpacaOrder.trading_mode == trading_mode,
        )
        .order_by(AlpacaOrder.created_at.asc(), AlpacaOrder.id.asc())
        .all()
    )

    net_qty = 0.0
    for row in rows:
        effective_qty = _alpaca_order_effective_qty(row)
        if effective_qty <= 0:
            continue
        side = str(getattr(row, "side", "") or "").strip().lower()
        if side == "buy":
            net_qty += effective_qty
        elif side == "sell":
            net_qty -= effective_qty

    return abs(net_qty)


def _get_managed_trade_baseline_qty(db, paper_trade_id: Optional[int], trading_mode: str) -> float:
    """Return the pre-existing live quantity recorded when this trade was opened."""
    if paper_trade_id is None:
        return 0.0

    from database.models import AlpacaOrder

    rows = (
        db.query(AlpacaOrder)
        .filter(
            AlpacaOrder.paper_trade_id == paper_trade_id,
            AlpacaOrder.trading_mode == trading_mode,
        )
        .order_by(AlpacaOrder.created_at.asc(), AlpacaOrder.id.asc())
        .all()
    )

    for row in rows:
        raw_response = getattr(row, "raw_response", None)
        if not isinstance(raw_response, dict):
            continue
        managed_context = raw_response.get("_managed_context")
        if not isinstance(managed_context, dict):
            continue
        try:
            baseline_qty = float(managed_context.get("pre_existing_qty") or 0.0)
        except Exception:
            baseline_qty = 0.0
        if baseline_qty > 0:
            return baseline_qty

    return 0.0


def _same_trading_day_as_now(ts: Optional[datetime]) -> bool:
    if ts is None:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(_ET).date() == datetime.now(_ET).date()


def _get_entry_conviction_block_reason(paper_trade, event: str, risk_profile: Optional[str] = None) -> Optional[str]:
    """Check if the conviction level blocks this trade entry.

    Crazy profile allows LOW conviction entries (matching paper_trading.py logic).
    All other profiles block LOW conviction entries unconditionally.
    """
    if event != "open":
        return None
    conviction = str(getattr(paper_trade, "conviction_level", "MEDIUM") or "MEDIUM").upper()
    if conviction == "LOW":
        # Crazy profile allows LOW conviction entries
        if risk_profile and str(risk_profile).strip().lower() == "crazy":
            return None
        return "entry rule: low conviction blocked"
    return None


def _get_stop_loss_block_reason(
    broker: "AlpacaBroker",
    symbol: str,
    event: str,
) -> Optional[str]:
    """Check if the live position has hit stop-loss."""
    if event != "open":
        return None
    try:
        from services.app_config import get_or_create_app_config as _get_cfg
        from database.models import AlpacaConfig as _AC
        # Need the actual config object — caller passes it
        pass
    except Exception:
        pass
    return None  # Caller handles via config


def _check_live_position_stop_loss(
    broker: "AlpacaBroker",
    symbol: str,
    stop_loss_pct: float,
) -> Optional[Dict[str, Any]]:
    """Check if a live position has hit stop-loss. Returns position dict if triggered, else None."""
    if stop_loss_pct <= 0:
        return None
    try:
        pos = broker.get_position(symbol)
        if not pos:
            return None
        qty = float(pos.get("qty") or pos.get("available_shares") or 0)
        if qty == 0:
            return None
        entry_price = float(pos.get("avg_entry_price") or 0)
        current_price = float(pos.get("current_price") or 0)
        if entry_price <= 0 or current_price <= 0:
            return None
        side = str(pos.get("side") or "").lower()
        if side == "short":
            pnl_pct = (entry_price - current_price) / entry_price * 100
        else:
            pnl_pct = (current_price - entry_price) / entry_price * 100
        if pnl_pct <= -stop_loss_pct:
            return {
                "pnl_pct": round(pnl_pct, 4),
                "entry_price": entry_price,
                "current_price": current_price,
                "side": side,
                "qty": abs(qty),
            }
    except Exception as exc:
        print(f"[alpaca] stop-loss check error for {symbol}: {exc}")
    return None


def _check_live_position_take_profit(
    broker: "AlpacaBroker",
    symbol: str,
    take_profit_pct: float,
) -> Optional[Dict[str, Any]]:
    """Check if a live position has hit take-profit. Returns position dict if triggered, else None."""
    if take_profit_pct <= 0:
        return None
    try:
        pos = broker.get_position(symbol)
        if not pos:
            return None
        qty = float(pos.get("qty") or pos.get("available_shares") or 0)
        if qty == 0:
            return None
        entry_price = float(pos.get("avg_entry_price") or 0)
        current_price = float(pos.get("current_price") or 0)
        if entry_price <= 0 or current_price <= 0:
            return None
        side = str(pos.get("side") or "").lower()
        if side == "short":
            pnl_pct = (entry_price - current_price) / entry_price * 100
        else:
            pnl_pct = (current_price - entry_price) / entry_price * 100
        if pnl_pct >= take_profit_pct:
            return {
                "pnl_pct": round(pnl_pct, 4),
                "entry_price": entry_price,
                "current_price": current_price,
                "side": side,
                "qty": abs(qty),
            }
    except Exception as exc:
        print(f"[alpaca] take-profit check error for {symbol}: {exc}")
    return None


# ── Main hook ─────────────────────────────────────────────────────────────────

def maybe_execute_alpaca_order(db, paper_trade, event: str, config) -> None:
    """
    Optionally route a paper trade open/close to Alpaca.
    event: "open" | "close"
    Never raises — all failures are logged to alpaca_orders and printed.
    """
    execution_mode = str(getattr(config, "alpaca_execution_mode", "off") or "off").strip().lower()
    if execution_mode not in {"paper", "live"}:
        _sym = str(getattr(paper_trade, "execution_ticker", "") or getattr(paper_trade, "underlying", "") or "?").upper()
        print(f"[alpaca] skipping {event} for {_sym}: alpaca_execution_mode='{execution_mode}' (must be 'paper' or 'live')")
        return

    broker = get_broker_from_keychain(mode=execution_mode)
    if broker is None:
        _sym = str(getattr(paper_trade, "execution_ticker", "") or getattr(paper_trade, "underlying", "") or "?").upper()
        print(f"[alpaca] skipping {event} for {_sym}: broker could not be initialized from keychain (mode={execution_mode})")
        return

    symbol       = str(getattr(paper_trade, "execution_ticker", "") or getattr(paper_trade, "underlying", "")).upper()
    signal_type  = str(getattr(paper_trade, "signal_type", "LONG")).upper()
    notional     = float(getattr(paper_trade, "amount", 100.0) or 100.0)
    shares       = float(getattr(paper_trade, "shares", 0.0) or 0.0)
    entry_price  = float(getattr(paper_trade, "entry_price", 0.0) or 0.0)
    paper_id     = getattr(paper_trade, "id", None)
    direct_short = _is_direct_short(paper_trade)
    allow_short  = bool(getattr(config, "alpaca_allow_short_selling", False))
    live_pos = _get_live_symbol_position(broker, symbol)
    live_side = str((live_pos or {}).get("side") or "").lower()
    live_value = (live_pos or {}).get("market_value")
    live_qty = (live_pos or {}).get("qty")

    if event == "open" and not _is_live_symbol_configured(config, paper_trade):
        _record_alpaca_order_skip(
            db,
            paper_id,
            "buy" if not direct_short else "sell",
            symbol,
            notional,
            broker.mode,
            f"symbol {symbol} is not enabled in tracked/custom symbols",
        )
        print(f"[alpaca] skipping open for {symbol}: symbol is not enabled in tracked/custom symbols")
        return

    # ── Entry conviction gate ───────────────────────────────────────────
    conviction_block = _get_entry_conviction_block_reason(
        paper_trade, event,
        risk_profile=getattr(config, "risk_profile", None),
    )
    if conviction_block:
        side_hint = "buy" if (event == "open" and not direct_short) else "sell"
        _record_alpaca_order_skip(
            db, paper_id, side_hint, symbol,
            notional if event == "open" else None,
            broker.mode, conviction_block,
        )
        print(f"[alpaca] skipping {event} for {symbol}: {conviction_block}")
        return

    # ── Live position direction-flip ──────────────────────────────────
    # When the new signal opposes an existing live position, close the live
    # position first then fall through to open the new one.
    # P&L exits are managed by the paper-trading trailing-stop system;
    # those closes propagate to Alpaca when the paper trade closes.
    if event == "open" and execution_mode == "live" and live_side:
        _want_long         = (signal_type == "LONG")
        _want_short_direct = direct_short and allow_short
        _conflict = (
            (live_side == "long"  and _want_short_direct) or
            (live_side == "short" and _want_long)
        )
        if _conflict:
            _flip_close_side = "sell" if live_side == "long" else "buy"
            _flip_qty        = abs(float(live_qty or 0))
            _live_cur_price  = float((live_pos or {}).get("current_price") or 0)
            _flip_slippage   = float(getattr(config, "alpaca_limit_slippage_pct", 0.002) or 0.002)
            _flip_ext        = _is_extended_hours_now(config)
            print(
                f"[alpaca] direction flip for {symbol}: closing existing {live_side} "
                f"(qty={_flip_qty}) before opening {signal_type}"
            )
            try:
                if _flip_ext and _live_cur_price > 0 and _flip_qty > 0:
                    _flip_limit = round(
                        _live_cur_price * (1 - _flip_slippage) if _flip_close_side == "sell"
                        else _live_cur_price * (1 + _flip_slippage),
                        2,
                    )
                    _flip_resp = broker.place_order(
                        symbol=symbol,
                        side=_flip_close_side,
                        qty=_flip_qty,
                        order_type="limit",
                        limit_price=_flip_limit,
                        extended_hours=True,
                        time_in_force="day",
                        client_order_id=f"gr-flip-{symbol.lower()}-{int(_time.time())}",
                    )
                else:
                    _flip_limit = None
                    _flip_resp  = broker.close_position(symbol)
                _record_alpaca_order(
                    db, None, _flip_close_side, symbol, None, _flip_qty,
                    _flip_resp, broker.mode, _flip_ext, _flip_limit, None,
                )
                print(
                    f"[alpaca] direction flip: {symbol} {live_side} closed "
                    f"(order={_flip_resp.get('id')}, status={_flip_resp.get('status')})"
                )
            except Exception as _flip_exc:
                _record_alpaca_order_error(
                    db, paper_id, _flip_close_side, symbol, None,
                    f"direction flip close failed: {_flip_exc}", broker.mode,
                )
                print(f"[alpaca] direction flip failed for {symbol}: {_flip_exc}")
                return
            # Fall through — live position closed, proceed to open the new one

    # ── Determine Alpaca side ────────────────────────────────────────────────
    if event == "open":
        configured_notional = None
        if broker.mode == "paper":
            configured_notional = getattr(config, "alpaca_paper_trade_amount_usd", None)
        elif broker.mode == "live":
            configured_notional = getattr(config, "alpaca_live_trade_amount_usd", None)
        try:
            configured_notional_value = float(configured_notional) if configured_notional is not None else 0.0
        except (TypeError, ValueError):
            configured_notional_value = 0.0
        is_fixed = bool(getattr(config, "alpaca_fixed_order_size", False))
        if is_fixed and configured_notional_value > 0:
            notional = configured_notional_value

        if direct_short:
            if not allow_short:
                # ── Map to inverse ETF ────────────────────────────────────
                # Look up the underlying ticker in the inverse ETF map.
                # If found, buy the inverse ETF instead of shorting the stock.
                underlying_ticker = str(getattr(paper_trade, "underlying", symbol)).upper()
                inverse_etf = INVERSE_ETF_MAP.get(underlying_ticker)
                if inverse_etf:
                    symbol = inverse_etf
                    side = "buy"
                    print(
                        f"[alpaca] SHORT→inverse ETF: {underlying_ticker} → buying {inverse_etf} "
                        f"(${notional:.2f}, paper_id={paper_id})"
                    )
                else:
                    print(f"[alpaca] skipping direct short on {symbol}: alpaca_allow_short_selling disabled")
                    _record_alpaca_order_error(
                        db, paper_id, "sell", symbol, notional,
                        f"short selling disabled; no inverse ETF mapped for {symbol}",
                        broker.mode,
                    )
                    return
            else:
                side = "sell"   # real Alpaca short-sell
        else:
            side = "buy"    # long, or buying the inverse ETF for a short signal

        try:
            _check_circuit_breakers(db, config, pending_notional=notional)
        except CircuitBreakerError as exc:
            _record_alpaca_order_error(db, paper_id, side, symbol, notional, f"circuit breaker: {exc}", broker.mode)
            return

        max_pos = getattr(config, "alpaca_max_position_usd", None)
        if max_pos and max_pos > 0:
            same_direction_live = (
                (side == "buy" and live_side == "long") or
                (side == "sell" and live_side == "short")
            )
            if same_direction_live and live_value is not None:
                remaining_capacity = max(0.0, float(max_pos) - float(live_value))
                if remaining_capacity <= 0.01:
                    _record_alpaca_order_skip(
                        db,
                        paper_id,
                        side,
                        symbol,
                        0.0,
                        broker.mode,
                        f"position cap reached for {symbol}: current ${live_value:.2f} >= max ${float(max_pos):.2f}",
                    )
                    print(
                        f"[alpaca] skipping {side} for {symbol}: "
                        f"position cap reached (current ${live_value:.2f}, max ${float(max_pos):.2f})"
                    )
                    return
                notional = min(notional, remaining_capacity)
            else:
                notional = min(notional, max_pos)

    # ── Build open_raw_context early ─────────────────────────────────────
    open_raw_context: Optional[Dict[str, Any]] = None
    if event == "open":
        same_direction_live = (
            (side == "buy" and live_side == "long") or
            (side == "sell" and live_side == "short")
        )
        open_raw_context = {
            "pre_existing_qty": float(live_qty or 0.0) if same_direction_live else 0.0,
            "pre_existing_side": live_side if same_direction_live else None,
        }
    elif event == "close":
        # Closing a direct short means buying back to cover; everything else is a sell
        side = "buy" if direct_short else "sell"

    if event == "close":
        # Guard: only close if a live open order was actually placed for this trade.
        # A skipped or failed open (direct short disabled, circuit breaker, etc.)
        # produces only an error row, so _has_live_open_order returns False and we
        # skip — preventing a stray close order from creating unintended exposure.
        if not _has_live_open_order(db, paper_id):
            print(
                f"[alpaca] skipping close for {symbol} (paper_id={paper_id}): "
                "no successful open order on record"
            )
            return

        # ── Minimum notional guard (skip dust closes) ─────────────────────
        # If the paper position is worth less than $1.00, skip the Alpaca close.
        # Prevents sub-penny "dust" orders that waste API calls and clutter
        # the order history. The dust is an acceptable loss in the paper
        # simulator; real accounts may still have dust from partial fills.
        _min_close_notional = float(getattr(config, "alpaca_min_close_notional", 1.0))
        if shares > 0 and entry_price > 0:
            _notional = shares * entry_price
        elif notional > 0:
            _notional = notional
        else:
            _notional = 0.0
        if _notional > 0 and _notional < _min_close_notional:
            print(
                f"[alpaca] skipping close for {symbol} (paper_id={paper_id}): "
                f"notional ${_notional:.4f} < ${_min_close_notional:.2f} threshold (dust)"
            )
            # Record as intentionally skipped for audit trail
            _record_alpaca_order_skip(
                db,
                paper_id,
                "sell" if not direct_short else "buy",
                symbol,
                None,
                broker.mode,
                f"below_min_notional(${_notional:.4f} < ${_min_close_notional:.2f})",
            )
            return

        # ── Compute app-managed qty for partial close ────────────────────
        # Only close the shares the app opened, leaving any manual baseline alone.
        # Read pre_existing_qty from the open order's _managed_context.
        from database.models import AlpacaOrder as _AO
        _open_order = (
            db.query(_AO)
            .filter(
                _AO.paper_trade_id == paper_id,
                _AO.status.in_(('filled', 'accepted', 'partially_filled')),
            )
            .first()
        )
        _managed_ctx = {}
        if _open_order:
            _managed_ctx = (getattr(_open_order, 'raw_response') or {}).get('_managed_context', {}) or {}
        _pre_existing = float(_managed_ctx.get('pre_existing_qty') or 0.0)

        # Current live position qty
        _live_qty = 0.0
        if live_qty is not None:
            try:
                _live_qty = float(live_qty)
            except Exception:
                _live_qty = 0.0

        # App-managed qty = current live qty minus any pre-existing manual shares
        _managed_qty = max(_live_qty - _pre_existing, 0.0)
        if _managed_qty <= 0:
            print(
                f'[alpaca] skipping close for {symbol} (paper_id={paper_id}): '
                f'no app-managed shares to close (live={_live_qty}, pre-existing={_pre_existing})'
            )
            return

    # ── Build order parameters ───────────────────────────────────────────────
    ext_hours  = _is_extended_hours_now(config)

    slippage   = float(getattr(config, "alpaca_limit_slippage_pct", 0.002) or 0.002)
    limit_price: Optional[float] = None
    qty:         Optional[float] = None
    use_notional: Optional[float] = None
    order_type = str(getattr(config, "alpaca_order_type", "market") or "market")
    time_in_force = "day"

    if ext_hours:
        # Pre/post-market: Alpaca requires explicit extended_hours + limit + qty.
        # For extended hours closes, Alpaca does NOT support close_position endpoint,
        # so we fall back to computing qty.
        if event == "open" and entry_price > 0 and notional > 0:
            qty = round(notional / entry_price, 6)
        elif shares > 0:
            qty = shares
        elif entry_price > 0:
            qty = round(notional / entry_price, 6)
        else:
            _record_alpaca_order_error(
                db, paper_id, side, symbol, notional,
                "extended hours order skipped: no price for qty calculation",
                broker.mode,
            )
            return
        limit_price = round(
            entry_price * (1 + slippage) if side == "buy" else entry_price * (1 - slippage),
            2,
        )
        limit_price = max(0.01, limit_price)
        order_type = "limit"
    else:
        if event == "close" and shares > 0:
            # For closes, use explicit qty (app-managed shares) instead of notional
            qty = shares
            use_notional = None
        else:
            use_notional = notional
        if order_type == "limit" and entry_price > 0:
            limit_price = round(
                entry_price * (1 + slippage) if side == "buy" else entry_price * (1 - slippage),
                2,
            )
            limit_price = max(0.01, limit_price)

    client_order_id = f"gr-{paper_id}-{event[:1]}-{int(_time.time())}"

    _try_exec_order = True
    _attempt = 0
    _last_error = None
    while _try_exec_order:
        _attempt += 1
        try:
            response = broker.place_order(
                symbol          = symbol,
                side            = side,
                notional        = use_notional,
                qty             = qty,
                order_type      = order_type,
                time_in_force   = time_in_force,
                limit_price     = limit_price,
                extended_hours  = ext_hours,
                client_order_id = client_order_id,
            )
            _record_alpaca_order(
                db, paper_id, side, symbol, use_notional, qty,
                response, broker.mode, ext_hours, limit_price, open_raw_context,
            )
            _log_msg = f"[alpaca] {event} {side} {symbol}: "
            if _attempt > 1:
                _log_msg += f"[RETRY-{_attempt}] "
            _log_msg += (
                f"order_id={response.get('id')} status={response.get('status')} "
                f"qty={response.get('qty')} filled_qty={response.get('filled_qty')} "
                f"filled_avg_price={response.get('filled_avg_price')}"
            )
            print(_log_msg)
            break
        except Exception as exc:
            _last_error = exc
            if _attempt >= 2:
                # Exhausted retries — log to decision log
                _record_alpaca_order_error(
                    db, paper_id, side, symbol, use_notional or notional,
                    f"{str(exc)} (after {_attempt} attempts)", broker.mode, client_order_id,
                )
                # Decision log fallback
                try:
                    from database.engine import DecisionLogSessionLocal
                    from database.models import DecisionLogTrade
                    from services.decision_logger import logger as _dl
                    _ddb = DecisionLogSessionLocal()
                    try:
                        _trade_log = _ddb.query(DecisionLogTrade).filter(
                            DecisionLogTrade.paper_trade_id == paper_id
                        ).first()
                        if _trade_log:
                            _dl.log_trade_event(
                                _ddb,
                                trade_log_id=_trade_log.id,
                                event_type="execution_failed",
                                run_id=None,
                                keep_vs_close="open",
                                decision_reason=f"Alpaca order failed after {_attempt} attempts: {exc}",
                                event_details={
                                    "symbol": symbol,
                                    "side": side,
                                    "notional": use_notional or notional,
                                    "attempts": _attempt,
                                    "error": str(exc),
                                },
                            )
                            _ddb.commit()
                    except Exception as _dlx:
                        _ddb.rollback()
                        print(f"[decision-log] execution fallback error: {_dlx}")
                    finally:
                        _ddb.close()
                except Exception as _dlx:
                    print(f"[decision-log] execution fallback error (non-fatal): {_dlx}")
                print(f"[alpaca] order FAILED after {_attempt} attempts for {side} {symbol}: {exc}")
                _try_exec_order = False
            else:
                # First failure — retry after brief delay
                print(f"[alpaca] order failed on attempt {_attempt} for {side} {symbol}: {exc} — retrying...")
                _time.sleep(2)


# ── Fill polling ──────────────────────────────────────────────────────────────

def poll_unfilled_orders(db) -> int:
    """
    Query Alpaca for the current status of any pending (non-terminal) orders
    and update the alpaca_orders rows. Returns count of rows updated.
    For rows missing alpaca_order_id (e.g. response lost), falls back to
    get_order_by_client_id to recover the Alpaca order ID.
    """
    from database.models import AlpacaOrder

    broker = get_broker_from_keychain(mode="live")
    if broker is None:
        return 0

    pending = (
        db.query(AlpacaOrder)
        .filter(
            AlpacaOrder.filled_at.is_(None),
            AlpacaOrder.status.notin_(list(_TERMINAL_STATUSES)),
            AlpacaOrder.error_message.is_(None),
        )
        .all()
    )

    updated = 0
    for order in pending:
        try:
            if order.alpaca_order_id:
                data = broker.get_order(order.alpaca_order_id)
            elif order.client_order_id:
                # Fallback: recover order ID via our own client_order_id
                data = broker.get_order_by_client_id(order.client_order_id)
                recovered_id = data.get("id")
                if recovered_id:
                    order.alpaca_order_id = recovered_id
            else:
                continue

            new_status = data.get("status")
            if not new_status or new_status == order.status:
                continue
            order.status           = new_status
            order.filled_qty       = float(data.get("filled_qty") or 0) or None
            order.filled_avg_price = float(data.get("filled_avg_price") or 0) or None
            raw_filled = data.get("filled_at")
            if raw_filled:
                try:
                    order.filled_at = datetime.fromisoformat(str(raw_filled).replace("Z", "+00:00"))
                except Exception:
                    pass
            order.raw_response = data
            updated += 1
        except httpx.HTTPStatusError as exc:
            oid = order.alpaca_order_id or order.client_order_id or order.id
            if exc.response.status_code == 404:
                # Order no longer exists on Alpaca — mark as cancelled to stop polling
                order.status = "cancelled"
                order.filled_at = datetime.now(timezone.utc)
                db.commit()
                updated += 1
                print(f"[alpaca] poll: order {oid} recovered via 404, marked cancelled")
            else:
                print(f"[alpaca] poll: order {oid} error: {exc}")
        except Exception as exc:
            oid = order.alpaca_order_id or order.client_order_id or order.id
            print(f"[alpaca] poll: order {oid} error: {exc}")

    if updated:
        db.commit()
    return updated


# ── Startup reconciliation ────────────────────────────────────────────────────

def reconcile_on_startup(db) -> None:
    """
    Compare open AlpacaOrder rows (no filled_at, non-terminal) against live
    Alpaca positions. Logs any orphans — positions open in our DB but absent
    from Alpaca. Does NOT auto-close anything; operator must intervene.
    """
    from database.models import AlpacaOrder

    broker = get_broker_from_keychain(mode="live")
    if broker is None:
        return

    try:
        positions   = broker.get_positions()
        alpaca_syms = {p.get("symbol", "").upper() for p in positions}
    except Exception as exc:
        print(f"[alpaca] reconcile_on_startup: failed to fetch positions: {exc}")
        return

    open_buy_orders = (
        db.query(AlpacaOrder)
        .filter(
            AlpacaOrder.filled_at.is_(None),
            AlpacaOrder.status.notin_(list(_TERMINAL_STATUSES)),
            AlpacaOrder.side == "buy",
        )
        .all()
    )

    changed = False
    for order in open_buy_orders:
        if order.symbol not in alpaca_syms:
            if not order.is_orphan:
                order.is_orphan = True
                changed = True
            if not order.orphan_acknowledged:
                print(
                    f"[alpaca] ORPHAN: {order.symbol} is open in our DB "
                    f"(AlpacaOrder id={order.id}, alpaca_order_id={order.alpaca_order_id}) "
                    f"but NOT found in Alpaca {broker.mode} positions. Manual review required."
                )
    if changed:
        db.commit()


# ── Overnight retry ───────────────────────────────────────────────────────────

def retry_window_skipped_orders(db, config) -> None:
    """
    Re-dispatch paper trade opens that were skipped solely because they arrived
    outside all valid Alpaca trading windows (e.g. 11 PM ET). Called at the top
    of each dispatch cycle so overnight paper opens execute when the market opens.

    Only fires when we are now inside a valid window. Skips any paper trade that
    has already been exited or already has a successful open order on record.
    """
    from database.models import AlpacaOrder, PaperTrade

    if not (_is_regular_market_hours_now() or _is_extended_hours_now(config)):
        return

    try:
        stranded = (
            db.query(AlpacaOrder)
            .filter(
                AlpacaOrder.status == "skipped",
                AlpacaOrder.side == "buy",
                AlpacaOrder.error_message.like("%outside all trading windows%"),
            )
            .all()
        )
    except Exception as exc:
        print(f"[alpaca] retry_window_skipped_orders: query failed: {exc}")
        return

    for skipped_order in stranded:
        pt_id = skipped_order.paper_trade_id
        if pt_id is None:
            continue

        pt = db.query(PaperTrade).filter(PaperTrade.id == pt_id).first()
        if pt is None or pt.exited_at is not None:
            continue

        already_placed = (
            db.query(AlpacaOrder)
            .filter(
                AlpacaOrder.paper_trade_id == pt_id,
                AlpacaOrder.status.in_(("filled", "accepted", "partially_filled")),
            )
            .first()
        )
        if already_placed:
            continue

        symbol = str(
            getattr(pt, "execution_ticker", "") or getattr(pt, "underlying", "")
        ).upper()
        print(f"[alpaca] retrying window-skipped open for {symbol} (paper_id={pt_id})")
        try:
            maybe_execute_alpaca_order(db, pt, "open", config)
        except Exception as exc:
            print(f"[alpaca] retry_window_skipped_orders: dispatch failed for {symbol}: {exc}")
