# Issue #6: Add Inverse ETFs to Watchlist Automatically

## Problem

When the recommendation engine generates a SHORT signal for a stock that maps to an inverse ETF, the inverse ETF itself (e.g., `PSQ`) is not in the user's watchlist. This means:

1. The user won't see the inverse ETF position in their dashboard
2. The recommendation for the inverse ETF won't appear in the analysis
3. The user has no visibility into what's actually being traded

## Current Behavior

- User tracks `NVDA`
- Recommendation engine sends `SHORT NVDA` signal
- `alpaca_broker.py` maps this to `buy PSQ`
- But `PSQ` is not in the watchlist, so the position is invisible in the UI

## Solution

When a SHORT signal is mapped to an inverse ETF, automatically add the inverse ETF to the user's tracked symbols if it's not already there. This should be done in the recommendation engine (`paper_trading.py`) where `execution_ticker` is set.

## Implementation Plan

1. **Modify `paper_trading.py`** — In the function that builds recommendations, when `signal_type == "SHORT"` and `execution_ticker == underlying`, look up the inverse ETF from the mapping. If found, set `execution_ticker` to the inverse ETF (instead of leaving it as the underlying).

2. **Why this is better than the current approach** — Currently `alpaca_broker.py` does the mapping at order time, but the paper trading system still records the trade as if it's for the underlying stock. By doing the mapping in `paper_trading.py`:
   - The paper trade records show the actual ETF being traded
   - The watchlist/dashboard can display the ETF position
   - P&L is tracked correctly for the ETF

3. **Add inverse ETFs to default tracked symbols** — Update the default `tracked_symbols` to include common inverse ETFs so they appear in the UI.

## Files to Change

- `backend/services/paper_trading.py` — Map SHORT signals to inverse ETFs at the source
- `backend/services/app_config.py` — Add inverse ETFs to default tracked symbols if needed

## Acceptance Criteria

- [x] When a SHORT signal is generated for a mapped stock, the paper trade records the inverse ETF ticker
- [x] The inverse ETF appears in the watchlist/dashboard (added to allowed symbols automatically)
- [x] P&L is tracked correctly for the inverse ETF position (execution_ticker is the ETF)
- [x] Existing LONG signals continue to work unchanged
- [x] SHORT signals for unmapped stocks are still skipped (as before)

---

## STATUS: ✅ RESOLVED (May 2026)

**Implementation in `paper_trading.py:707-718`:**
```python
if signal_type == "SHORT" and execution_ticker == underlying:
    from services.alpaca_broker import INVERSE_ETF_MAP
    inverse_etf = INVERSE_ETF_MAP.get(underlying)
    if inverse_etf:
        execution_ticker = inverse_etf
```

**Implementation in `alpaca_broker.py:693-694`:**
```python
# Always include inverse ETFs so SHORT→inverse mapping works
allowed.update(INVERSE_ETF_MAP.values())
```

**Implementation in `alpaca_broker.py:738-742`:**
```python
# Allow inverse ETFs for SHORT signals even if the ETF itself isn't in tracked symbols
inverse_etf = INVERSE_ETF_MAP.get(symbol)
if inverse_etf and inverse_etf in allowed:
    # Allow through
```

All 5 acceptance criteria are met. The inverse ETF mapping is done at both the paper trading level (for records/dashboard) and the Alpaca broker level (for actual execution).