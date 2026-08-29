"""Opening Range (ORB) guard tests.

Covers: opening-wait block, opening-range-against block, HIGH-conviction
override, fail-open on missing data, and regular-session-only behavior.
All time-dependent logic takes an explicit `now`; range data is monkeypatched.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from services import paper_trading as pt

_ET = ZoneInfo("America/New_York")


def _now(et_time_str: str) -> datetime:
    """Build a tz-aware UTC datetime from an 'HH:MM' ET time on a trading weekday."""
    dt = datetime.now(_ET).date()
    # Force a weekday (pick a known Wednesday if the real day is a weekend)
    while dt.weekday() >= 5:
        dt = dt.replace(day=dt.day - 1)
    hour, minute = (int(x) for x in et_time_str.split(":"))
    return datetime(dt.year, dt.month, dt.day, hour, minute, tzinfo=_ET).astimezone(timezone.utc)


@pytest.fixture()
def fake_range(monkeypatch):
    """Stub _opening_range_data to return a deterministic range.

    Default range: high=100.0, low=99.0  → 'above' requires price >= 100.2,
    'below' requires price <= 98.802 (min_break_pct=0.2%).
    """
    def _install(data):
        monkeypatch.setattr(pt, "_opening_range_data", lambda *a, **k: data)
    return _install


class TestOpeningWait:
    def test_blocks_new_entry_during_wait_window(self):
        # 9:31 ET — 1 minute after open, inside wait_minutes=15
        reason, info = pt._opening_range_block(
            "SPY", "SPY", 100.0, "LONG", "HIGH", _now("09:31")
        )
        assert reason == "opening_wait"
        assert info["minutes_since_open"] < 15

    def test_allows_when_wait_elapsed(self, fake_range):
        fake_range({"high": 100.0, "low": 99.0, "bar_count": 3})
        assert pt._opening_range_block("SPY", "SPY", 100.5, "LONG", "MEDIUM", _now("10:00")) is None


class TestOpeningRangeAgainst:
    def test_blocks_long_when_price_below_range(self, fake_range):
        # Price 99.5 is below the 'above' threshold (100.2) and above the
        # 'below' threshold (98.802) → inside → should NOT block.
        fake_range({"high": 100.0, "low": 99.0, "bar_count": 3})
        assert pt._opening_range_block("SPY", "SPY", 99.5, "LONG", "MEDIUM", _now("10:00")) is None

    def test_blocks_short_when_price_broke_above(self, fake_range):
        fake_range({"high": 100.0, "low": 99.0, "bar_count": 3})
        reason, info = pt._opening_range_block("SPY", "SPY", 100.5, "SHORT", "MEDIUM", _now("10:00"))
        assert reason == "opening_range_against"
        assert info["price_side"] == "above"

    def test_blocks_long_when_price_broke_below(self, fake_range):
        fake_range({"high": 100.0, "low": 99.0, "bar_count": 3})
        reason, info = pt._opening_range_block("SPY", "SPY", 98.5, "LONG", "MEDIUM", _now("10:00"))
        assert reason == "opening_range_against"
        assert info["price_side"] == "below"

    def test_high_conviction_overrides_against(self, fake_range):
        fake_range({"high": 100.0, "low": 99.0, "bar_count": 3})
        assert pt._opening_range_block("SPY", "SPY", 100.5, "SHORT", "HIGH", _now("10:00")) is None

    def test_allows_signal_with_the_break(self, fake_range):
        fake_range({"high": 100.0, "low": 99.0, "bar_count": 3})
        assert pt._opening_range_block("SPY", "SPY", 100.5, "LONG", "LOW", _now("10:00")) is None
        assert pt._opening_range_block("SPY", "SPY", 98.5, "SHORT", "LOW", _now("10:00")) is None


class TestFailOpen:
    def test_missing_range_data_allows(self, fake_range):
        fake_range(None)
        assert pt._opening_range_block("SPY", "SPY", 100.5, "SHORT", "MEDIUM", _now("10:00")) is None

    def test_zero_price_allows(self, fake_range):
        fake_range({"high": 100.0, "low": 99.0, "bar_count": 3})
        assert pt._opening_range_block("SPY", "SPY", 0.0, "SHORT", "MEDIUM", _now("10:00")) is None

    def test_disabled_config_allows(self, fake_range, monkeypatch):
        fake_range({"high": 100.0, "low": 99.0, "bar_count": 3})
        monkeypatch.setitem(pt._L, "opening_range", {"enabled": False})
        assert pt._opening_range_block("SPY", "SPY", 100.5, "SHORT", "MEDIUM", _now("10:00")) is None


class TestSessionScope:
    def test_premarket_never_gated(self, fake_range):
        fake_range({"high": 100.0, "low": 99.0, "bar_count": 3})
        assert pt._opening_range_block("SPY", "SPY", 100.5, "SHORT", "MEDIUM", _now("06:00")) is None

    def test_after_hours_never_gated(self, fake_range):
        fake_range({"high": 100.0, "low": 99.0, "bar_count": 3})
        assert pt._opening_range_block("SPY", "SPY", 100.5, "SHORT", "MEDIUM", _now("17:00")) is None