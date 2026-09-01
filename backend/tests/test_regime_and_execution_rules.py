"""Tests for the 4-rule execution refinement: regime/leverage throttle,
overnight 3x de-risk, counter-trend cooldown, run-length protection,
IC-scaled sizing, and the dust/order safeguards."""

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
    _compute_vol_normalized_amount,
    _counter_trend_cooldown_blocks,
    _overnight_derisk_blocks,
    _new_entry_derisk_gate,
)
from services.regime import (
    classify_regime_from_indicators,
    classify_regime_from_prices,
    regime_leverage_cap,
)


# ── Fixtures (mirrors test_churn_guards conventions) ───────────────────────


class _FrozenClock(datetime):
    """Stand-in for paper_trading.datetime: now() returns a fixed time."""
    _frozen: datetime = datetime(2025, 6, 12, 19, 30, tzinfo=timezone.utc)  # 15:30 ET (EDT)

    @classmethod
    def now(cls, tz=None):
        t = cls._frozen
        return t.astimezone(tz) if tz else t


@pytest.fixture()
def db_session():
    import services.paper_trading as _pt
    _pt._cron_overlap_keys.clear()
    _pt._cron_overlap_underlying_keys.clear()
    _pt._last_order_times.clear()
    _pt._confirmation_streaks.clear()
    _pt._churn_blocked.clear()
    _pt.datetime = datetime  # ensure real clock unless a test freezes it

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def frozen_afternoon():
    """Patch paper_trading.datetime to a fixed 15:30 ET Thursday."""
    import services.paper_trading as _pt
    orig = _pt.datetime
    _FrozenClock._frozen = datetime(2025, 6, 12, 19, 30, tzinfo=timezone.utc)
    _pt.datetime = _FrozenClock
    yield
    _pt.datetime = orig


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


def _open_pos(db_session, symbol="USO", execution=None, signal="LONG", leverage="1x",
              entry=100.0, entered_min_ago=60, conviction="MEDIUM", trading_type="SWING"):
    t = PaperTrade(
        underlying=symbol, execution_ticker=execution or symbol, signal_type=signal,
        leverage=leverage, market_session="open", amount=1000.0,
        shares=1000.0 / entry, entry_price=entry,
        entered_at=_FrozenClock.now(timezone.utc) - timedelta(minutes=entered_min_ago),
        holding_window_until=_FrozenClock.now(timezone.utc) + timedelta(hours=2),
        analysis_request_id="prev", trading_type=trading_type, conviction_level=conviction,
    )
    db_session.add(t)
    db_session.commit()
    return t


def _stub_runtime():
    import services.paper_trading as _pt
    orig_ms, orig_cep = _pt.market_status, _pt.close_expired_positions
    _pt.market_status = lambda allow_extended_hours=True: {"status": "open", "label": "Market Open", "tradeable": True}
    _pt.close_expired_positions = lambda db, alpaca_pending=None: []
    return orig_ms, orig_cep


def _run(db_session, recommendations, quotes, trade_amount=1000.0):
    import services.paper_trading as _pt
    orig_ms, orig_cep = _stub_runtime()
    try:
        return process_signals(
            db=db_session, recommendations=recommendations, quotes_by_symbol=quotes,
            request_id="next", trade_amount=trade_amount,
        )
    finally:
        _pt.market_status, _pt.close_expired_positions = orig_ms, orig_cep


# ── 1. Regime classification & leverage throttling ─────────────────────────


def test_regime_choppy_when_ma_cluster_tight():
    ind = {"sma_50": 100.5, "sma_200": 100.1, "atr_14_pct": 1.2}
    assert classify_regime_from_indicators(ind) == "choppy"


def test_regime_trending_when_ma_spread_wide():
    ind = {"sma_50": 120.0, "sma_200": 90.0, "atr_14_pct": 1.2}
    assert classify_regime_from_indicators(ind) == "trending"


def test_regime_unknown_without_mas_fails_open():
    assert classify_regime_from_indicators({"atr_14_pct": 1.2}) == "unknown"
    assert classify_regime_from_indicators(None) == "unknown"
    assert classify_regime_from_indicators({}) == "unknown"


def test_regime_choppy_when_high_atr_no_trend():
    ind = {"sma_50": 100.5, "sma_200": 100.1, "atr_14_pct": 4.0}
    assert classify_regime_from_indicators(ind) == "choppy"


def test_regime_leverage_cap_throttles_3x_in_chop():
    assert regime_leverage_cap("choppy", 3) == 2
    assert regime_leverage_cap("trending", 3) == 3
    assert regime_leverage_cap("unknown", 3) == 3
    assert regime_leverage_cap("choppy", 1) == 1  # never upscale


