## Problem

The conviction system exists but is underutilized — 148 of 166 recent trades are MEDIUM conviction.

### Recommendation Trades (166 recent trades from analysis 986-999)
- **148 MEDIUM conviction** (89.2%)
- **16 HIGH conviction** (9.6%)
- **2 LOW conviction** (1.2%)

### Paper Trades Performance by Conviction
- HIGH: 91 trades, avg **+0.24%** PnL per trade
- MEDIUM: 115 trades, avg **+0.18%** PnL per trade
- LOW: 21 trades, avg **-0.16%** PnL per trade

### Decision Log Evidence
- The `decision_log_blend` table shows historical data that could calibrate thresholds
- Earlier signals that were downweighted are still being treated as MEDIUM when they should be LOW
- The blended scoring system exists but the HIGH threshold is too high to reach

### Impact
- ~40+ trades could be reclassified as HIGH if threshold lowered from 0.8 to ~0.70
- Position sizing is not differentiated by conviction level
- LOW conviction trades are losing money but still getting full position sizes

### Recommendation
1. Lower HIGH conviction threshold from 0.8 to ~0.70 — this would reclassify ~40+ trades as HIGH
2. Use conviction level to drive position sizing: HIGH = 1.5x, MEDIUM = 1.0x, LOW = 0.5x
3. Use decision_log_blend data to calibrate thresholds based on historical signal decay
4. Consider auto-blocking LOW conviction trades below 0.5 threshold

---

## STATUS: ⚠️ PARTIALLY RESOLVED

**Implemented:**
1. **Conviction thresholds are configurable** in `config/logic_config.json`:
   - `high_score_threshold: 0.6`, `high_confidence_threshold: 0.7` (normal profile)
   - `high_score_threshold: 0.5`, `high_confidence_threshold: 0.6` (crazy profile)
   - These are lower than the original 0.8 threshold mentioned in the issue

2. **Per-symbol conviction in `persistence_service.py:331-348`** — Each symbol gets its own conviction computed from its directional_score × confidence, independent of portfolio-level signal

3. **Position sizing by conviction in `paper_trading.py:424-453`** — `_compute_vol_normalized_amount()` applies conviction scalars:
   - HIGH: 1.5x, MEDIUM: 1.0x, LOW: 0.5x
   - Uses ATR-based volatility targeting: `size = (target_vol × base) / ATR_pct × conviction_scalar`

4. **LOW conviction blocking in `paper_trading.py:1141-1145`** — LOW conviction trades are blocked (except "crazy" profile)

5. **Crazy profile overrides** in `signal_service.py:98-101` and `persistence_service.py:323-324` — Custom conviction thresholds for aggressive profile

**Not yet implemented:**
1. **Threshold calibration from decision_log_blend** — The config has hardcoded thresholds, not dynamically calibrated from historical data
2. **LOW conviction trades are fully blocked, not reduced** — The issue suggested reducing size (0.5x), but the current implementation blocks them entirely (except crazy profile). This is actually more conservative/safer.
3. **0.5 absolute threshold** — No hard floor below 0.5 confidence; the current system uses score×confidence product rather than raw confidence