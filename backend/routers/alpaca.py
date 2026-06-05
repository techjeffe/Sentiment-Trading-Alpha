"""
Alpaca brokerage admin routes.
All routes require the admin token (if ADMIN_API_TOKEN is set).
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.engine import get_db
from security import require_admin_token
from services.alpaca_broker import get_broker_from_keychain, poll_unfilled_orders
from services.app_config import get_or_create_app_config, update_app_config
from services.secret_store import (
    clear_alpaca_secrets,
    get_alpaca_secret_status,
    save_alpaca_secrets,
    get_alpaca_credentials_for_mode,
)

router = APIRouter(prefix="/alpaca", tags=["Alpaca"])


class AlpacaSecretsPayload(BaseModel):
    api_key: str
    secret_key: str
    trading_mode: str = "paper"


class AlpacaSettingsPayload(BaseModel):
    alpaca_execution_mode:                 Optional[str]   = None
    alpaca_live_trading_enabled:           Optional[bool]  = None
    alpaca_allow_short_selling:            Optional[bool]  = None
    alpaca_paper_trade_amount_usd:         Optional[float] = None
    alpaca_live_trade_amount_usd:          Optional[float] = None
    alpaca_max_position_usd:               Optional[float] = None
    alpaca_max_total_exposure_usd:         Optional[float] = None
    alpaca_order_type:                     Optional[str]   = None
    alpaca_limit_slippage_pct:             Optional[float] = None
    alpaca_daily_loss_limit_usd:           Optional[float] = None
    alpaca_max_consecutive_losses:         Optional[int]   = None


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_alpaca_status(
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Key config status, live trading settings, and account info if keys are valid."""
    secret_status = get_alpaca_secret_status()
    config = get_or_create_app_config(db)

    account_info: Optional[Dict[str, Any]] = None
    if secret_status.get("live", {}).get("configured"):
        try:
            broker = get_broker_from_keychain(mode="live")
            if broker:
                account_info = broker.get_account()
        except Exception as exc:
            account_info = {"error": str(exc)}
    elif secret_status.get("paper", {}).get("configured"):
        try:
            broker = get_broker_from_keychain(mode="paper")
            if broker:
                account_info = broker.get_account()
        except Exception as exc:
            account_info = {"error": str(exc)}

    return {
        "secrets":                            secret_status,
        "execution_mode":                     str(getattr(config, "alpaca_execution_mode", "off") or "off"),
        "live_trading_enabled":               bool(getattr(config, "alpaca_live_trading_enabled",   False)),
        "allow_short_selling":                bool(getattr(config, "alpaca_allow_short_selling",    False)),
        "paper_trade_amount_usd":             getattr(config, "alpaca_paper_trade_amount_usd",      None),
        "live_trade_amount_usd":              getattr(config, "alpaca_live_trade_amount_usd",       None),
        "max_position_usd":                   getattr(config, "alpaca_max_position_usd",            None),
        "max_total_exposure_usd":             getattr(config, "alpaca_max_total_exposure_usd",      None),
        "order_type":                         str(getattr(config,  "alpaca_order_type",             "market") or "market"),
        "limit_slippage_pct":                 float(getattr(config, "alpaca_limit_slippage_pct",    0.002) or 0.002),
        "daily_loss_limit_usd":               getattr(config, "alpaca_daily_loss_limit_usd",        None),
        "max_consecutive_losses":             getattr(config, "alpaca_max_consecutive_losses",      3),
        "account":                            account_info,
    }


# ── Secrets ───────────────────────────────────────────────────────────────────

