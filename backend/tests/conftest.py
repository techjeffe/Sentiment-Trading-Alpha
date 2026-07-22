"""
Shared pytest fixtures for backend tests.

This module provides autouse fixtures that reset module-level mutable state
between tests to prevent test pollution.
"""

import pytest
from typing import Generator


@pytest.fixture(autouse=True)
def _reset_paper_trading_state() -> Generator[None, None, None]:
    """
    Reset module-level mutable state in paper_trading.py between tests.
    
    This prevents test pollution from:
    - _cron_overlap_keys: Prevents duplicate orders across tests
    - _cron_overlap_underlying_keys: Prevents opposing position tests
    - _last_order_times: Prevents manual+auto duplicate order tests
    
    Yields control to the test, then cleans up after.
    """
    # Run the test
    yield
    
    # Cleanup: reset module-level dicts in paper_trading
    try:
        from services.paper_trading import (
            _cron_overlap_keys,
            _cron_overlap_underlying_keys,
            _last_order_times,
        )
        
        _cron_overlap_keys.clear()
        _cron_overlap_underlying_keys.clear()
        _last_order_times.clear()
    except ImportError:
        # paper_trading not imported in this test, nothing to clean
        pass
