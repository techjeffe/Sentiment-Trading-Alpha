from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.append(str(Path(__file__).resolve().parents[1]))

from config.logic_loader import LOGIC as _L
from database.models import AppConfig, Base, PaperTrade
from services.paper_trading import get_summary, process_signals


@pytest.fixture()
def db_session(monkeypatch):
    # Clear module-level cron overlap state so tests don't interfere with each other
    import services.paper_trading as _pt
    _pt._cron_overlap_keys.clear()
    _pt._last_order_times.clear()

    # Restore real PaperTrade class in case test_alpaca_broker patched it.
    # _resolve_underlying_conflicts imports PaperTrade at function-call time,
    # so we must patch both the module attribute AND the paper_trading module's
    # local namespace (if it cached the import).
    import database.models as _dm
    from database.models import PaperTrade as _RealPaperTrade
    _dm.PaperTrade = _RealPaperTrade

    # Also patch in paper_trading's namespace since it may have cached the import
    import services.paper_trading as _pt_mod
    _pt_mod.PaperTrade = _RealPaperTrade

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
        symbol_prompt_overrides={},
        symbol_company_aliases={},
        display_timezone="America/Chicago",
        enabled_rss_feeds=[],
        custom_rss_feeds=[],
        custom_rss_feed_labels={},
        rss_article_detail_mode="normal",
        rss_article_limits={"light": 5, "normal": 15, "detailed": 25},
        data_ingestion_interval_seconds=900,
        snapshot_retention_limit=12,
        extraction_model="",
        reasoning_model="",
        risk_profile="moderate",
        web_research_enabled=False,
        allow_extended_hours_trading=True,
        remote_snapshot_enabled=False,
        remote_snapshot_mode="telegram",
        remote_snapshot_min_pnl_change_usd=5.0,
        remote_snapshot_heartbeat_minutes=360,
        remote_snapshot_interval_minutes=360,
        remote_snapshot_send_on_position_change=True,
        remote_snapshot_include_closed_trades=False,
        remote_snapshot_max_recommendations=4,
        paper_trade_amount=1000.0,
    )
    payload.update(overrides)
    config = AppConfig(**payload)
    db_session.add(config)
    db_session.commit()
    return config


def test_process_signals_closes_existing_position_using_existing_ticker_price(db_session):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    open_trade = PaperTrade(
        underlying="SPY",
        execution_ticker="SPY",
        signal_type="SHORT",
        leverage="1x",
        market_session="open",
        amount=1000.0,
        shares=2.0,
        entry_price=500.0,
        entered_at=datetime.now(timezone.utc),
        analysis_request_id="prev",
    )
    db_session.add(open_trade)
    db_session.commit()

    from services import paper_trading as paper_trading_module

    original_market_status = paper_trading_module.market_status
    original_close_expired_positions = paper_trading_module.close_expired_positions
    paper_trading_module.market_status = lambda allow_extended_hours=True: {
        "status": "open",
        "label": "Market Open",
        "tradeable": True,
    }
    paper_trading_module.close_expired_positions = lambda db, alpaca_pending=None: []
    try:
        actions = process_signals(
            db=db_session,
            recommendations=[
                {
                    "underlying": "SPY",
                    "execution_ticker": "SPXS",
                    "signal_type": "SHORT",
                    "leverage": "3x",
                    "conviction_level": "HIGH",
                    "trading_type": "POSITION",
                    "holding_minutes": 180,
                }
            ],
            quotes_by_symbol={
                "SPY": {"current_price": 495.0},
                "SPXS": {"current_price": 12.0},
            },
            request_id="next",
            trade_amount=1000.0,
        )
    finally:
        paper_trading_module.market_status = original_market_status
        paper_trading_module.close_expired_positions = original_close_expired_positions

    closed_trade = (
        db_session.query(PaperTrade)
        .filter(PaperTrade.id == open_trade.id)
        .first()
    )
    new_trade = (
        db_session.query(PaperTrade)
        .filter(PaperTrade.underlying == "SPY", PaperTrade.exited_at.is_(None))
        .first()
    )

    assert closed_trade.exit_price == 495.0
    assert round(closed_trade.realized_pnl, 4) == 10.0
    assert new_trade is not None
    assert new_trade.execution_ticker == "SPXS"
    assert new_trade.entry_price == 12.0
    assert actions[0]["exit_price"] == 495.0