def test_classify_from_prices_choppy_series():
    import numpy as np
    import pandas as pd
    idx = pd.date_range("2025-05-01", periods=240, freq="D")
    chop = pd.Series(100 + np.sin(np.linspace(0, 15, 240)) * 2, index=idx)
    assert classify_regime_from_prices(chop) == "choppy"


def test_classify_from_prices_trending_series():
    import numpy as np
    import pandas as pd
    idx = pd.date_range("2025-05-01", periods=240, freq="D")
    trend = pd.Series(np.linspace(100, 150, 240), index=idx)
    assert classify_regime_from_prices(trend) == "trending"


def test_analysis_router_imports_regime_helper():
    """Regression: rerun_analysis_snapshot calls market_regime_from_price_context;
    a missing import was a runtime NameError on every snapshot rerun."""
    import routers.analysis as ra
    from services.regime import market_regime_from_price_context
    assert ra.market_regime_from_price_context is market_regime_from_price_context


def test_parse_et_time_tolerates_legacy_bare_hour():
    """Regression: an old admin bug stored start_et as a bare number ("14");
    _parse_et_time must fall back instead of raising mid-run."""
    from services.regime import _parse_et_time
    from datetime import time as time_cls
    assert _parse_et_time("14") == time_cls(15, 0)  # malformed → default 15:00
    assert _parse_et_time("14:30") == time_cls(14, 30)
    assert _parse_et_time("") == time_cls(15, 0)
    assert _parse_et_time("bogus") == time_cls(15, 0)


def test_resolve_leverage_respects_regime_throttle():
    from services.analysis.signal_service import SignalService
    svc = SignalService(_L)
    # crazy profile normally allows 3x at atr 1.0
    assert svc._resolve_leverage(0.8, "crazy", action="BUY", atr_pct=1.0, regime="trending") == "3x"
    # choppy regime downshifts to 2x (QLD/SSO instead of TQQQ/SPXL)
    assert svc._resolve_leverage(0.8, "crazy", action="BUY", atr_pct=1.0, regime="choppy") == "2x"
    # conservative profile unaffected by regime
    assert svc._resolve_leverage(0.8, "conservative", action="SELL", atr_pct=1.0, regime="choppy") == "inverse"


# ── 2. Overnight de-risk for 3x ────────────────────────────────────────────


def test_overnight_derisk_closes_3x_after_start_time(db_session, frozen_afternoon):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    pos = _open_pos(db_session, symbol="QQQ", execution="TQQQ", signal="LONG", leverage="3x",
                    conviction="HIGH", entered_min_ago=120)
    actions = _run(db_session, [{
        "underlying": "QQQ", "execution_ticker": "TQQQ", "signal_type": "HOLD",
        "leverage": "3x", "conviction_level": "HIGH", "trading_type": "SWING",
        "holding_minutes": 720, "regime": "choppy", "ic_strong": False,
    }], {"TQQQ": {"current_price": 52.0}})

    assert any(a.get("reason") == "overnight_derisk_3x" for a in actions)
    db_session.refresh(pos)
    assert pos.exited_at is not None
    assert pos.close_reason == "overnight_derisk_3x"


def test_overnight_derisk_exempts_high_conviction_strong_ic(db_session, frozen_afternoon):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    pos = _open_pos(db_session, symbol="QQQ", execution="TQQQ", signal="LONG", leverage="3x",
                    conviction="HIGH", entered_min_ago=120)
    actions = _run(db_session, [{
        "underlying": "QQQ", "execution_ticker": "TQQQ", "signal_type": "HOLD",
        "leverage": "3x", "conviction_level": "HIGH", "trading_type": "SWING",
        "holding_minutes": 720, "regime": "choppy", "ic_strong": True,
    }], {"TQQQ": {"current_price": 52.0}})

    assert not any(a.get("reason") == "overnight_derisk_3x" for a in actions)
    db_session.refresh(pos)
    assert pos.exited_at is None


def test_overnight_derisk_blocks_new_3x_entry(db_session, frozen_afternoon):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    import services.paper_trading as _pt
    _now = _FrozenClock.now(timezone.utc)
    _pt._update_confirmation_streak("QQQ", "LONG", _now)
    _pt._update_confirmation_streak("QQQ", "LONG", _now)
    actions = _run(db_session, [{
        "underlying": "QQQ", "execution_ticker": "TQQQ", "signal_type": "LONG",
        "leverage": "3x", "conviction_level": "HIGH", "trading_type": "SWING",
        "holding_minutes": 720, "regime": "choppy", "ic_strong": False, "size_pct": "100.0",
    }], {"TQQQ": {"current_price": 52.0}})

    assert any(a.get("reason") == "overnight_derisk_no_entry" for a in actions)
    open_count = db_session.query(PaperTrade).filter(PaperTrade.exited_at.is_(None)).count()
    assert open_count == 0


