## Problem

148 Alpaca orders were skipped due to Pattern Day Trader (PDT) restrictions.

### Alpaca Order Analysis (343 total orders)
- **148 orders skipped** with: "PDT protection: live account equity $1056.xx with daytrade_count=3 blocks opening new positions"
- Account has ~$1,056 equity and has hit the PDT limit (3 day trades)
- This means the system is day-trading too aggressively for the account size

### Root Cause
The order router has no PDT-aware guard. It sends orders without checking `daytrade_count` first. When the limit is reached, orders are silently skipped instead of being queued or downgraded to longer holding periods.

### Impact
- 43% of all Alpaca orders are wasted due to PDT blocks
- This is a massive drag on strategy performance — signals are generated but never executed

### Recommendation
Add PDT-aware order filtering in the Alpaca order router:
1. Before sending any order, check `daytrade_count` from Alpaca API
2. If at limit, either:
   - Block new day trades and queue for next day
   - Downgrade SWING signals to POSITION (longer holding period to avoid same-day exits)
   - Flag for user review via Telegram/remote control
3. Consider funding account above $30,000 to remove PDT restrictions entirely

### Current Config Gaps
- `alpaca_daily_loss_limit_usd` is configured but no PDT guard
- `alpaca_max_consecutive_losses` is set but doesn't address day trade limits

---

## STATUS: ⚠️ PARTIALLY RESOLVED

**Implemented:**
- **`alpaca_broker.py:949-991`** — `_get_pdt_block_reason()` checks `daytrade_count` and `pattern_day_trader` flag from Alpaca API
- **`alpaca_broker.py:1170-1173`** — HIGH conviction trades can override PDT blocks via `high_conviction_override`
- PDT blocks are logged with the exact reason (equity + daytrade_count)

**Not yet implemented:**
1. **No queueing mechanism** — Orders are skipped, not queued for next trading day
2. **No auto-downgrade** — SWING signals are not automatically downgraded to POSITION to avoid same-day exits
3. **No Telegram/remote alert** — When PDT limit is hit, there's no notification to the user
4. **HIGH conviction override may be too permissive** — It lets any HIGH conviction trade through, even on PDT-flagged accounts

**Remaining risk:** The 43% skip rate from the original analysis may persist if the account stays below $25k equity. The HIGH conviction override could help but may lead to unexpected day trades.