def test_get_summary_returns_configured_paper_trade_amount(db_session, monkeypatch):
    _seed_config(db_session, paper_trade_amount=1000.0)

    class DummyPriceClient:
        def get_realtime_quote(self, ticker):
            return {"current_price": 105.0}

    monkeypatch.setattr("services.data_ingestion.yfinance_client.PriceClient", DummyPriceClient)

    db_session.add(
        PaperTrade(
            underlying="QQQ",
            execution_ticker="TQQQ",
            signal_type="LONG",
            leverage="3x",
            market_session="open",
            amount=1000.0,
            shares=10.0,
            entry_price=100.0,
            entered_at=datetime.now(timezone.utc),
            analysis_request_id="req-1",
        )
    )
    db_session.commit()

    payload = get_summary(db_session)

    assert payload["paper_trade_amount"] == 1000.0
    assert payload["summary"]["total_deployed"] == 1000.0
    assert payload["open_positions"][0]["amount"] == 1000.0
    assert payload["open_positions"][0]["current_price"] == 105.0


def test_spy_short_leverage_upgrade_is_not_treated_as_direction_flip(db_session):
    _seed_config(db_session, paper_trade_amount=1000.0, reentry_cooldown_minutes=0)
    open_trade = PaperTrade(
        underlying="SPY",
        execution_ticker="SPY",
        signal_type="SHORT",
        leverage="1x",
        market_session="open",
        amount=1000.0,
        shares=2.0,
        entry_price=500.0,
        entered_at=datetime.now(timezone.utc),
        analysis_request_id="prev",
    )
    db_session.add(open_trade)
    db_session.commit()

    from services import paper_trading as paper_trading_module

    original_market_status = paper_trading_module.market_status
    original_close_expired_positions = paper_trading_module.close_expired_positions
    paper_trading_module.market_status = lambda allow_extended_hours=True: {
        "status": "open",
        "label": "Market Open",
        "tradeable": True,
    }
    paper_trading_module.close_expired_positions = lambda db, alpaca_pending=None: []
    try:
        actions = process_signals(
            db=db_session,
            recommendations=[
                {
                    "underlying": "SPY",
                    "execution_ticker": "SDS",
                    "signal_type": "SHORT",
                    "leverage": "2x",
                    "conviction_level": "HIGH",
                    "trading_type": "SWING",
                    "holding_minutes": 180,
                }
            ],
            quotes_by_symbol={
                "SPY": {"current_price": 495.0},
                "SDS": {"current_price": 20.0},
            },
            request_id="next",
            trade_amount=1000.0,
        )
    finally:
        paper_trading_module.market_status = original_market_status
        paper_trading_module.close_expired_positions = original_close_expired_positions

    new_trade = (
        db_session.query(PaperTrade)
        .filter(PaperTrade.underlying == "SPY", PaperTrade.exited_at.is_(None))
        .first()
    )
    closed_trade = (
        db_session.query(PaperTrade)
        .filter(PaperTrade.id == open_trade.id)
        .first()
    )
    assert closed_trade is not None
    assert closed_trade.close_reason == "ticker_leverage_change"
    assert new_trade is not None
    assert new_trade.signal_type == "SHORT"
    assert new_trade.execution_ticker == "SDS"


