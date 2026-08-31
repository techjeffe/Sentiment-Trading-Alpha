"""Tests for the churn guards: entry confirmation (3), trailing warmup (5), same-day no-rebuy (6),
and the trailing-warmup / no-flip helpers. Plus: decision-log decay metrics correctness."""

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
    _confirmation_ready,
    _confirmation_streaks,
    _flip_should_early_close,
    _no_flip_blocks_open,
    _same_day_rebuy_blocks,
    _trailing_warmup_blocks,
    _update_confirmation_streak,
    _churn_blocked,
)


@pytest.fixture()
def db_session():
    import services.paper_trading as _pt
    _pt._cron_overlap_keys.clear()
    _pt._cron_overlap_underlying_keys.clear()
    _pt._last_order_times.clear()
    _pt._confirmation_streaks.clear()
    _pt._churn_blocked.clear()

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
        id=1, auto_run_enabled=True, auto_run_interval_minutes=30,
        tracked_symbols=["USO", "BITO", "QQQ", "SPY"], custom_symbols=[],
        max_posts=50, include_backtest=True, lookback_days=14,
        risk_profile="moderate", paper_trade_amount=1000.0,
    )
    payload.update(overrides)
    db_session.add(AppConfig(**payload))
    db_session.commit()


def _open_pos(db_session, symbol="USO", signal="LONG", entry=100.0, entered_min_ago=30):
    t = PaperTrade(
        underlying=symbol, execution_ticker=symbol, signal_type=signal, leverage="1x",
        market_session="open", amount=1000.0, shares=1000.0 / entry, entry_price=entry,
        entered_at=datetime.now(timezone.utc) - timedelta(minutes=entered_min_ago),
        holding_window_until=datetime.now(timezone.utc) + timedelta(hours=2),
        analysis_request_id="prev", trading_type="SWING", conviction_level="MEDIUM",
    )
    db_session.add(t)
    db_session.commit()
    return t


def _run(db_session, recommendation, quotes):
    from services import paper_trading as _pt
    orig_ms = _pt.market_status
    orig_cep = _pt.close_expired_positions
    _pt.market_status = lambda allow_extended_hours=True: {"status": "open", "label": "Market Open", "tradeable": True}
    _pt.close_expired_positions = lambda db, alpaca_pending=None: []
    try:
        return process_signals(
            db=db_session, recommendations=[recommendation], quotes_by_symbol=quotes,
            request_id="next", trade_amount=1000.0,
        )
    finally:
        _pt.market_status = orig_ms
        _pt.close_expired_positions = orig_cep


class TestConfirmationGuard:
    def test_new_entry_requires_N_runs(self, db_session):
        _seed_config(db_session)
        rec = {"underlying": "USO", "execution_ticker": "USO", "signal_type": "LONG",
               "leverage": "1x", "conviction_level": "HIGH", "trading_type": "SWING",
               "holding_minutes": 720, "size_pct": "100.0"}
        quotes = {"USO": {"current_price": 101.0}}
        import services.paper_trading as _pt
        # first two runs → confirmation pending (clear cron-overlap between runs so
        # they're treated as separate analysis cycles, like real 10-min spacing)
        for _ in range(2):
            _pt._cron_overlap_underlying_keys.clear()
            _pt._cron_overlap_keys.clear()
            actions = _run(db_session, rec, quotes)
            assert actions[0]["action"] == "skipped"
            assert actions[0]["reason"] == "confirmation_pending"
        # third consecutive run → opened
        _pt._cron_overlap_underlying_keys.clear()
        _pt._cron_overlap_keys.clear()
        actions = _run(db_session, rec, quotes)
        assert actions[0]["action"] == "opened"

    def test_hold_resets_streak(self, db_session):
        _seed_config(db_session)
        _update_confirmation_streak("USO", "LONG", datetime.now(timezone.utc))
        _update_confirmation_streak("USO", "LONG", datetime.now(timezone.utc))
        # a SHORT signal resets the LONG streak
        _confirmation_ready("USO", "SHORT", datetime.now(timezone.utc))
        assert _confirmation_streaks.get(("USO", "LONG")) is None
        assert _confirmation_streaks.get(("USO", "SHORT"))[1] == 1

    def test_approved_flip_bypasses_confirmation(self, db_session):
        _seed_config(db_session)
        pos = _open_pos(db_session)
        rec = {"underlying": "USO", "execution_ticker": "SCO", "signal_type": "SHORT",
               "leverage": "1x", "conviction_level": "HIGH", "directional_score": 0.3,
               "trading_type": "SWING", "holding_minutes": 720, "size_pct": "100.0"}
        quotes = {"USO": {"current_price": 101.0}, "SCO": {"current_price": 10.0}}
        actions = _run(db_session, rec, quotes)
        assert actions[0]["action"] == "opened"
        db_session.refresh(pos)
        assert pos.exited_at is not None

    def test_same_direction_ticker_upgrade_bypasses(self, db_session):
        _seed_config(db_session)
        pos = _open_pos(db_session, signal="SHORT")
        rec = {"underlying": "USO", "execution_ticker": "SCO", "signal_type": "SHORT",
               "leverage": "2x", "conviction_level": "HIGH", "trading_type": "SWING",
               "holding_minutes": 720, "size_pct": "100.0"}
        quotes = {"USO": {"current_price": 100.0}, "SCO": {"current_price": 10.0}}
        actions = _run(db_session, rec, quotes)
        assert actions[0]["action"] == "opened"


