"""
Tests for Alpaca broker entry/exit rule enforcement.

Validates that:
- LOW conviction signals are blocked from entering Alpaca
- Stop-loss monitoring blocks open for positions at/below threshold
- Take-profit monitoring blocks open for positions at/above threshold
- Long/short P&L calculations are correct
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Mock httpx BEFORE importing alpaca_broker (which imports httpx at module level)
sys.modules['httpx'] = MagicMock()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestAlpacaEntryConvictionGate(unittest.TestCase):
    """Test the entry conviction gate in Alpaca broker."""

    def _make_mock_paper_trade(self, conviction_level="HIGH", signal_type="LONG",
                               underlying="USO", execution_ticker="USO",
                               amount=100.0, entry_price=100.0, id=1):
        """Create a mock PaperTrade object."""
        trade = MagicMock()
        trade.conviction_level = conviction_level
        trade.signal_type = signal_type
        trade.underlying = underlying
        trade.execution_ticker = execution_ticker
        trade.amount = amount
        trade.entry_price = entry_price
        trade.id = id
        trade.shares = amount / entry_price if entry_price > 0 else 0
        return trade

    def test_low_conviction_blocked(self):
        """LOW conviction should be blocked before Alpaca dispatch."""
        from services.alpaca_broker import _get_entry_conviction_block_reason

        paper_trade = self._make_mock_paper_trade(conviction_level="LOW")
        reason = _get_entry_conviction_block_reason(paper_trade, "open")
        self.assertEqual(reason, "entry rule: low conviction blocked")

    def test_medium_conviction_allowed(self):
        """MEDIUM conviction should pass."""
        from services.alpaca_broker import _get_entry_conviction_block_reason

        paper_trade = self._make_mock_paper_trade(conviction_level="MEDIUM")
        reason = _get_entry_conviction_block_reason(paper_trade, "open")
        self.assertIsNone(reason)

    def test_high_conviction_allowed(self):
        """HIGH conviction should pass."""
        from services.alpaca_broker import _get_entry_conviction_block_reason

        paper_trade = self._make_mock_paper_trade(conviction_level="HIGH")
        reason = _get_entry_conviction_block_reason(paper_trade, "open")
        self.assertIsNone(reason)

    def test_close_event_no_conviction_gate(self):
        """Conviction gate should not apply to close events."""
        from services.alpaca_broker import _get_entry_conviction_block_reason

        paper_trade = self._make_mock_paper_trade(conviction_level="LOW")
        reason = _get_entry_conviction_block_reason(paper_trade, "close")
        self.assertIsNone(reason)


class TestAlpacaStopLossMonitoring(unittest.TestCase):
    """Test live position stop-loss monitoring."""

    def _make_mock_broker(self, symbol: str, side: str, entry_price: float,
                          current_price: float, qty: float = 10.0):
        """Create a mock AlpacaBroker with position data."""
        broker = MagicMock()
        position = {
            "symbol": symbol.upper(),
            "qty": qty,
            "side": side,
            "avg_entry_price": entry_price,
            "current_price": current_price,
        }
        broker.get_position = MagicMock(return_value=position)
        return broker

    def test_long_stop_loss_triggered(self):
        """Long position at -2.1% should trigger at 2.0% stop-loss."""
        from services.alpaca_broker import _check_live_position_stop_loss

        broker = self._make_mock_broker("USO", "long", 100.0, 97.9, 10.0)
        result = _check_live_position_stop_loss(broker, "USO", 2.0)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["pnl_pct"], -2.1, places=1)

    def test_long_stop_loss_not_triggered(self):
        """Long position at -1.5% should not trigger at 2.0% stop-loss."""
        from services.alpaca_broker import _check_live_position_stop_loss

        broker = self._make_mock_broker("USO", "long", 100.0, 98.5, 10.0)
        result = _check_live_position_stop_loss(broker, "USO", 2.0)
        self.assertIsNone(result)

    def test_short_stop_loss_triggered(self):
        """Short position at -2.1% should trigger at 2.0% stop-loss."""
        from services.alpaca_broker import _check_live_position_stop_loss

        # Short: entry=100, current=102.1 → loss = (100-102.1)/100*100 = -2.1%
        broker = self._make_mock_broker("USO", "short", 100.0, 102.1, 10.0)
        result = _check_live_position_stop_loss(broker, "USO", 2.0)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["pnl_pct"], -2.1, places=1)

    def test_short_stop_loss_not_triggered(self):
        """Short position at -1.5% should not trigger at 2.0% stop-loss."""
        from services.alpaca_broker import _check_live_position_stop_loss

        # Short: entry=100, current=101.5 → loss = (100-101.5)/100*100 = -1.5%
        broker = self._make_mock_broker("USO", "short", 100.0, 101.5, 10.0)
        result = _check_live_position_stop_loss(broker, "USO", 2.0)
        self.assertIsNone(result)

    def test_zero_stop_loss_returns_none(self):
        """Zero stop-loss should always return None."""
        from services.alpaca_broker import _check_live_position_stop_loss

        broker = self._make_mock_broker("USO", "long", 100.0, 50.0, 10.0)
        result = _check_live_position_stop_loss(broker, "USO", 0.0)
        self.assertIsNone(result)


class TestAlpacaTakeProfitMonitoring(unittest.TestCase):
    """Test live position take-profit monitoring."""

    def _make_mock_broker(self, symbol: str, side: str, entry_price: float,
                          current_price: float, qty: float = 10.0):
        broker = MagicMock()
        position = {
            "symbol": symbol.upper(),
            "qty": qty,
            "side": side,
            "avg_entry_price": entry_price,
            "current_price": current_price,
        }
        broker.get_position = MagicMock(return_value=position)
        return broker

    def test_long_take_profit_triggered(self):
        """Long position at +3.1% should trigger at 3.0% take-profit."""
        from services.alpaca_broker import _check_live_position_take_profit

        broker = self._make_mock_broker("USO", "long", 100.0, 103.1, 10.0)
        result = _check_live_position_take_profit(broker, "USO", 3.0)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["pnl_pct"], 3.1, places=1)

    def test_long_take_profit_not_triggered(self):
        """Long position at +2.5% should not trigger at 3.0% take-profit."""
        from services.alpaca_broker import _check_live_position_take_profit

        broker = self._make_mock_broker("USO", "long", 100.0, 102.5, 10.0)
        result = _check_live_position_take_profit(broker, "USO", 3.0)
        self.assertIsNone(result)

    def test_short_take_profit_triggered(self):
        """Short position at +3.1% should trigger at 3.0% take-profit."""
        from services.alpaca_broker import _check_live_position_take_profit

        # Short: entry=100, current=96.9 → profit = (100-96.9)/100*100 = 3.1%
        broker = self._make_mock_broker("USO", "short", 100.0, 96.9, 10.0)
        result = _check_live_position_take_profit(broker, "USO", 3.0)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["pnl_pct"], 3.1, places=1)

    def test_short_take_profit_not_triggered(self):
        """Short position at +2.5% should not trigger at 3.0% take-profit."""
        from services.alpaca_broker import _check_live_position_take_profit

        # Short: entry=100, current=97.5 → profit = (100-97.5)/100*100 = 2.5%
        broker = self._make_mock_broker("USO", "short", 100.0, 97.5, 10.0)
        result = _check_live_position_take_profit(broker, "USO", 3.0)
        self.assertIsNone(result)

    def test_zero_take_profit_returns_none(self):
        """Zero take-profit should always return None."""
        from services.alpaca_broker import _check_live_position_take_profit

        broker = self._make_mock_broker("USO", "long", 100.0, 200.0, 10.0)
        result = _check_live_position_take_profit(broker, "USO", 0.0)
        self.assertIsNone(result)


class TestAlpacaOrderSkipRecord(unittest.TestCase):
    """Test the order skip recording function."""

    def test_record_alpaca_order_skip_called(self):
        """_record_alpaca_order_skip should create an order with skipped status."""
        from unittest.mock import patch
        mock_alpaca_order_cls = MagicMock()
        mock_alpaca_order_cls.return_value.status = "skipped"
        fake_db_models = MagicMock()
        fake_db_models.AlpacaOrder = mock_alpaca_order_cls

        with patch.dict('sys.modules', {'database': MagicMock(), 'database.models': fake_db_models}):
            from services.alpaca_broker import _record_alpaca_order_skip

            db = MagicMock()
            _record_alpaca_order_skip(
                db, paper_trade_id=1, side="buy", symbol="USO",
                notional=100.0, trading_mode="live",
                reason="test_reason", client_order_id="test-001",
            )
            # Should have called db.add (the order is created internally)
            db.add.assert_called_once()
            db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
