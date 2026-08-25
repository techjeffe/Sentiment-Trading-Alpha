from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append(str(Path(__file__).resolve().parents[1]))

from config.logic_loader import LOGIC as _L
from database.models import AppConfig, Base, PaperTrade
from services.paper_trading import process_signals
from services.paper_trading import (
    _apply_flip_hold,
    _flip_should_early_close,
    _min_stop_loss_pct,
    _min_trade_size_usd,
)


@pytest.fixture()
def db_session(monkeypatch):
    import services.paper_trading as _pt
    _pt._cron_overlap_keys.clear()
    _pt._last_order_times.clear()

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _seed_config(db_session, **overrides):
    payload = dict(
        id=1,
        auto_run_enabled=True,
        auto_run_interval_minutes=30,
        tracked_symbols=["USO", "BITO", "QQQ", "SPY"],
        custom_symbols=[],
        max_posts=50,
        include_backtest=True,
        lookback_days=14,
        risk_profile="moderate",
        paper_trade_amount=1000.0,
    )
    payload.update(overrides)
    config = AppConfig(**payload)
    db_session.add(config)
    db_session.commit()
    return config


def _open_long(db_session, symbol="USO", amount=1000.0, entry=100.0):
    trade = PaperTrade(
        underlying=symbol,
        execution_ticker=symbol,
        signal_type="LONG",
        leverage="1x",
        market_session="open",
        amount=amount,
        shares=amount / entry,
        entry_price=entry,
        entered_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        holding_window_until=datetime.now(timezone.utc) + timedelta(hours=2),
        analysis_request_id="prev",
        trading_type="SWING",
        conviction_level="MEDIUM",
    )
    db_session.add(trade)
    db_session.commit()
    return trade


def _run_flip(db_session, conviction="MEDIUM", directional_score=0.3, price=101.0):
    from services import paper_trading as _pt

    original_market_status = _pt.market_status
    original_close_expired_positions = _pt.close_expired_positions
    _pt.market_status = lambda allow_extended_hours=True: {
        "status": "open",
        "label": "Market Open",
        "tradeable": True,
    }
    _pt.close_expired_positions = lambda db, alpaca_pending=None: []
    try:
        return process_signals(
            db=db_session,
            recommendations=[
                {
                    "underlying": "USO",
                    "execution_ticker": "SCO",
                    "signal_type": "SHORT",
                    "leverage": "1x",
                    "conviction_level": conviction,
                    "directional_score": directional_score,
                    "trading_type": "SWING",
                    "holding_minutes": 720,
                    "size_pct": "100.0",
                }
            ],
            quotes_by_symbol={
                "USO": {"current_price": price},
                "SCO": {"current_price": 10.0},
            },
            request_id="next",
            trade_amount=1000.0,
        )
    finally:
        _pt.market_status = original_market_status
        _pt.close_expired_positions = original_close_expired_positions


def _open_positions(db_session, exclude_id=None):
    q = db_session.query(PaperTrade).filter(PaperTrade.exited_at.is_(None))
    if exclude_id is not None:
        q = q.filter(PaperTrade.id != exclude_id)
    return q.all()


# ── unit: config helpers ──────────────────────────────────────────────


def test_min_trade_size_and_stop_loss_config_loaded():
    assert _min_trade_size_usd() == 50.0
    assert _min_stop_loss_pct() == 2.0


def test_flip_should_early_close_conviction_matrix():
    assert _flip_should_early_close("HIGH", {"directional_score": 0.3}) is True
    assert _flip_should_early_close("MEDIUM", {"directional_score": 0.3}) is False
    assert _flip_should_early_close("MEDIUM", {"directional_score": 0.7}) is True
    assert _flip_should_early_close("LOW", {"directional_score": 0.9}) is False


def test_apply_flip_hold_caps_window_and_resets_trailing():
    pos = PaperTrade()
    pos.signal_type = "LONG"
    pos.trading_type = "SWING"
    pos.entry_price = 100.0
    pos.amount = 1000.0
    pos.entered_at = datetime.now(timezone.utc) - timedelta(hours=4)
    pos.holding_window_until = datetime.now(timezone.utc) + timedelta(hours=10)
    pos.trailing_stop_price = 95.0
    pos.best_price_seen = 103.0
    now = datetime.now(timezone.utc)

    res = _apply_flip_hold(pos, "MEDIUM", "SWING", 720, now, _L)

    assert res["reason"] == "flip_hold_window_refreshed"
    # Capped at flip_hold_max_extension_minutes (240 min) from 'now'
    delta = res["holding_window_until"] - now
    assert delta <= timedelta(minutes=241)
    assert delta > timedelta(minutes=239)
    assert pos.trailing_stop_price is None
    assert pos.best_price_seen is None
    assert pos.conviction_level == "MEDIUM"


# ── integration: flip-hold behavior in process_signals ────────────────


def test_medium_flip_holds_position_and_refreshes_window(db_session):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    open_trade = _open_long(db_session)
    old_window = open_trade.holding_window_until

    actions = _run_flip(db_session, conviction="MEDIUM", directional_score=0.3, price=101.0)

    # Position stays open — no flip close, no opposite position opened.
    open_positions = _open_positions(db_session, exclude_id=open_trade.id)
    refreshed = db_session.query(PaperTrade).filter(PaperTrade.id == open_trade.id).first()

    assert refreshed.exited_at is None
    assert refreshed.close_reason is None
    assert len(open_positions) == 0  # only the original position, still open
    assert actions[0]["action"] == "held"
    assert actions[0]["reason"] == "flip_hold_window_refreshed"
    assert refreshed.holding_window_until > old_window


def test_low_flip_holds_position(db_session):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    open_trade = _open_long(db_session)

    actions = _run_flip(db_session, conviction="LOW", directional_score=0.9, price=101.0)

    refreshed = db_session.query(PaperTrade).filter(PaperTrade.id == open_trade.id).first()
    assert refreshed.exited_at is None
    assert actions[0]["action"] == "held"


def test_strong_medium_flip_still_early_closes(db_session):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    open_trade = _open_long(db_session)

    actions = _run_flip(db_session, conviction="MEDIUM", directional_score=0.7, price=101.0)

    closed = db_session.query(PaperTrade).filter(PaperTrade.id == open_trade.id).first()
    new_shorts = [p for p in _open_positions(db_session) if p.signal_type == "SHORT"]
    assert closed.exited_at is not None
    assert closed.close_reason == "direction_flip"
    assert len(new_shorts) == 1
    assert actions[0]["action"] == "opened"


def test_high_flip_still_early_closes(db_session):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    open_trade = _open_long(db_session)

    actions = _run_flip(db_session, conviction="HIGH", directional_score=0.3, price=101.0)

    closed = db_session.query(PaperTrade).filter(PaperTrade.id == open_trade.id).first()
    assert closed.exited_at is not None
    assert closed.close_reason == "direction_flip"
    assert actions[0]["action"] == "opened"


def test_flip_hold_still_respects_stop_loss(db_session):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    open_trade = _open_long(db_session)

    # Price drops 3% below entry (100 → 97): stop-loss (2%) must fire even
    # though the new signal is a MEDIUM conviction flip that would otherwise hold.
    actions = _run_flip(db_session, conviction="MEDIUM", directional_score=0.3, price=97.0)

    closed = db_session.query(PaperTrade).filter(PaperTrade.id == open_trade.id).first()
    assert closed.exited_at is not None
    assert closed.close_reason == "stop_loss_hit"
    assert actions[0]["action"] == "closed"
    assert actions[0]["reason"] == "stop_loss_hit"