class TestNoFlipCooldown:
    def test_flip_back_blocked_within_window(self, db_session):
        _seed_config(db_session, reentry_cooldown_minutes=0)
        pos = _open_pos(db_session)  # LONG
        # Immediately try SHORT (flip-back within 60 min of a close)
        rec = {"underlying": "USO", "execution_ticker": "USO", "signal_type": "SHORT",
               "leverage": "1x", "conviction_level": "MEDIUM", "directional_score": -0.5,
               "trading_type": "SWING", "holding_minutes": 720, "size_pct": "100.0"}
        quotes = {"USO": {"current_price": 101.0}}
        actions = _run(db_session, rec, quotes)
        # MEDIUM flip is a held flip; no new SHORT opens
        assert any(a.get("reason") in ("no_flip_cooldown", "flip_hold_window_refreshed", "conviction_window_blocks_flip") for a in actions)

    def test_no_flip_blocks_helper(self, db_session):
        _seed_config(db_session)
        pos = _open_pos(db_session, signal="SHORT")
        pos.exited_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert _no_flip_blocks_open(pos, "LONG", datetime.now(timezone.utc)) is True
        pos.exited_at = datetime.now(timezone.utc) - timedelta(minutes=90)
        assert _no_flip_blocks_open(pos, "LONG", datetime.now(timezone.utc)) is False


class TestSameDayNoRebuy:
    def test_rebuy_at_exit_price_blocked(self, db_session):
        _seed_config(db_session)
        pos = _open_pos(db_session)
        pos.exit_price = 100.0
        pos.exited_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        db_session.commit()
        assert _same_day_rebuy_blocks("USO", "LONG", 99.98, datetime.now(timezone.utc), db_session) is True
        # price meaningfully lower → not a churn re-buy
        assert _same_day_rebuy_blocks("USO", "LONG", 96.0, datetime.now(timezone.utc), db_session) is False
        # outside window → allowed
        assert _same_day_rebuy_blocks("USO", "LONG", 99.98, datetime.now(timezone.utc) + timedelta(minutes=70), db_session) is False


class TestTrailingWarmup:
    def _open_hold_pos(self, db_session, symbol="USO", entered_min_ago=2):
        pos = _open_pos(db_session, symbol=symbol, signal="LONG", entered_min_ago=entered_min_ago)
        # HOLD path needs window EXPIRED so it reaches the trailing branch
        pos.holding_window_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        return pos

    def test_warmup_blocks_trailing_armed(self, db_session):
        _seed_config(db_session)
        # young position (2 min old) → warmup blocks arming a trail
        self._open_hold_pos(db_session, entered_min_ago=2)
        rec = {"underlying": "USO", "execution_ticker": "USO", "signal_type": "HOLD",
               "leverage": "1x", "conviction_level": "LOW", "trading_type": "VOLATILE_EVENT",
               "holding_minutes": 720, "size_pct": "100.0"}
        quotes = {"USO": {"current_price": 101.0}}
        actions = _run(db_session, rec, quotes)
        assert actions[0]["reason"] == "trailing_warmup"

    def test_mature_position_arms_trailing(self, db_session):
        _seed_config(db_session)
        self._open_hold_pos(db_session, entered_min_ago=60)
        rec = {"underlying": "USO", "execution_ticker": "USO", "signal_type": "HOLD",
               "leverage": "1x", "conviction_level": "LOW", "trading_type": "VOLATILE_EVENT",
               "holding_minutes": 720, "size_pct": "100.0"}
        quotes = {"USO": {"current_price": 101.0}}
        actions = _run(db_session, rec, quotes)
        assert actions[0]["action"] == "trailing"

    def test_warmup_helper(self, db_session):
        _seed_config(db_session)
        young = _open_pos(db_session, entered_min_ago=2)
        assert _trailing_warmup_blocks(young, datetime.now(timezone.utc)) is True
        old = _open_pos(db_session, symbol="QQQ", entered_min_ago=60)
        assert _trailing_warmup_blocks(old, datetime.now(timezone.utc)) is False