def test_derisk_ignored_without_pipeline_context(db_session, frozen_afternoon):
    """Hand-built recs (tests, manual hooks) without regime/IC context never trigger."""
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    pos = _open_pos(db_session, symbol="QQQ", execution="TQQQ", signal="LONG", leverage="3x",
                    conviction="HIGH", entered_min_ago=120)
    actions = _run(db_session, [{
        "underlying": "QQQ", "execution_ticker": "TQQQ", "signal_type": "HOLD",
        "leverage": "3x", "conviction_level": "HIGH", "trading_type": "SWING",
        "holding_minutes": 720,
    }], {"TQQQ": {"current_price": 52.0}})

    assert not any(a.get("reason") == "overnight_derisk_3x" for a in actions)
    db_session.refresh(pos)
    assert pos.exited_at is None


# ── 3. Counter-trend cooldown ──────────────────────────────────────────────


def _close_seed(db_session, symbol, signal, reason, days_ago, exit_price, entry=100.0):
    t = PaperTrade(
        underlying=symbol, execution_ticker=symbol, signal_type=signal, leverage="1x",
        market_session="open", amount=1000.0, shares=10.0, entry_price=entry,
        entered_at=_FrozenClock.now(timezone.utc) - timedelta(days=days_ago + 1),
        exited_at=_FrozenClock.now(timezone.utc) - timedelta(days=days_ago),
        close_reason=reason, holding_window_until=None, analysis_request_id="old",
        trading_type="SWING", conviction_level="MEDIUM", exit_price=exit_price,
    )
    db_session.add(t)
    db_session.commit()
    return t


def test_two_consecutive_stopouts_block_reentry(db_session, frozen_afternoon):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    _close_seed(db_session, "USO", "LONG", "stop_loss_hit", days_ago=1, exit_price=96.0)
    _close_seed(db_session, "USO", "LONG", "stop_loss_hit", days_ago=3, exit_price=98.0)
    import services.paper_trading as _pt
    # Build the 3-consecutive-run confirmation streak the way production does
    # (2 pre-runs + this run = 3), so the counter-trend gate is what blocks.
    _now = _FrozenClock.now(timezone.utc)
    _pt._update_confirmation_streak("USO", "LONG", _now)
    _pt._update_confirmation_streak("USO", "LONG", _now)
    actions = _run(db_session, [{
        "underlying": "USO", "execution_ticker": "USO", "signal_type": "LONG",
        "leverage": "1x", "conviction_level": "HIGH", "trading_type": "SWING",
        "holding_minutes": 720, "size_pct": "100.0",
    }], {"USO": {"current_price": 101.0}})

    assert any(a.get("reason") == "counter_trend_cooldown" for a in actions)
    open_count = db_session.query(PaperTrade).filter(PaperTrade.exited_at.is_(None)).count()
    assert open_count == 0


def test_profitable_close_between_stopouts_resets_chain(db_session):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    _close_seed(db_session, "USO", "LONG", "take_profit_hit", days_ago=1, exit_price=104.0)
    _close_seed(db_session, "USO", "LONG", "stop_loss_hit", days_ago=3, exit_price=98.0)
    assert _counter_trend_cooldown_blocks("USO", "LONG", _FrozenClock.now(timezone.utc), db_session) is False


def test_cooldown_expires_after_72h(db_session):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    _close_seed(db_session, "USO", "LONG", "stop_loss_hit", days_ago=6, exit_price=96.0)
    _close_seed(db_session, "USO", "LONG", "stop_loss_hit", days_ago=8, exit_price=98.0)
    assert _counter_trend_cooldown_blocks("USO", "LONG", _FrozenClock.now(timezone.utc), db_session) is False


# ── 4. Run-length protection ───────────────────────────────────────────────


def test_high_conviction_single_stock_trails_on_take_profit(db_session):
    """A SHORT rec on a profitable NVDA LONG forces the SL/TP path (rec differs
    from position), where the take-profit breach converts to an ATR trail."""
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    pos = _open_pos(db_session, symbol="NVDA", execution="NVDA", signal="LONG", leverage="1x",
                    entry=100.0, conviction="HIGH", entered_min_ago=60)
    actions = _run(db_session, [{
        "underlying": "NVDA", "execution_ticker": "NVDA", "signal_type": "SHORT",
        "leverage": "1x", "conviction_level": "HIGH", "trading_type": "SWING",
        "holding_minutes": 720, "size_pct": "100.0",
    }], {"NVDA": {"current_price": 103.6}})  # +3.6% > take_profit 3.0

    assert any(a.get("reason") == "run_length_protection" for a in actions)
    db_session.refresh(pos)
    assert pos.exited_at is None
    assert pos.trailing_stop_price is not None
    assert pos.trailing_stop_price < 103.6  # stop sits below the current price