def test_min_same_day_exit_edge_does_not_block_direction_flip(db_session):
    _seed_config(
        db_session,
        paper_trade_amount=1000.0,
        reentry_cooldown_minutes=0,
        min_same_day_exit_edge_pct=0.5,
    )
    open_trade = PaperTrade(
        underlying="USO",
        execution_ticker="USO",
        signal_type="LONG",
        leverage="1x",
        market_session="open",
        amount=1000.0,
        shares=10.0,
        entry_price=100.0,
        entered_at=datetime.now(timezone.utc),
        analysis_request_id="prev",
    )
    db_session.add(open_trade)
    db_session.commit()

    from services import paper_trading as paper_trading_module

    original_market_status = paper_trading_module.market_status
    original_close_expired_positions = paper_trading_module.close_expired_positions
    paper_trading_module.market_status = lambda allow_extended_hours=True: {
        "status": "open",
        "label": "Market Open",
        "tradeable": True,
    }
    paper_trading_module.close_expired_positions = lambda db, alpaca_pending=None: []
    try:
        actions = process_signals(
            db=db_session,
            recommendations=[
                {
                    "underlying": "USO",
                    "execution_ticker": "SCO",
                    "signal_type": "SHORT",
                    "leverage": "1x",
                    "conviction_level": "HIGH",
                    "trading_type": "SWING",
                    "holding_minutes": 180,
                }
            ],
            quotes_by_symbol={
                "USO": {"current_price": 100.3},
                "SCO": {"current_price": 20.0},
            },
            request_id="next",
            trade_amount=1000.0,
        )
    finally:
        paper_trading_module.market_status = original_market_status
        paper_trading_module.close_expired_positions = original_close_expired_positions

    # Direction flip (LONG→SHORT) should bypass the same-day exit edge gate
    closed_trade = db_session.query(PaperTrade).filter(PaperTrade.id == open_trade.id).first()
    new_trade = (
        db_session.query(PaperTrade)
        .filter(PaperTrade.underlying == "USO", PaperTrade.id != open_trade.id, PaperTrade.exited_at.is_(None))
        .first()
    )

    assert closed_trade is not None
    assert closed_trade.exited_at is not None
    assert new_trade is not None
    assert actions[0]["action"] == "opened"


def test_min_same_day_exit_edge_does_not_block_same_day_loss_cut(db_session):
    _seed_config(
        db_session,
        paper_trade_amount=1000.0,
        reentry_cooldown_minutes=0,
        min_same_day_exit_edge_pct=0.5,
    )
    open_trade = PaperTrade(
        underlying="USO",
        execution_ticker="USO",
        signal_type="LONG",
        leverage="1x",
        market_session="open",
        amount=1000.0,
        shares=10.0,
        entry_price=100.0,
        entered_at=datetime.now(timezone.utc),
        analysis_request_id="prev",
    )
    db_session.add(open_trade)
    db_session.commit()

    from services import paper_trading as paper_trading_module

    original_market_status = paper_trading_module.market_status
    original_close_expired_positions = paper_trading_module.close_expired_positions
    paper_trading_module.market_status = lambda allow_extended_hours=True: {
        "status": "open",
        "label": "Market Open",
        "tradeable": True,
    }
    paper_trading_module.close_expired_positions = lambda db, alpaca_pending=None: []
    try:
        actions = process_signals(
            db=db_session,
            recommendations=[
                {
                    "underlying": "USO",
                    "execution_ticker": "SCO",
                    "signal_type": "SHORT",
                    "leverage": "1x",
                    "conviction_level": "HIGH",
                    "trading_type": "SWING",
                    "holding_minutes": 180,
                }
            ],
            quotes_by_symbol={
                "USO": {"current_price": 99.0},
                "SCO": {"current_price": 20.0},
            },
            request_id="next",
            trade_amount=1000.0,
        )
    finally:
        paper_trading_module.market_status = original_market_status
        paper_trading_module.close_expired_positions = original_close_expired_positions

    closed_trade = db_session.query(PaperTrade).filter(PaperTrade.id == open_trade.id).first()
    new_trade = (
        db_session.query(PaperTrade)
        .filter(PaperTrade.underlying == "USO", PaperTrade.id != open_trade.id, PaperTrade.exited_at.is_(None))
        .first()
    )

    assert closed_trade is not None
    assert closed_trade.exited_at is not None
    assert new_trade is not None
    assert actions[0]["action"] == "opened"
