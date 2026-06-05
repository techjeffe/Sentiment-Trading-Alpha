from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.append(str(Path(__file__).resolve().parents[1]))

from database.models import AlpacaOrder, AppConfig, Base, PaperTrade
from services.alpaca_broker import maybe_execute_alpaca_order


@pytest.fixture()
def db_session():
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
        tracked_symbols=["USO", "IBIT", "QQQ", "SPY"],
        custom_symbols=[],
        allow_extended_hours_trading=True,
        alpaca_execution_mode="live",
        alpaca_live_trading_enabled=True,
        alpaca_allow_short_selling=False,
        alpaca_max_position_usd=None,
        alpaca_max_total_exposure_usd=None,
        alpaca_order_type="market",
        alpaca_limit_slippage_pct=0.002,
        alpaca_daily_loss_limit_usd=None,
        alpaca_max_consecutive_losses=3,
    )
    payload.update(overrides)
    config = AppConfig(**payload)
    db_session.add(config)
    db_session.commit()
    return config


class DummyBroker:
    def __init__(self, mode: str = "live") -> None:
        self.mode = mode
        self.orders = []
        self.position = {"qty": "2"}
        self.account = {"equity": "50000", "daytrade_count": "0", "pattern_day_trader": False}

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return {
            "id": f"alpaca-{len(self.orders)}",
            "client_order_id": kwargs.get("client_order_id"),
            "type": kwargs.get("order_type"),
            "time_in_force": kwargs.get("time_in_force"),
            "status": "accepted",
            "qty": kwargs.get("qty"),
        }

    def close_position(self, symbol: str):
        result = {
            "id": f"close-{symbol}-{len(self.orders)}",
            "status": "accepted",
            "symbol": symbol.upper(),
            "side": "sell",
            "qty": float(self.position.get("qty", 0)),
        }
        self.orders.append(result)
        return result

    def get_position(self, symbol: str):
        return self.position

    def get_account(self):
        return self.account


def test_open_skips_when_symbol_is_not_user_configured(db_session, monkeypatch):
    config = _seed_config(db_session, tracked_symbols=["SPY"], custom_symbols=[])
    paper_trade = PaperTrade(
        underlying="NVDA",
        execution_ticker="NVDA",
        signal_type="LONG",
        leverage="1x",
        market_session="open",
        amount=500.0,
        shares=2.0,
        entry_price=250.0,
        entered_at=datetime.now(timezone.utc),
        analysis_request_id="req-symbol-scope",
    )
    db_session.add(paper_trade)
    db_session.commit()

    broker = DummyBroker(mode="live")
    monkeypatch.setattr("services.alpaca_broker.get_broker_from_keychain", lambda mode=None: broker)
    monkeypatch.setattr("services.alpaca_broker._is_extended_hours_now", lambda cfg=None: False)
    monkeypatch.setattr("services.alpaca_broker._is_regular_market_hours_now", lambda: True)
    monkeypatch.setattr("services.alpaca_broker._check_circuit_breakers", lambda db, cfg, pending_notional=0.0: None)

    maybe_execute_alpaca_order(db_session, paper_trade, "open", config)

    assert broker.orders == []
    skip = db_session.query(AlpacaOrder).filter(AlpacaOrder.status == "skipped").one()
    assert "not enabled in tracked/custom symbols" in (skip.error_message or "")


