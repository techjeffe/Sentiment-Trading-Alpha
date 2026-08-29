# Trading Strategy Recommendations Based on Database Analysis

**Analysis Date:** 2026-04-22  
**Data Source:** trading_system.db (34 trades, 21 snapshots, 3 executions, 1 close)

---

## KEY FINDINGS SUMMARY

| Metric | Value |
|--------|-------|
| Total Trades Generated | 34 |
| Trades Executed | 3 (8.8%) |
| Trades Closed | 1 (2.9%) |
| Win Rate (closed) | 0% (1 loss) |
| Avg Confidence | 0.71 (all MEDIUM) |
| Avg 1h Return | -0.11% |
| Snapshot Positive Rate | 48% |

---

## RECOMMENDATION 1: Fix Execution Gap - 91% of Trades Never Executed

### Problem
Only **3 of 34 trades** (8.8%) were actually executed, and only **1 trade** was closed. The system generates signals but fails to execute them.

```
Trade #140: USO BUY @ $128.25 → Closed @ $129.94 (LOSS of $1.69)
Trade #144: QQQ SELL @ $649.32 → Not closed
Trade #168: SBIT BUY @ $40.63 → Not closed
```

### Root Cause
The `trade_executions` table shows only 3 entries for 34 trades. The execution pipeline is either not triggered or failing silently.

### Recommendation
1. **Enable paper trading execution** - Ensure `auto_run_enabled=True` triggers trade execution
2. **Add execution monitoring** - Create alerts when trades are generated but not executed within 1 hour
3. **Fix the execution gap** - Debug why `maybe_execute_alpaca_order` or `process_signals` isn't being called for most trades
4. **Add retry logic** - If execution fails, retry up to 3 times before skipping

### Expected Impact
If all 34 trades were executed with the same -0.11% avg return, the portfolio would lose ~$0.37/trade. But more importantly, we need to validate the strategy with actual execution data.

---

## RECOMMENDATION 2: Eliminate Blind Short Bias - 94% of Trades Are SHORT

### Problem
**32 of 34 trades (94%) are SHORT signals**, and **25 of 34 are BUY orders for inverse ETFs**. This is a one-directional strategy that will fail in bull markets.

```
Signal Type Distribution:
  SHORT: 32 trades (94%)
  HOLD:   2 trades  (6%)

Execution Tickers:
  SPXS (3x Short SPY):  6 trades
  SQQQ (3x Short QQQ):  5 trades
  SBIT (2x Short Bitcoin): 5 trades
  SCO (2x Short Oil):   3 trades
```

### Root Cause
The sentiment engine is producing overwhelmingly SHORT signals. Looking at the analysis results:
```
analysis #48: signal_type=SHORT, symbol=USO → action=BUY (inverse ETF)
analysis #48: signal_type=SHORT, symbol=QQQ → action=SELL
analysis #48: signal_type=SHORT, symbol=SPY → action=SELL
```

The system interprets SHORT sentiment as BUY for inverse ETFs, but the underlying sentiment analysis is biased toward bearishness.

### Recommendation
1. **Add long signal capability** - The strategy should go LONG on regular ETFs (SPY, QQQ, USO) when sentiment is bullish
2. **Implement market regime detection** - In bull markets, SHORT signals will lose money. Add a regime filter that reduces SHORT exposure when SPY is trending up
3. **Target 60/40 long-short ratio** - Even a contrarian strategy needs long exposure to benefit from market drift
4. **Add mean reversion filter** - If the underlying asset has dropped >3% in 24h, a SHORT signal might be a "sell into weakness" not a "continue shorting"

### Expected Impact
Adding long signals would roughly double the trading opportunities and reduce drawdown during bull markets.

---

## RECOMMENDATION 3: Implement Conviction-Based Position Sizing

### Problem
**ALL 34 trades have MEDIUM conviction** - the conviction system is completely broken. No distinction is made between high and low confidence signals.

```
Confidence Score Distribution:
  HIGH (>=0.8):  2 trades, avg conf=0.83
  MEDIUM (0.6-0.8): 30 trades, avg conf=0.72
  LOW (<0.6):    2 trades, avg conf=0.55

Conviction Level Distribution:
  MEDIUM: 34 trades (100%)
  HIGH:   0 trades
  LOW:    0 trades
```

### Root Cause
The conviction_level is not being updated from the analysis results. The `trades` table shows conviction_level="MEDIUM" for all trades regardless of confidence_score. The mapping from confidence_score to conviction_level is broken.

### Recommendation
1. **Fix conviction mapping** - Implement proper thresholds:
   - confidence >= 0.8 → HIGH conviction
   - confidence 0.6-0.8 → MEDIUM conviction
   - confidence < 0.6 → LOW conviction
2. **Implement position sizing by conviction:**
   - HIGH conviction: 100% of trade amount
   - MEDIUM conviction: 50% of trade amount
   - LOW conviction: 25% of trade amount (or skip entirely)
3. **Add conviction-based leverage adjustment:**
   - HIGH conviction: up to 3x leverage
   - MEDIUM conviction: max 2x leverage
   - LOW conviction: max 1x leverage or skip

### Expected Impact
Proper conviction-based sizing would have reduced exposure on the losing trades and increased exposure on the winning ones. The highest conviction trade (UCO, conf=0.83) was profitable (+1.03% at 1h), while low conviction trades (USO, conf=0.55) had mixed results.

---

## RECOMMENDATION 4: Fix Stop Loss and Take Profit Parameters

### Problem
**ALL trades have identical stop loss (2%) and take profit (3%)** regardless of:
- Volatility (ATR)
- Leverage used
- Market regime
- Time horizon

