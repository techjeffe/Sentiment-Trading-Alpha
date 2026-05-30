"""
API endpoints for PDT (Pattern Day Trader) status and queue management.

Endpoints:
- GET  /api/pdt/status          — current PDT status and config
- GET  /api/pdt/queue           — list pending queued orders
- POST /api/pdt/queue/replay    — manually trigger queue replay
- DELETE /api/pdt/queue/clear   — clear all queued orders
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from database.engine import SessionLocal
from database.models import AppConfig, PdtpendingOrder
from services.app_config import get_or_create_app_config

router = APIRouter(prefix="/api/pdt", tags=["PDT Queue"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class PdtpendingOrderResponse(BaseModel):
    id: int
    paper_trade_id: Optional[int]
    symbol: str
    side: str
    notional: Optional[float]
    qty: Optional[float]
    order_type: str
    status: str
    original_conviction: Optional[str]
    original_trading_type: Optional[str]
    downgraded_to_trading_type: Optional[str]
    queued_at: datetime
    replayed_at: Optional[datetime]
    error_message: Optional[str]


class PdtpendingQueueResponse(BaseModel):
    total: int
    queued: int
    replayed: int
    failed: int
    orders: List[PdtpendingOrderResponse]


class PdtpendingStatusResponse(BaseModel):
    queue_enabled: bool
    downgrade_enabled: bool
    notify_on_limit: bool
    max_queue_size: int
    queue_total: int
    queue_queued: int
    queue_replayed: int
    queue_failed: int


class ReplayRequest(BaseModel):
    """Empty request — replay all queued orders."""
    pass


class ClearRequest(BaseModel):
    """Empty request — clear all queued orders."""
    pass


@router.get("/status", response_model=PdtpendingStatusResponse)
def get_pdt_status(db=Depends(_get_db)):
    """Get current PDT configuration and queue status."""
    config = get_or_create_app_config(db)

    # Count queue entries by status
    queue_total = db.query(PdtpendingOrder).count()
    queue_queued = db.query(PdtpendingOrder).filter(
        PdtpendingOrder.status == "queued"
    ).count()
    queue_replayed = db.query(PdtpendingOrder).filter(
        PdtpendingOrder.status == "replayed"
    ).count()
    queue_failed = db.query(PdtpendingOrder).filter(
        PdtpendingOrder.status == "failed"
    ).count()

    return PdtpendingStatusResponse(
        queue_enabled=bool(getattr(config, "alpaca_pdt_queue_enabled", True)),
        downgrade_enabled=bool(getattr(config, "alpaca_pdt_downgrade_swing_to_position", True)),
        notify_on_limit=bool(getattr(config, "alpaca_pdt_notify_on_limit", True)),
        max_queue_size=int(getattr(config, "alpaca_pdt_max_queue_size", 10)),
        queue_total=queue_total,
        queue_queued=queue_queued,
        queue_replayed=queue_replayed,
        queue_failed=queue_failed,
    )


@router.get("/queue", response_model=PdtpendingQueueResponse)
def get_pdt_queue(limit: int = 50, offset: int = 0, db=Depends(_get_db)):
    """List PDT-queued orders."""
    query = db.query(PdtpendingOrder).order_by(
        PdtpendingOrder.queued_at.desc()
    )

    total = query.count()
    orders = query.offset(offset).limit(limit).all()

    queued = query.filter(PdtpendingOrder.status == "queued").count()
    replayed = query.filter(PdtpendingOrder.status == "replayed").count()
    failed = query.filter(PdtpendingOrder.status == "failed").count()

    return PdtpendingQueueResponse(
        total=total,
        queued=queued,
        replayed=replayed,
        failed=failed,
        orders=[
            PdtpendingOrderResponse(
                id=o.id,
                paper_trade_id=o.paper_trade_id,
                symbol=o.symbol,
                side=o.side,
                notional=o.notional,
                qty=o.qty,
                order_type=o.order_type,
                status=o.status,
                original_conviction=o.original_conviction,
                original_trading_type=o.original_trading_type,
                downgraded_to_trading_type=o.downgraded_to_trading_type,
                queued_at=o.queued_at,
                replayed_at=o.replayed_at,
                error_message=o.error_message,
            )
            for o in orders
        ],
    )


@router.post("/queue/replay")
def replay_pdt_queue(db=Depends(_get_db)):
    """Manually trigger replay of all queued PDT orders."""
    from datetime import timezone
    from services.keychain import get_alpaca_keychain

    queued = db.query(PdtpendingOrder).filter(
        PdtpendingOrder.status == "queued"
    ).order_by(PdtpendingOrder.queued_at.asc()).all()

    if not queued:
        return {"message": "No queued orders to replay", "replayed": 0, "failed": 0}

    broker = get_alpaca_keychain(mode="live")
    if broker is None:
        raise HTTPException(status_code=503, detail="Alpaca broker not configured")

    replayed_count = 0
    failed_count = 0

    for entry in queued:
        try:
            kwargs = {
                "symbol": entry.symbol,
                "notional": entry.notional,
                "order_type": entry.order_type or "market",
                "time_in_force": entry.time_in_force or "day",
                "limit_price": entry.limit_price,
                "extended_hours": entry.extended_hours,
            }

            if entry.side == "buy":
                result = broker.submit_buy_order(**kwargs)
            else:
                result = broker.submit_sell_order(**kwargs)

            if result and result.get("success"):
                entry.status = "replayed"
                entry.replayed_at = datetime.now(timezone.utc)
                entry.raw_response = result.get("response")
                replayed_count += 1
            else:
                entry.status = "failed"
                entry.error_message = result.get("error", "unknown") if result else "no response"
                entry.raw_response = result
                failed_count += 1

        except Exception as exc:
            entry.status = "failed"
            entry.error_message = str(exc)
            failed_count += 1

    db.commit()
    return {
        "message": f"Replay complete: {replayed_count} replayed, {failed_count} failed",
        "replayed": replayed_count,
        "failed": failed_count,
    }


@router.delete("/queue/clear")
def clear_pdt_queue(db=Depends(_get_db)):
    """Clear all queued PDT orders (keeps replayed/failed for audit)."""
    cleared = db.query(PdtpendingOrder).filter(
        PdtpendingOrder.status == "queued"
    ).delete()
    db.commit()
    return {"message": f"Cleared {cleared} queued order(s)"}