def test_close_sells_only_app_managed_qty_above_manual_baseline(db_session, monkeypatch):
    config = _seed_config(db_session, tracked_symbols=["NVDA"], custom_symbols=["NVDA"])
    paper_trade = PaperTrade(
        underlying="NVDA",
        execution_ticker="NVDA",
        signal_type="LONG",
        leverage="1x",
        market_session="open",
        amount=1000.0,
        shares=5.0,
        entry_price=200.0,
        entered_at=datetime.now(timezone.utc) - timedelta(days=1),
        analysis_request_id="req-close-owned-only",
    )
    db_session.add(paper_trade)
    db_session.commit()
    db_session.add(
        AlpacaOrder(
            paper_trade_id=paper_trade.id,
            alpaca_order_id="existing-open",
            client_order_id="existing-client-open",
            symbol="NVDA",
            side="buy",
            notional=1000.0,
            qty=5.0,
            order_type="market",
            time_in_force="day",
            status="filled",
            filled_qty=5.0,
            trading_mode="live",
            raw_response={"_managed_context": {"pre_existing_qty": 3.0, "pre_existing_side": "long"}},
        )
    )
    db_session.commit()

    broker = DummyBroker(mode="live")
    broker.position = {"qty": "8", "market_value": "1760.00", "side": "long"}
    monkeypatch.setattr("services.alpaca_broker.get_broker_from_keychain", lambda mode=None: broker)
    monkeypatch.setattr("services.alpaca_broker._is_extended_hours_now", lambda cfg=None: False)
    monkeypatch.setattr("services.alpaca_broker._is_regular_market_hours_now", lambda: True)
    monkeypatch.setattr("services.alpaca_broker._check_circuit_breakers", lambda db, cfg, pending_notional=0.0: None)

    maybe_execute_alpaca_order(db_session, paper_trade, "close", config)

    assert len(broker.orders) == 1
    assert broker.orders[0]["side"] == "sell"
    assert broker.orders[0]["qty"] == pytest.approx(5.0)


def test_close_skips_when_only_manual_baseline_remains(db_session, monkeypatch):
    config = _seed_config(db_session, tracked_symbols=["NVDA"], custom_symbols=["NVDA"])
    paper_trade = PaperTrade(
        underlying="NVDA",
        execution_ticker="NVDA",
        signal_type="LONG",
        leverage="1x",
        market_session="open",
        amount=1000.0,
        shares=5.0,
        entry_price=200.0,
        entered_at=datetime.now(timezone.utc) - timedelta(days=1),
        analysis_request_id="req-close-baseline-only",
    )
    db_session.add(paper_trade)
    db_session.commit()
    db_session.add(
        AlpacaOrder(
            paper_trade_id=paper_trade.id,
            alpaca_order_id="existing-open-2",
            client_order_id="existing-client-open-2",
            symbol="NVDA",
            side="buy",
            notional=1000.0,
            qty=5.0,
            order_type="market",
            time_in_force="day",
            status="filled",
            filled_qty=5.0,
            trading_mode="live",
            raw_response={"_managed_context": {"pre_existing_qty": 3.0, "pre_existing_side": "long"}},
        )
    )
    db_session.commit()

    broker = DummyBroker(mode="live")
    broker.position = {"qty": "3", "market_value": "660.00", "side": "long"}
    monkeypatch.setattr("services.alpaca_broker.get_broker_from_keychain", lambda mode=None: broker)
    monkeypatch.setattr("services.alpaca_broker._is_extended_hours_now", lambda cfg=None: False)
    monkeypatch.setattr("services.alpaca_broker._is_regular_market_hours_now", lambda: True)
    monkeypatch.setattr("services.alpaca_broker._check_circuit_breakers", lambda db, cfg, pending_notional=0.0: None)

    maybe_execute_alpaca_order(db_session, paper_trade, "close", config)

    assert broker.orders == []


@pytest.mark.parametrize("event,side", [("open", "buy"), ("close", "sell")])
def test_extended_hours_orders_use_limit_and_qty(db_session, monkeypatch, event, side):
    config = _seed_config(db_session, allow_extended_hours_trading=True, alpaca_order_type="market")
    paper_trade = PaperTrade(
        underlying="SPY",
        execution_ticker="SPY",
        signal_type="LONG",
        leverage="1x",
        market_session="pre-market",
        amount=1000.0,
        shares=2.0,
        entry_price=500.0,
        entered_at=datetime.now(timezone.utc),
        analysis_request_id="req-1",
    )
    db_session.add(paper_trade)
    db_session.commit()

    if event == "close":
        db_session.add(
            AlpacaOrder(
                paper_trade_id=paper_trade.id,
                alpaca_order_id="existing-open",
                client_order_id="existing-client-open",
                symbol="SPY",
                side="buy",
                notional=1000.0,
                qty=2.0,
                order_type="limit",
                time_in_force="day",
                extended_hours=True,
                status="accepted",
                trading_mode="live",
            )
        )
        db_session.commit()

    broker = DummyBroker(mode="live")
    monkeypatch.setattr("services.alpaca_broker.get_broker_from_keychain", lambda mode=None: broker)
    monkeypatch.setattr("services.alpaca_broker._is_extended_hours_now", lambda cfg=None: True)
    monkeypatch.setattr("services.alpaca_broker._check_circuit_breakers", lambda db, cfg, pending_notional=0.0: None)

    maybe_execute_alpaca_order(db_session, paper_trade, event, config)

    assert len(broker.orders) == 1
    order = broker.orders[0]
    assert order["side"] == side
    assert order["extended_hours"] is True
    assert order["order_type"] == "limit"
    assert order["time_in_force"] == "day"
    assert order["qty"] == 2.0
    assert order["notional"] is None
    assert order["limit_price"] == pytest.approx(501.0 if event == "open" else 499.0)