```
All trades: sl=2.0% tp=3.0%
All trades: holding_period=4h or 12h
All trades: trading_type=SWING
```

### Root Cause
The stop loss and take profit are hardcoded defaults, not calculated based on:
1. **ATR-based sizing** - The decision_log has ATR data but it's not being used in the trades table
2. **Leverage adjustment** - A 2% stop on 3x leverage is a 6% portfolio loss
3. **Volatility adjustment** - High volatility assets need wider stops

### Recommendation
1. **Implement ATR-based stops:**
   - Stop loss = max(2%, 1.5 × ATR)
   - Take profit = max(3%, 2 × ATR)
2. **Adjust for leverage:**
   - 1x leverage: 2% stop, 3% target
   - 2x leverage: 1% stop, 1.5% target (risk-adjusted)
   - 3x leverage: 0.5% stop, 1% target (de-risked)
3. **Add time-based exits:**
   - If position hasn't reached TP after 2× holding period, exit
   - If position is losing >1% after 1h, consider early exit
4. **Implement trailing stops** - Once profit > 2%, move stop to breakeven

### Expected Impact
The UCO trade (#155) had +1.03% at 1h with 2x leverage. With a proper trailing stop, we could have captured more of that gain instead of exiting at fixed TP.

---

## RECOMMENDATION 5: Add Trade-Level Performance Tracking and Feedback Loop

### Problem
The system generates 34 trades but only tracks 1 close. There's no feedback loop to learn from trade outcomes.

```
Trade Tracking:
  - 34 trades generated
  - 3 trades executed
  - 1 trade closed
  - 21 trade snapshots (forward-looking PnL projections)
  - 0 trades with both entry AND exit recorded
```

### Root Cause
The `trade_closes` table has only 1 entry. The system is not tracking whether trades are closed at stop loss, take profit, or time expiry. Without exit data, we can't calculate real PnL or improve the strategy.

### Recommendation
1. **Implement automatic trade closing:**
   - At stop loss price
   - At take profit price
   - At holding period expiry
   - When signal reverses (e.g., SHORT→LONG)
2. **Add post-trade analysis:**
   - Record exit reason: "stop_loss", "take_profit", "time_exit", "signal_reversal"
   - Record actual PnL vs projected PnL from snapshots
   - Calculate slippage: entry_price vs execution_price
3. **Implement strategy backtesting:**
   - Use trade_snapshots to simulate forward PnL
   - Compare 1h, 4h, 1d, 3d horizons to find optimal exit timing
   - Example from data: Trade #146 (USO BUY) had +1.27% at 1h, suggesting the strategy works for energy longs
4. **Add A/B testing for strategies:**
   - Group A: 3x leverage inverse ETFs
   - Group B: 1x leverage regular ETFs
   - Compare risk-adjusted returns

### Expected Impact
With proper tracking, we could identify that:
- Trade #155 (UCO BUY 2x, conf=0.83) was the highest conviction trade and it was profitable
- Trade #151 (SCO BUY 2x, conf=0.77) lost -1.66% at 1h
- The difference: UCO (crude oil) was trending up, SCO (oil short) was trending down

---

## ADDITIONAL INSIGHTS FROM DATA

### Best Performing Signals
| Trade | Symbol | Signal | Leverage | 1h Return | Confidence |
|-------|--------|--------|----------|-----------|------------|
| #146 | USO | BUY | 1x | +1.27% | 0.61 |
| #149 | USO | BUY | 1x | +1.58% | 0.55 |
| #155 | UCO | BUY | 2x | +1.03% | 0.83 |
| #150 | SPXS | BUY | 3x | +0.18% | 0.55 |
| #160 | SPXS | BUY | 3x | +0.19% | 0.75 |

### Worst Performing Signals
| Trade | Symbol | Signal | Leverage | 1h Return | Confidence |
|-------|--------|--------|----------|-----------|------------|
| #151 | SCO | BUY | 2x | -1.66% | 0.77 |
| #152 | SBIT | BUY | 2x | -1.24% | 0.77 |
| #141 | QQQ | SELL | 3x | -1.32% | 0.64 |
| #157 | SCO | BUY | 2x | -0.66% | 0.75 |
| #147 | SQQQ | BUY | 3x | -0.68% | 0.61 |

### Key Observation
**USO (oil/energy) trades were consistently profitable** while **inverse tech trades (SQQQ, SPXS) were mixed**. This suggests:
1. The sentiment engine correctly identified energy sector strength on 2026-04-22
2. The tech short thesis was wrong - QQQ and SPX were not declining as expected
3. The strategy should weight sector-specific signals more heavily than broad market direction

---

## IMPLEMENTATION PRIORITY

1. **HIGH PRIORITY:** Fix execution gap (Recommendation #1)
2. **HIGH PRIORITY:** Add long signal capability (Recommendation #2)
3. **MEDIUM PRIORITY:** Fix conviction-based sizing (Recommendation #3)
4. **MEDIUM PRIORITY:** Implement ATR-based stops (Recommendation #4)
5. **LOW PRIORITY:** Add trade tracking feedback loop (Recommendation #5)

---

## CONCLUSION

The trading strategy has potential but suffers from critical implementation gaps:
- **91% of trades never execute** - the system generates ideas but doesn't act on them
- **94% SHORT bias** - the strategy is one-directional and will fail in bull markets
- **Zero conviction differentiation** - all trades treated equally regardless of confidence
- **Static risk parameters** - stops and targets don't adapt to volatility or leverage
- **No performance feedback** - trades aren't properly closed or tracked

Fixing these issues would transform the strategy from a "generate and forget" system into a complete trading pipeline with proper execution, risk management, and learning capabilities.