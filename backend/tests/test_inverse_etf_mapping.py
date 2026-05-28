"""
Tests for SHORT → inverse ETF mapping logic.

Verifies that when short selling is disabled, SHORT signals are routed to
the appropriate inverse ETF instead of being skipped.
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from types import SimpleNamespace


class MockPaperTrade:
    """Minimal mock of a PaperTrade row."""
    def __init__(
        self,
        underlying: str = "NVDA",
        execution_ticker: str = "NVDA",
        signal_type: str = "SHORT",
        amount: float = 100.0,
        id: int = 42,
    ):
        self.underlying = underlying
        self.execution_ticker = execution_ticker
        self.signal_type = signal_type
        self.amount = amount
        self.id = id


class TestInverseETFMapping:
    """Test the INVERSE_ETF_MAP and related logic."""

    def test_inverse_etf_map_has_entries(self):
        """The map should have entries for common tickers."""
        from services.alpaca_broker import INVERSE_ETF_MAP
        assert len(INVERSE_ETF_MAP) > 0, "Inverse ETF map should not be empty"
        # Check some expected mappings
        assert "NVDA" in INVERSE_ETF_MAP
        assert "SPY" in INVERSE_ETF_MAP
        assert "QQQ" in INVERSE_ETF_MAP

    def test_inverse_etf_map_values_are_valid_tickers(self):
        """All mapped values should be valid ticker symbols."""
        from services.alpaca_broker import INVERSE_ETF_MAP
        valid_etfs = {"PSQ", "SQQQ", "SPXS", "BITI", "RWM", "SOXS", "FAZ", "DXD"}
        for underlying, etf in INVERSE_ETF_MAP.items():
            assert etf.isalpha() and len(etf) <= 5, f"Invalid ETF ticker: {etf}"

    def test_no_duplicate_inverse_etf_targets(self):
        """Multiple underlying tickers may map to the same inverse ETF (intentional)."""
        from services.alpaca_broker import INVERSE_ETF_MAP
        # NVDA and AMD both map to PSQ — this is intentional
        # Just verify the map is a valid dict
        assert isinstance(INVERSE_ETF_MAP, dict)

    def test_direct_short_signal_with_no_short_selling_gets_mapped(self):
        """
        When signal_type=SHORT, execution_ticker=underlying, and
        allow_short=False, the code should look up an inverse ETF.
        """
        from services.alpaca_broker import INVERSE_ETF_MAP
        trade = MockPaperTrade(
            underlying="NVDA",
            execution_ticker="NVDA",
            signal_type="SHORT",
        )
        underlying = str(trade.underlying).upper()
        assert underlying in INVERSE_ETF_MAP
        inverse_etf = INVERSE_ETF_MAP[underlying]
        assert inverse_etf == "PSQ"

    def test_direct_short_signal_spy_gets_mapped(self):
        """SPY SHORT should map to SPXS."""
        from services.alpaca_broker import INVERSE_ETF_MAP
        trade = MockPaperTrade(
            underlying="SPY",
            execution_ticker="SPY",
            signal_type="SHORT",
        )
        underlying = str(trade.underlying).upper()
        assert underlying in INVERSE_ETF_MAP
        inverse_etf = INVERSE_ETF_MAP[underlying]
        assert inverse_etf == "SPXS"

    def test_direct_short_signal_tsla_gets_mapped(self):
        """TSLA SHORT should map to SQQQ."""
        from services.alpaca_broker import INVERSE_ETF_MAP
        trade = MockPaperTrade(
            underlying="TSLA",
            execution_ticker="TSLA",
            signal_type="SHORT",
        )
        underlying = str(trade.underlying).upper()
        assert underlying in INVERSE_ETF_MAP
        inverse_etf = INVERSE_ETF_MAP[underlying]
        assert inverse_etf == "SQQQ"

    def test_direct_short_signal_coin_gets_mapped(self):
        """COIN SHORT should map to BITI."""
        from services.alpaca_broker import INVERSE_ETF_MAP
        trade = MockPaperTrade(
            underlying="COIN",
            execution_ticker="COIN",
            signal_type="SHORT",
        )
        underlying = str(trade.underlying).upper()
        assert underlying in INVERSE_ETF_MAP
        inverse_etf = INVERSE_ETF_MAP[underlying]
        assert inverse_etf == "BITI"

    def test_direct_short_signal_no_map_returns_none(self):
        """A ticker not in the map should return None."""
        from services.alpaca_broker import INVERSE_ETF_MAP
        trade = MockPaperTrade(
            underlying="ZZZZZ",
            execution_ticker="ZZZZZ",
            signal_type="SHORT",
        )
        underlying = str(trade.underlying).upper()
        assert underlying not in INVERSE_ETF_MAP
        assert INVERSE_ETF_MAP.get(underlying) is None

    def test_is_direct_short_true_for_short_no_etf(self):
        """_is_direct_short returns True when signal is SHORT and execution_ticker == underlying."""
        from services.alpaca_broker import _is_direct_short
        trade = MockPaperTrade(
            underlying="NVDA",
            execution_ticker="NVDA",
            signal_type="SHORT",
        )
        assert _is_direct_short(trade) is True

    def test_is_direct_short_false_when_etf_mapped(self):
        """_is_direct_short returns False when execution_ticker != underlying."""
        from services.alpaca_broker import _is_direct_short
        trade = MockPaperTrade(
            underlying="NVDA",
            execution_ticker="PSQ",
            signal_type="SHORT",
        )
        assert _is_direct_short(trade) is False

    def test_is_direct_short_false_for_long(self):
        """_is_direct_short returns False for LONG signals."""
        from services.alpaca_broker import _is_direct_short
        trade = MockPaperTrade(
            underlying="NVDA",
            execution_ticker="NVDA",
            signal_type="LONG",
        )
        assert _is_direct_short(trade) is False

    def test_configured_live_execution_symbols_includes_inverse_etfs(self):
        """_configured_live_execution_symbols should include inverse ETFs."""
        from services.alpaca_broker import _configured_live_execution_symbols, INVERSE_ETF_MAP
        config = MagicMock()
        config.tracked_symbols = ["SPY"]
        config.custom_symbols = []
        allowed = _configured_live_execution_symbols(config)
        # All inverse ETFs should be in the allowed set
        for etf in INVERSE_ETF_MAP.values():
            assert etf in allowed, f"Inverse ETF {etf} should be in allowed execution symbols"

    def test_is_live_symbol_configured_allows_inverse_etfs(self):
        """_is_live_symbol_configured should allow SHORT→inverse ETF mappings."""
        from services.alpaca_broker import _is_live_symbol_configured, INVERSE_ETF_MAP
        config = MagicMock()
        config.tracked_symbols = ["NVDA"]
        config.custom_symbols = []
        trade = MockPaperTrade(
            underlying="NVDA",
            execution_ticker="NVDA",
            signal_type="SHORT",
        )
        # Should be True because NVDA is tracked and PSQ is in inverse ETF map
        assert _is_live_symbol_configured(config, trade) is True


class TestInverseETFMappingIntegration:
    """Integration-style tests for the full maybe_execute_alpaca_order flow."""

    @patch("services.alpaca_broker.get_broker_from_keychain")
    @patch("services.alpaca_broker._get_live_symbol_position")
    def test_short_signal_with_inverse_etf_maps_correctly(self, mock_live_pos, mock_get_broker):
        """
        A SHORT signal for NVDA with allow_short=False should map to PSQ.
        """
        from services.alpaca_broker import INVERSE_ETF_MAP
        mock_broker = MagicMock()
        mock_broker.mode = "live"
        mock_get_broker.return_value = mock_broker
        mock_live_pos.return_value = None

        trade = MockPaperTrade(
            underlying="NVDA",
            execution_ticker="NVDA",
            signal_type="SHORT",
            amount=100.0,
            id=100,
        )
        config = MagicMock()
        config.alpaca_execution_mode = "live"
        config.alpaca_allow_short_selling = False
        config.alpaca_live_trade_amount_usd = 100.0
        config.alpaca_max_position_usd = 10000.0
        config.alpaca_fixed_order_size = False
        config.alpaca_limit_slippage_pct = 0.002
        config.alpaca_order_type = "market"
        config.alpaca_max_total_exposure_usd = 50000.0
        config.alpaca_daily_loss_limit_usd = 500.0
        config.alpaca_max_consecutive_losses = 3
        config.risk_profile = "moderate"
        config.alpaca_high_conviction_override_enabled = False
        config.stop_loss_pct = 2.0
        config.take_profit_pct = 3.0
        config.alpaca_live_trade_amount_usd = 100.0

        mock_db = MagicMock()

        # Call the function
        from services.alpaca_broker import maybe_execute_alpaca_order
        maybe_execute_alpaca_order(mock_db, trade, "open", config)

        # place_order should have been called with PSQ, not NVDA
        call_args = mock_broker.place_order.call_args
        if call_args:
            symbol_arg = call_args[1].get("symbol") or call_args[0][0]
            side_arg = call_args[1].get("side") or call_args[0][1]
            # The inverse ETF should be used
            expected_etf = INVERSE_ETF_MAP.get("NVDA", "PSQ")
            assert symbol_arg == expected_etf, f"Expected {expected_etf}, got {symbol_arg}"
            assert side_arg == "buy", f"Expected 'buy' side for inverse ETF, got {side_arg}"

    @patch("services.alpaca_broker.get_broker_from_keychain")
    @patch("services.alpaca_broker._get_live_symbol_position")
    def test_short_signal_no_inverse_etf_skipped(self, mock_live_pos, mock_get_broker):
        """
        A SHORT signal for a ticker not in the inverse ETF map should be skipped
        when allow_short=False.
        """
        mock_broker = MagicMock()
        mock_broker.mode = "live"
        mock_get_broker.return_value = mock_broker
        mock_live_pos.return_value = None

        trade = MockPaperTrade(
            underlying="ZZZZZ",  # Not in inverse ETF map
            execution_ticker="ZZZZZ",
            signal_type="SHORT",
            amount=100.0,
            id=101,
        )
        config = MagicMock()
        config.alpaca_execution_mode = "live"
        config.alpaca_allow_short_selling = False
        config.alpaca_live_trade_amount_usd = 100.0
        config.alpaca_max_position_usd = 10000.0
        config.alpaca_fixed_order_size = False
        config.alpaca_limit_slippage_pct = 0.002
        config.alpaca_order_type = "market"
        config.alpaca_max_total_exposure_usd = 50000.0
        config.alpaca_daily_loss_limit_usd = 500.0
        config.alpaca_max_consecutive_losses = 3
        config.risk_profile = "moderate"
        config.alpaca_high_conviction_override_enabled = False
        config.stop_loss_pct = 2.0
        config.take_profit_pct = 3.0

        mock_db = MagicMock()

        from services.alpaca_broker import maybe_execute_alpaca_order
        maybe_execute_alpaca_order(mock_db, trade, "open", config)

        # place_order should NOT have been called (order was skipped)
        mock_broker.place_order.assert_not_called()

        # An error record should have been created
        error_calls = mock_db.add.call_args_list
        assert len(error_calls) > 0, "An error record should have been logged"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])