def test_regular_hours_respects_configured_order_type(db_session, monkeypatch):
    config = _seed_config(db_session, allow_extended_hours_trading=True, alpaca_order_type="market")
    paper_trade = PaperTrade(
        underlying="QQQ",
        execution_ticker="QQQ",
        signal_type="LONG",
        leverage="1x",
        market_session="open",
        amount=500.0,
        shares=1.5,
        entry_price=333.33,
        entered_at=datetime.now(timezone.utc),
        analysis_request_id="req-2",
    )
    db_session.add(paper_trade)
    db_session.commit()

    broker = DummyBroker(mode="live")
    monkeypatch.setattr("services.alpaca_broker.get_broker_from_keychain", lambda mode=None: broker)
    monkeypatch.setattr("services.alpaca_broker._is_extended_hours_now", lambda cfg=None: False)
    monkeypatch.setattr("services.alpaca_broker._is_regular_market_hours_now", lambda: True)
    monkeypatch.setattr("services.alpaca_broker._check_circuit_breakers", lambda db, cfg, pending_notional=0.0: None)

    maybe_execute_alpaca_order(db_session, paper_trade, "open", config)

    assert len(broker.orders) == 1
    order = broker.orders[0]
    assert order["extended_hours"] is False
    assert order["order_type"] == "market"
    assert order["notional"] == 500.0
    assert order["qty"] is None
    assert order["limit_price"] is None


def test_open_skips_when_existing_live_position_already_at_cap(db_session, monkeypatch):
    config = _seed_config(db_session, alpaca_max_position_usd=100.0)
    paper_trade = PaperTrade(
        underlying="USO",
        execution_ticker="USO",
        signal_type="LONG",
        leverage="1x",
        market_session="open",
        amount=100.0,
        shares=0.666667,
        entry_price=150.0,
        entered_at=datetime.now(timezone.utc),
        analysis_request_id="req-3",
    )
    db_session.add(paper_trade)
    db_session.commit()

    broker = DummyBroker(mode="live")
    broker.position = {"qty": "0.7", "market_value": "103.11", "side": "long"}
    monkeypatch.setattr("services.alpaca_broker.get_broker_from_keychain", lambda mode=None: broker)
    monkeypatch.setattr("services.alpaca_broker._is_extended_hours_now", lambda cfg=None: False)
    monkeypatch.setattr("services.alpaca_broker._is_regular_market_hours_now", lambda: True)
    monkeypatch.setattr("services.alpaca_broker._check_circuit_breakers", lambda db, cfg, pending_notional=0.0: None)

    maybe_execute_alpaca_order(db_session, paper_trade, "open", config)

    assert broker.orders == []
    skip = db_session.query(AlpacaOrder).filter(AlpacaOrder.status == "skipped").one()
    assert skip.symbol == "USO"
    assert "position cap reached" in (skip.error_message or "")