def test_run_length_skipped_for_non_high_conviction():
    """Pure-function check: MEDIUM conviction is never run-length protected."""
    from types import SimpleNamespace
    from services.paper_trading import _apply_run_length_trail
    pos = SimpleNamespace(
        conviction_level="MEDIUM", underlying="NVDA", signal_type="LONG",
        best_price_seen=100.0, entry_price=100.0, trailing_stop_price=None,
        holding_window_until=None, trading_type="SWING",
    )
    ok = _apply_run_length_trail(pos, 103.6, 0.0, 3.0, _FrozenClock.now(timezone.utc), _L)
    assert ok is False
    assert pos.trailing_stop_price is None


def test_etf_family_keeps_fixed_take_profit(db_session):
    """Leveraged/commodity ETF families (INSTRUMENT_SPECS) are NOT run-length protected."""
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    pos = _open_pos(db_session, symbol="USO", execution="USO", signal="LONG", leverage="1x",
                    entry=100.0, conviction="HIGH", entered_min_ago=60)
    actions = _run(db_session, [{
        "underlying": "USO", "execution_ticker": "USO", "signal_type": "SHORT",
        "leverage": "1x", "conviction_level": "HIGH", "trading_type": "SWING",
        "holding_minutes": 720, "size_pct": "100.0",
    }], {"USO": {"current_price": 103.6}})

    assert any(a.get("reason") == "take_profit_hit" for a in actions)
    db_session.refresh(pos)
    assert pos.exited_at is not None


# ── 5. IC-scaled sizing ────────────────────────────────────────────────────


def test_ic_positive_scales_position_up():
    base = _compute_vol_normalized_amount(1000.0, "MEDIUM", 1.0)          # 1000.0
    up = _compute_vol_normalized_amount(1000.0, "MEDIUM", 1.0, ic_score=0.3)   # ×1.15
    down = _compute_vol_normalized_amount(1000.0, "MEDIUM", 1.0, ic_score=-0.3)  # ×0.85
    assert up > base
    assert down < base
    assert abs(up - base * 1.15) < 1.0
    assert abs(down - base * 0.85) < 1.0


def test_ic_sizing_clamped_and_ignored_without_score():
    assert _compute_vol_normalized_amount(1000.0, "MEDIUM", 1.0, ic_score=5.0) == \
        _compute_vol_normalized_amount(1000.0, "MEDIUM", 1.0, ic_score=None) * 1.2
    assert _compute_vol_normalized_amount(1000.0, "MEDIUM", 1.0, ic_score=None) == \
        _compute_vol_normalized_amount(1000.0, "MEDIUM", 1.0)


# ── 6. Dust / order safeguards ─────────────────────────────────────────────


def test_portfolio_cap_shrink_below_dust_skips(db_session, frozen_afternoon):
    """Cap squeeze below the notional floor skips the entry (dust suppression)."""
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0,
                 vol_sizing_portfolio_cap_usd=30.0)
    import services.paper_trading as _pt
    _now = _FrozenClock.now(timezone.utc)
    _pt._update_confirmation_streak("USO", "LONG", _now)
    _pt._update_confirmation_streak("USO", "LONG", _now)
    actions = _run(db_session, [{
        "underlying": "USO", "execution_ticker": "USO", "signal_type": "LONG",
        "leverage": "1x", "conviction_level": "HIGH", "trading_type": "SWING",
        "holding_minutes": 720, "size_pct": "100.0",
    }], {"USO": {"current_price": 101.0}})

    assert any(a.get("reason") == "min_trade_size" for a in actions)
    open_count = db_session.query(PaperTrade).filter(PaperTrade.exited_at.is_(None)).count()
    assert open_count == 0


def test_dispatch_skips_dust_open(monkeypatch):
    """Broker dispatch drops opens whose notional can't clear min_trade_size_usd."""
    from types import SimpleNamespace
    from services import paper_trading as _pt
    _pt._last_order_times.clear()
    calls = []
    monkeypatch.setattr(
        "services.alpaca_broker.maybe_execute_alpaca_order",
        lambda db, trade, event, config: calls.append((trade, event)),
    )
    dust_open = SimpleNamespace(
        execution_ticker="USO", underlying="USO", signal_type="LONG", amount=10.0,
        shares=0.1, entry_price=100.0, id=999,
    )
    _pt._dispatch_alpaca_orders(None, [(dust_open, "open")], SimpleNamespace(alpaca_execution_mode="paper"))
    assert calls == []  # dust open never reached the broker