@router.post("/secrets")
async def save_alpaca_keys(
    payload: AlpacaSecretsPayload,
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Store Alpaca API key + secret in the OS keychain."""
    try:
        result = save_alpaca_secrets(payload.api_key, payload.secret_key, payload.trading_mode)
        return {"ok": True, "status": result}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/secrets")
async def clear_alpaca_keys(
    mode: Optional[str] = Query(default=None, pattern="^(paper|live)$"),
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Remove Alpaca API keys from the OS keychain. Pass ?mode=paper or ?mode=live to clear only one slot."""
    result = clear_alpaca_secrets(mode=mode)
    return {"ok": True, "status": result}


# ── Connection test ───────────────────────────────────────────────────────────

@router.post("/test-connection")
async def test_alpaca_connection(
    mode: Optional[str] = Query(default=None, pattern="^(paper|live)$"),
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Validate stored keys by calling GET /v2/account on Alpaca. Pass ?mode=paper|live to test a specific slot."""
    broker = get_broker_from_keychain(mode=mode)
    if broker is None:
        slot = f"{mode} " if mode else ""
        raise HTTPException(status_code=400, detail=f"Alpaca {slot}API keys not configured")
    try:
        account = broker.get_account()
        return {"ok": True, "mode": broker.mode, "account": account}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Alpaca connection failed: {exc}")


# ── Live account / positions ──────────────────────────────────────────────────

@router.get("/account")
async def get_alpaca_account(
    mode: Optional[str] = Query(default=None, pattern="^(paper|live)$"),
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    broker = get_broker_from_keychain(mode=mode)
    if broker is None:
        slot = f" for {mode}" if mode else ""
        raise HTTPException(status_code=400, detail=f"Alpaca API keys not configured{slot}")
    try:
        account = broker.get_account()
        if isinstance(account, dict):
            account["trading_mode"] = broker.mode
        return account
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/positions")
async def get_alpaca_positions(
    mode: Optional[str] = Query(default=None, pattern="^(paper|live)$"),
    _admin: None = Depends(require_admin_token),
) -> List[Dict[str, Any]]:
    broker = get_broker_from_keychain(mode=mode)
    if broker is None:
        slot = f" for {mode}" if mode else ""
        raise HTTPException(status_code=400, detail=f"Alpaca API keys not configured{slot}")
    try:
        positions = broker.get_positions()
        for position in positions:
            if isinstance(position, dict):
                position["trading_mode"] = broker.mode
        return positions
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/positions/{symbol}/close")
async def close_alpaca_position(
    symbol: str,
    mode: Optional[str] = Query(default=None, pattern="^(paper|live)$"),
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Close an open Alpaca position for the given symbol."""
    broker = get_broker_from_keychain(mode=mode)
    if broker is None:
        slot = f" for {mode}" if mode else ""
        raise HTTPException(status_code=400, detail=f"Alpaca API keys not configured{slot}")
    try:
        result = broker.close_position(symbol)
        if isinstance(result, dict):
            result["trading_mode"] = broker.mode
        return {"ok": True, "result": result}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ── Order log ─────────────────────────────────────────────────────────────────

@router.get("/orders")
async def get_alpaca_orders(
    limit: int = 50,
    mode: Optional[str] = Query(default=None, pattern="^(paper|live)$"),
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Return recent AlpacaOrder rows from our DB (newest first)."""
    from database.models import AlpacaOrder

    query = db.query(AlpacaOrder)
    if mode:
        query = query.filter(AlpacaOrder.trading_mode == mode)
    rows = query.order_by(AlpacaOrder.created_at.desc()).limit(min(limit, 200)).all()
    return [
        {
            "id":               o.id,
            "paper_trade_id":   o.paper_trade_id,
            "alpaca_order_id":  o.alpaca_order_id,
            "symbol":           o.symbol,
            "side":             o.side,
            "notional":         o.notional,
            "qty":              o.qty,
            "order_type":       o.order_type,
            "limit_price":      o.limit_price,
            "extended_hours":   o.extended_hours,
            "status":           o.status,
            "filled_qty":       o.filled_qty,
            "filled_avg_price": o.filled_avg_price,
            "trading_mode":     o.trading_mode,
            "error_message":    o.error_message,
            "submitted_at":     o.submitted_at.isoformat() if o.submitted_at else None,
            "filled_at":        o.filled_at.isoformat()    if o.filled_at    else None,
            "created_at":       o.created_at.isoformat()   if o.created_at   else None,
        }
        for o in rows
    ]


@router.get("/orphans")
async def get_orphan_orders(
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Return open AlpacaOrder rows flagged as orphans that have not been acknowledged."""
    from database.models import AlpacaOrder

    rows = (
        db.query(AlpacaOrder)
        .filter(AlpacaOrder.is_orphan == True, AlpacaOrder.orphan_acknowledged == False)  # noqa: E712
        .order_by(AlpacaOrder.created_at.desc())
        .all()
    )
    return [
        {
            "id":              o.id,
            "symbol":          o.symbol,
            "side":            o.side,
            "status":          o.status,
            "trading_mode":    o.trading_mode,
            "alpaca_order_id": o.alpaca_order_id,
            "created_at":      o.created_at.isoformat() if o.created_at else None,
        }
        for o in rows
    ]


@router.post("/orphans/{order_id}/acknowledge")
async def acknowledge_orphan(
    order_id: int,
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Mark an orphan order as acknowledged so it is no longer surfaced."""
    from database.models import AlpacaOrder

    order = db.query(AlpacaOrder).filter(AlpacaOrder.id == order_id).first()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    order.orphan_acknowledged = True
    db.commit()
    return {"ok": True, "id": order_id}


@router.post("/poll-orders")
async def poll_orders(
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Manually trigger a fill-status poll for all pending Alpaca orders."""
    updated = poll_unfilled_orders(db)
    return {"ok": True, "updated_count": updated}


# ── Settings ──────────────────────────────────────────────────────────────────

@router.put("/settings")
async def update_alpaca_settings(
    payload: AlpacaSettingsPayload,
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Update Alpaca-related AppConfig fields (guards, limits, kill switch)."""
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    config = update_app_config(db, data)
    return {
        "ok":                        True,
        "execution_mode":            str(getattr(config, "alpaca_execution_mode", "off") or "off"),
        "live_trading_enabled":      bool(getattr(config, "alpaca_live_trading_enabled",   False)),
        "allow_short_selling":       bool(getattr(config, "alpaca_allow_short_selling",    False)),
        "paper_trade_amount_usd":    getattr(config, "alpaca_paper_trade_amount_usd",      None),
        "live_trade_amount_usd":     getattr(config, "alpaca_live_trade_amount_usd",       None),
        "max_position_usd":          getattr(config, "alpaca_max_position_usd",            None),
        "max_total_exposure_usd":    getattr(config, "alpaca_max_total_exposure_usd",      None),
        "order_type":                str(getattr(config,  "alpaca_order_type",             "market") or "market"),
        "limit_slippage_pct":        float(getattr(config, "alpaca_limit_slippage_pct",    0.002) or 0.002),
        "daily_loss_limit_usd":      getattr(config, "alpaca_daily_loss_limit_usd",        None),
        "max_consecutive_losses":    getattr(config, "alpaca_max_consecutive_losses",      3),
    }


# ── Cancel all orders ─────────────────────────────────────────────────────────

@router.post("/cancel-all-orders")
async def cancel_all_orders(
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Cancel every open order on Alpaca. Wired to the circuit-breaker kill switch."""
    broker = get_broker_from_keychain()
    if broker is None:
        raise HTTPException(status_code=400, detail="Alpaca API keys not configured")
    try:
        cancelled = broker.cancel_all_orders()
        return {"ok": True, "cancelled_count": len(cancelled), "orders": cancelled}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ── Account configurations ────────────────────────────────────────────────────

@router.get("/account/configurations")
async def get_account_configurations(
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Return Alpaca account-level settings (e.g. shorting_enabled)."""
    broker = get_broker_from_keychain()
    if broker is None:
        raise HTTPException(status_code=400, detail="Alpaca API keys not configured")
    try:
        return broker.get_account_configurations()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ── Portfolio history ─────────────────────────────────────────────────────────

@router.get("/portfolio-history")
async def get_portfolio_history(
    period: str = "1M",
    timeframe: str = "1D",
    extended_hours: bool = False,
    mode: Optional[str] = Query(default=None, pattern="^(paper|live)$"),
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Return Alpaca account equity curve for the given period/timeframe."""
    broker = get_broker_from_keychain(mode=mode)
    if broker is None:
        slot = f" for {mode}" if mode else ""
        raise HTTPException(status_code=400, detail=f"Alpaca API keys not configured{slot}")
    try:
        history = broker.get_portfolio_history(period=period, timeframe=timeframe, extended_hours=extended_hours)
        if isinstance(history, dict):
            history["trading_mode"] = broker.mode
        return history
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ── Account activities ────────────────────────────────────────────────────────

@router.get("/activities")
async def get_account_activities(
    activity_type: Optional[str] = None,
    limit: int = 100,
    mode: Optional[str] = Query(default=None, pattern="^(paper|live)$"),
    _admin: None = Depends(require_admin_token),
) -> List[Dict[str, Any]]:
    """Return Alpaca account activities (fills, fees, dividends, etc.)."""
    broker = get_broker_from_keychain(mode=mode)
    if broker is None:
        slot = f" for {mode}" if mode else ""
        raise HTTPException(status_code=400, detail=f"Alpaca API keys not configured{slot}")
    try:
        activities = broker.get_account_activities(activity_type=activity_type, limit=min(limit, 500))
        for activity in activities:
            if isinstance(activity, dict):
                activity["trading_mode"] = broker.mode
        return activities
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ── Live summary (computed server-side from Alpaca API) ───────────────────────

@router.get("/live-summary")
async def get_alpaca_live_summary(
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Compute live trading summary from Alpaca's API.
    Falls back to the alpaca_orders DB table when the Alpaca activities API is unavailable.
    
    Returns:
      - account: account details (equity, cash, buying_power, unrealized_pl)
      - positions: open positions with unrealized P&L
      - realized_pnl: total realized P&L from completed round-trips
      - win_count / loss_count / win_rate: computed from fill history or DB
      - total_trades: number of completed round-trips
      - closed_trades: list of completed round-trip details
    """
    broker = get_broker_from_keychain(mode="live")
    if broker is None:
        raise HTTPException(status_code=400, detail="Alpaca live API keys not configured")

    def _to_float(val, default=0.0):
        try:
            return float(val) if val is not None else default
        except (TypeError, ValueError):
            return default

    try:
        # 1. Fetch account
        account = broker.get_account()
        if not isinstance(account, dict):
            account = {}

        equity = _to_float(account.get("equity"))
        cash = _to_float(account.get("cash"))
        buying_power = _to_float(account.get("buying_power"))
        unrealized_pl = _to_float(account.get("unrealized_pl"))
        last_equity = _to_float(account.get("last_equity"))
        daytrade_count = _to_float(account.get("daytrade_count"))
        pattern_day_trader = str(account.get("pattern_day_trader", "")).lower() in {"true", "1", "yes"}
        trading_blocked = str(account.get("trading_blocked", "")).lower() in {"true", "1", "yes"}
    except Exception as exc:
        print(f"[alpaca] live-summary: account fetch failed: {exc}")
        raise HTTPException(status_code=502, detail=f"Cannot fetch Alpaca account: {exc}")

    # 2. Fetch open positions
    try:
        positions = broker.get_positions()
        if not isinstance(positions, list):
            positions = []
    except Exception as exc:
        print(f"[alpaca] live-summary: positions fetch failed: {exc}")
        positions = []

    open_positions_list = []
    computed_unrealized_pnl = 0.0
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        qty = _to_float(pos.get("qty"))
        if qty == 0:
            continue
        avg_entry = _to_float(pos.get("avg_entry_price"))
        current = _to_float(pos.get("current_price"))
        side = str(pos.get("side", "long")).lower()
        # Compute unrealized P&L manually since Alpaca's account-level field may be stale
        if avg_entry > 0 and current > 0 and qty > 0:
            if side == "short":
                pos_pnl = (avg_entry - current) * qty
            else:
                pos_pnl = (current - avg_entry) * qty
        else:
            pos_pnl = _to_float(pos.get("unrealized_pnl"))
        computed_unrealized_pnl += pos_pnl
        open_positions_list.append({
            "symbol": pos.get("symbol", ""),
            "qty": qty,
            "avg_entry_price": avg_entry,
            "current_price": current,
            "market_value": _to_float(pos.get("market_value")),
            "unrealized_pnl": round(pos_pnl, 4),
            "unrealized_plpc": _to_float(pos.get("unrealized_plpc")),
            "side": side,
        })

    # 3. Try to get fill activities from Alpaca API (authoritative source)
    activities: list = []
    try:
        activities = broker.get_account_activities(activity_type="FILL", limit=500)
        if not isinstance(activities, list):
            activities = []
    except Exception as exc:
        print(f"[alpaca] live-summary: activities API failed, falling back to DB: {exc}")
        activities = []

    realized_pnl = 0.0
    wins = 0
    losses = 0
    closed_trades_list = []

    if activities:
        # Compute realized P&L from fill activities (Alpaca API)
        fills_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
        for act in activities:
            if not isinstance(act, dict):
                continue
            symbol = str(act.get("symbol", "") or "").upper().strip()
            if not symbol:
                continue
            side = str(act.get("side", "") or "").lower().strip()
            if side not in ("buy", "sell"):
                continue
            fills_by_symbol.setdefault(symbol, []).append({
                "side": side,
                "qty": _to_float(act.get("qty")),
                "price": _to_float(act.get("price")),
                "transaction_time": act.get("transaction_time", ""),
            })

        for symbol in fills_by_symbol:
            fills_by_symbol[symbol].sort(key=lambda f: f["transaction_time"])

        for symbol, fills in fills_by_symbol.items():
            buys = [f for f in fills if f["side"] == "buy"]
            sells = [f for f in fills if f["side"] == "sell"]

            buy_idx = 0
            sell_idx = 0
            while buy_idx < len(buys) and sell_idx < len(sells):
                buy = buys[buy_idx]
                sell = sells[sell_idx]
                matched_qty = min(buy["qty"], sell["qty"])
                if matched_qty <= 0:
                    if buy["qty"] <= 0:
                        buy_idx += 1
                    if sell["qty"] <= 0:
                        sell_idx += 1
                    continue

                pnl = (sell["price"] - buy["price"]) * matched_qty
                realized_pnl += pnl
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1

                closed_trades_list.append({
                    "symbol": symbol,
                    "buy_price": buy["price"],
                    "sell_price": sell["price"],
                    "qty": matched_qty,
                    "pnl": round(pnl, 4),
                    "closed_at": sell["transaction_time"],
                })

                buys[buy_idx]["qty"] -= matched_qty
                sells[sell_idx]["qty"] -= matched_qty

                if buys[buy_idx]["qty"] <= 0:
                    buy_idx += 1
                if sells[sell_idx]["qty"] <= 0:
                    sell_idx += 1
    else:
        # Fallback: compute realized P&L from alpaca_orders table
        try:
            from database.models import AlpacaOrder
            from datetime import timezone

            live_orders = (
                db.query(AlpacaOrder)
                .filter(
                    AlpacaOrder.trading_mode == "live",
                    AlpacaOrder.status == "filled",
                    AlpacaOrder.filled_avg_price.isnot(None),
                    AlpacaOrder.filled_qty.isnot(None),
                )
                .all()
            )

            by_trade: Dict[int, List] = {}
            for o in live_orders:
                ptid = o.paper_trade_id
                if ptid is None:
                    continue
                by_trade.setdefault(ptid, []).append(o)

            _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
            for trade_orders in by_trade.values():
                buys = [o for o in trade_orders if o.side == "buy"]
                sells = [o for o in trade_orders if o.side == "sell"]
                if not buys or not sells:
                    continue
                buy = max(buys, key=lambda o: o.filled_at or _epoch)
                sell = max(sells, key=lambda o: o.filled_at or _epoch)
                if not buy.filled_avg_price or not sell.filled_avg_price:
                    continue
                qty = min(_to_float(buy.filled_qty), _to_float(sell.filled_qty))
                if qty <= 0:
                    continue
                pnl = (float(sell.filled_avg_price) - float(buy.filled_avg_price)) * qty
                realized_pnl += pnl
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                closed_at_str = sell.filled_at.isoformat() if sell.filled_at else ""
                closed_trades_list.append({
                    "symbol": buy.symbol,
                    "buy_price": float(buy.filled_avg_price),
                    "sell_price": float(sell.filled_avg_price),
                    "qty": qty,
                    "pnl": round(pnl, 4),
                    "closed_at": closed_at_str,
                })
        except Exception as exc:
            print(f"[alpaca] live-summary: DB fallback failed: {exc}")

    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

    # Sort closed trades newest first
    closed_trades_list.sort(key=lambda t: t["closed_at"], reverse=True)

    return {
        "account": {
            "equity": equity,
            "cash": cash,
            "buying_power": buying_power,
            "unrealized_pl": unrealized_pl,
            "last_equity": last_equity,
            "daytrade_count": daytrade_count,
            "pattern_day_trader": pattern_day_trader,
            "trading_blocked": trading_blocked,
        },
        "positions": open_positions_list,
        "realized_pnl": round(realized_pnl, 4),
        "unrealized_pnl": round(computed_unrealized_pnl, 4),
        "win_count": wins,
        "loss_count": losses,
        "win_rate": round(win_rate, 1),
        "total_trades": total_trades,
        "closed_trades": closed_trades_list,
    }


# ── Paginated closed trades (older than 4 days) ────────────────────────────────

@router.get("/closed-trades")
async def get_paginated_alpaca_closed_trades(
    offset: int = Query(default=0, ge=0, description="Number of trades to skip"),
    limit: int = Query(default=20, ge=1, le=100, description="Max trades to return"),
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    """Return older Alpaca closed trades for pagination. Trades older than those returned by /live-summary."""
    from database.models import AlpacaOrder
    from datetime import timedelta, timezone

    now_utc = datetime.now(timezone.utc)
    cutoff_date = now_utc - timedelta(days=4)
    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    # Fetch all filled live orders older than 4 days
    all_old_orders = (
        db.query(AlpacaOrder)
        .filter(
            AlpacaOrder.trading_mode == "live",
            AlpacaOrder.status == "filled",
            AlpacaOrder.filled_avg_price.isnot(None),
            AlpacaOrder.filled_qty.isnot(None),
            AlpacaOrder.filled_at.isnot(None),
            AlpacaOrder.filled_at < cutoff_date,
        )
        .order_by(AlpacaOrder.filled_at.desc())
        .all()
    )

    # Group by paper_trade_id to match buy/sell for round-trip P&L
    by_trade: Dict[int, List] = {}
    for o in all_old_orders:
        ptid = o.paper_trade_id
        if ptid is not None:
            by_trade.setdefault(ptid, []).append(o)

    # Count completed round-trips (must have both buy and sell)
    round_trip_count = sum(1 for orders in by_trade.values() if any(o.side == "buy" for o in orders) and any(o.side == "sell" for o in orders))

    # Build closed trades list
    closed_trades = []
    for trade_orders in by_trade.values():
        buys = [o for o in trade_orders if o.side == "buy"]
        sells = [o for o in trade_orders if o.side == "sell"]
        if not buys or not sells:
            continue
        buy = max(buys, key=lambda o: o.filled_at or _epoch)
        sell = max(sells, key=lambda o: o.filled_at or _epoch)
        if not buy.filled_avg_price or not sell.filled_avg_price:
            continue
        qty = min(float(buy.filled_qty or 0), float(sell.filled_qty or 0))
        if qty <= 0:
            continue
        pnl = (float(sell.filled_avg_price) - float(buy.filled_avg_price)) * qty
        closed_trades.append({
            "id": buy.id,
            "symbol": buy.symbol,
            "buy_price": float(buy.filled_avg_price),
            "sell_price": float(sell.filled_avg_price),
            "qty": qty,
            "pnl": round(pnl, 4),
            "closed_at": sell.filled_at.isoformat() if sell.filled_at else None,
        })

    # Sort newest first
    closed_trades.sort(key=lambda t: t["closed_at"] or "", reverse=True)

    return {
        "closed_trades": closed_trades[offset:offset + limit],
        "total_available": round_trip_count,
        "offset": offset,
        "limit": limit,
    }


@router.get("/dispatch-errors")
async def get_dispatch_errors(
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    """Return unacknowledged Alpaca dispatch errors (e.g. close-before-open failures)."""
    from database.models import AlpacaDispatchError

    errors = (
        db.query(AlpacaDispatchError)
        .filter(AlpacaDispatchError.acknowledged == False)
        .order_by(AlpacaDispatchError.created_at.desc())
        .all()
    )
    return [
        {
            "id": e.id,
            "symbol": e.symbol,
            "underlying": e.underlying,
            "error_type": e.error_type,
            "error_message": e.error_message,
            "trading_mode": e.trading_mode,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in errors
    ]


@router.post("/dispatch-errors/{error_id}/acknowledge")
async def acknowledge_dispatch_error(
    error_id: int,
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    """Acknowledge (dismiss) a dispatch error so it no longer appears on the trading page."""
    from database.models import AlpacaDispatchError

    error = db.query(AlpacaDispatchError).filter(AlpacaDispatchError.id == error_id).first()
    if error:
        error.acknowledged = True
        db.commit()
        return {"acknowledged": True}
    return {"acknowledged": False, "error": "not found"}