def test_extended_hours_open_uses_remaining_capacity_qty(db_session, monkeypatch):
    config = _seed_config(db_session, alpaca_max_position_usd=100.0, allow_extended_hours_trading=True)
    paper_trade = PaperTrade(
        underlying="USO",
        execution_ticker="USO",
        signal_type="LONG",
        leverage="1x",
        market_session="after-hours",
        amount=100.0,
        shares=0.666667,
        entry_price=150.0,
        entered_at=datetime.now(timezone.utc),
        analysis_request_id="req-4",
    )
    db_session.add(paper_trade)
    db_session.commit()

    broker = DummyBroker(mode="live")
    broker.position = {"qty": "0.4", "market_value": "60.00", "side": "long"}
    monkeypatch.setattr("services.alpaca_broker.get_broker_from_keychain", lambda mode=None: broker)
    monkeypatch.setattr("services.alpaca_broker._is_extended_hours_now", lambda cfg=None: True)
    monkeypatch.setattr("services.alpaca_broker._check_circuit_breakers", lambda db, cfg, pending_notional=0.0: None)

    maybe_execute_alpaca_order(db_session, paper_trade, "open", config)

    assert len(broker.orders) == 1
    order = broker.orders[0]
    assert order["notional"] is None
    assert order["qty"] == pytest.approx(40.0 / 150.0, rel=0, abs=1e-6)


def test_direction_flip_closes_existing_then_opens_new(db_session, monkeypatch):
    """Direction flip: existing live long + new SHORT signal should close then open."""
    config = _seed_config(
        db_session,
        tracked_symbols=["NET"],
        custom_symbols=["NET"],
        alpaca_allow_short_selling=True,
    )
    paper_trade = PaperTrade(
        underlying="NET",
        execution_ticker="NET",
        signal_type="SHORT",
        leverage="1x",
        market_session="open",
        amount=100.0,
        shares=0.4,
        entry_price=250.0,
        entered_at=datetime.now(timezone.utc),
        analysis_request_id="req-flip",
    )
    db_session.add(paper_trade)
    db_session.commit()

    broker = DummyBroker(mode="live")
    # Simulate existing long position at +24% P&L
    broker.position = {"qty": "0.4", "market_value": "124.0", "side": "long", "current_price": "310.0"}
    monkeypatch.setattr("services.alpaca_broker.get_broker_from_keychain", lambda mode=None: broker)
    monkeypatch.setattr("services.alpaca_broker._is_extended_hours_now", lambda cfg=None: False)
    monkeypatch.setattr("services.alpaca_broker._is_regular_market_hours_now", lambda: True)
    monkeypatch.setattr("services.alpaca_broker._check_circuit_breakers", lambda db, cfg, pending_notional=0.0: None)

    maybe_execute_alpaca_order(db_session, paper_trade, "open", config)

    # First order is the flip close (close_position → sell), second is the new short open
    assert len(broker.orders) == 2
    close_order, open_order = broker.orders
    assert close_order["side"] == "sell"   # closed the long
    assert open_order["side"] == "sell"    # opened the short


def test_direction_flip_blocks_open_if_close_fails(db_session, monkeypatch):
    """If the flip close fails, the new open should NOT be placed."""
    config = _seed_config(
        db_session,
        tracked_symbols=["NET"],
        custom_symbols=["NET"],
        alpaca_allow_short_selling=True,
    )
    paper_trade = PaperTrade(
        underlying="NET",
        execution_ticker="NET",
        signal_type="SHORT",
        leverage="1x",
        market_session="open",
        amount=100.0,
        shares=0.4,
        entry_price=250.0,
        entered_at=datetime.now(timezone.utc),
        analysis_request_id="req-flip-fail",
    )
    db_session.add(paper_trade)
    db_session.commit()

    broker = DummyBroker(mode="live")
    broker.position = {"qty": "0.4", "market_value": "124.0", "side": "long", "current_price": "310.0"}

    def _fail_close(symbol):
        raise RuntimeError("Alpaca rejected close")

    broker.close_position = _fail_close
    monkeypatch.setattr("services.alpaca_broker.get_broker_from_keychain", lambda mode=None: broker)
    monkeypatch.setattr("services.alpaca_broker._is_extended_hours_now", lambda cfg=None: False)
    monkeypatch.setattr("services.alpaca_broker._is_regular_market_hours_now", lambda: True)
    monkeypatch.setattr("services.alpaca_broker._check_circuit_breakers", lambda db, cfg, pending_notional=0.0: None)

    maybe_execute_alpaca_order(db_session, paper_trade, "open", config)

    assert broker.orders == []   # no open placed
    err = db_session.query(AlpacaOrder).filter(AlpacaOrder.error_message.isnot(None)).first()
    assert err is not None
    assert "direction flip close failed" in (err.error_message or "")



