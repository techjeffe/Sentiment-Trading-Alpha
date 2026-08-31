# Trading Logic Reference

This document explains how the system decides when to enter and exit trades, how sentiment is scored, how scores decay and blend across runs, and how each key threshold can be changed.

---

## How a Trade Gets Opened

Every analysis run processes recent news articles through a two-layer AI review (blue team + red team). The output for each symbol is a **directional score** between −1.0 and +1.0.

If **continuous entry sizing** is enabled (default on), the score controls position *size* via a sigmoid curve instead of a binary gate:

```
size_pct = min + (max - min) / (1 + exp(-steepness * (|score| - midpoint)))
```

At `|score| = midpoint` the position is 50% of full size. Scores below `skip_floor` produce a no-entry signal.

If continuous entry is off, the legacy binary gate applies:

```
directional score > entry_threshold  →  LONG signal
directional score < −entry_threshold →  SHORT signal
otherwise                             →  HOLD
```

**Default values** (configurable in Admin → Trading Logic and `logic_config.json`):

| Setting | Default | Description |
|---|---|---|
| `entry_threshold` (normal) | 0.42 | Minimum |score| to enter a new position |
| `entry_threshold` (closed market) | 0.42 | During closed-market hysteresis the threshold stays at 0.42 |
| `keep_threshold` (normal) | 0.30 | Lower bar to keep an existing position open |
| `keep_threshold` (closed market) | 0.22 | Even lower bar to keep during closed-market mode |
| `sigmoid_midpoint` | 0.42 | Score at which continuous entry is 50% sized |
| `sigmoid_steepness` | 6.0 | How sharp the sigmoid transition is |
| `skip_floor` | 0.10 | Score below this → no entry at all |
| `keep_floor` | 0.10 | Score below this on existing position → close entirely |

### Regime Adaptation (Volatility-Adjusted Threshold)

When enabled (default on), the entry threshold is dynamically adjusted based on market volatility:

- **ATR % ≥ 3.0 (high vol):** entry_threshold × 1.25 (harder to enter)
- **ATR % ≤ 1.0 (low vol):** entry_threshold × 0.80 (easier to enter)

The max ATR % across all tracked symbols is used as the trigger.

---

## Signal Decay (Age-Based Score Attenuation)

Directional scores are exponentially decayed based on hours elapsed since the previous analysis run:

```
decay_factor = 0.5 ^ (age_hours / half_life)
effective_directional = directional_score × decay_factor
```

**Per-symbol half-lives** (from `logic_config.json → signal_decay`):

| Symbol | Half-life |
|---|---|
| SPY, QQQ | 2 hours |
| USO | 4 hours |
| IBIT, BITO | 6 hours |
| Default (custom symbols) | 3 hours |

### Separate Hold Decay (for Existing Positions)

When `hold_decay_enabled` is on, positions already held use a slower decay rate than new entries, preventing existing positions from decaying too quickly under stale news:

```
hold_decay_factor = 0.5 ^ (age_hours / hold_half_life)
effective_hold_directional = directional_score × hold_decay_factor
```

Hold half-lives are configured separately under `signal_decay.symbol_hold_half_lives` in `logic_config.json`. When hold decay is disabled, the standard decay factor is used for both entry and hold decisions.

---

## Rolling Sentiment Averaging

Per-symbol sentiment scores from the current run are blended with recent historical runs using exponential decay. This prevents a single run of noisy or sparse articles from flipping the trading signal.

```
blended_score = (current_weight × current_score + Σ(decayed_weight_i × score_i)) / total_weight
```

Weight = `0.5 ^ (age_hours / half_life)` where `half_life = 20 minutes` (0.33 hours). Runs older than 2 hours are excluded entirely.

**Stabilization effect (20-min half-life at 10-min schedule):**

| Age | Weight vs Current |
|---|---|
| Current run | 100% |
| 10 min ago | 70% |
| 20 min ago | 50% |
| 30 min ago | 35% |
| 60 min ago | 12% |

Non-numeric fields (reasoning, signal_type, urgency) are preserved from the current run, not blended.

---

## How a Trade Gets Closed

A position is closed when any of these happen:

1. **HOLD signal fires** — Instead of closing immediately, a **trailing stop** is set. The stop = `best_price_seen × (1 ± stop_loss_pct × tighten_factor)`. The stop tightens every run while in trailing mode. The position only closes if price crosses the stop level. Thesis re-confirmation clears the trailing stop.
2. **Direction flips** — e.g., LONG → SHORT. The old position closes, then a new one opens in the new direction.
3. **Different execution ticker or leverage** — same result: close old, open new.
4. **Symbol removed from tracked list** — position is closed at the next available price.
5. **Conviction window expires** — When `trail_on_window_expiry` is enabled (default on), expired positions transition to trailing stop mode instead of closing flat. When disabled, they close immediately at market price.
6. **Data gap HOLD** — If the signal is HOLD and article count dropped by ≥60% from a previous baseline of ≥10, the HOLD is flagged as `data_gap_hold` and the position is *preserved* rather than closed. The flag clears once adequate data returns.
7. **Trailing stop hit** — If a trailing stop was set and the price crosses it, the position closes.

### Conviction Window Reset

When a re-run confirms the same direction, the holding window resets:
- Same or upgraded trade type → full window
- Downgraded type → proportionally reduced window

---

## Materiality Gate (Thesis Flip Prevention)

Before the model is allowed to flip from LONG → SHORT or vice versa, the system checks whether the change is "material" — i.e., something real changed. A flip is blocked unless **at least one** of these is true:

| Condition | Default threshold | Admin-editable |
|---|---|---|
| New articles vs last run | ≥ 6 new articles (or dynamic rolling baseline) | Yes |
| Sentiment score shift | ≥ 0.24 change | Yes |
| Price move vs last run | ≥ ATR × 0.5, bounded 0.75%–3.0% | No (JSON only) |

The article count threshold uses a **dynamic rolling baseline** when enough history exists (≥5 runs). The baseline is the mean ± 1 stddev over the last 20 runs per symbol. Until enough history accumulates, the fixed threshold (6 articles) is used.

### Data Gap Protection

When article count drops by ≥60% from the previous run (and the previous run had ≥10 articles), a HOLD signal is flagged as `data_gap_hold`. This signals paper trading to preserve open positions instead of closing them, preventing transient data gaps from unwinding positions.

The History tab in the dashboard shows a **DATA GAP** badge on affected runs.

---

## How Sentiment Is Scored

The LLM never outputs raw numbers. Instead it outputs structured facts (event type, whether confirmed, bluster vs substance phrases, symbol relevance), and Python converts those to calibrated scores.

### Bluster Score (−1.0 to +1.0)
Measures how much of the content is rhetorical noise vs substantive policy content.

```
raw = (substance_phrases - bluster_phrases) / total_phrases
if unconfirmed: raw -= 0.15          (was 0.35 — lowered to reduce auto-SHORT bias)
bluster_score = clamp(raw, -1.0, 1.0)
```

A SHORT signal requires a bluster score below **−0.60** (tightened from −0.35) combined with low policy support.

SHORT directional score = weighted blend: 40% bluster magnitude + 60% policy score (was `max(abs(bluster), policy)` — prevents pure rhetoric from producing full-magnitude SHORT).

### Policy Score (0.0 to 1.0)
Measures the market-moving weight of the event type.

| Event Type | Base Score |
|---|---|
| Monetary Policy (Fed, central banks) | 0.82 |
| Regulatory | 0.74 |
| Fiscal (spending, taxes) | 0.68 |
| Trade Policy (tariffs, sanctions) | 0.72 |
| Geopolitical | 0.58 |
| Macro Data (CPI, jobs) | 0.52 |
| Earnings | 0.44 |
| Sector News | 0.36 |
| Noise | 0.08 |
| Unknown | 0.30 |

Modifiers:
- **Unconfirmed event:** base × 0.65 (was 0.48 — raised to reduce auto-SHORT on unconfirmed policy)
- **Irrelevant to the tracked symbol:** score × 0.18
- Signal requires policy score ≥ **0.40** to trigger LONG or SHORT

### Confidence Score (0.28 to 0.91)
Reflects how much trust to put in the signal.

```
base = 0.40 + (source_count / 10) × 0.38
if irrelevant: base -= 0.18
if unconfirmed: base -= 0.08
confidence = clamp(base, 0.28, 0.91)
```

Confidence is further modified by **technical indicator alignment** when enabled (default on). Each of RSI(14), SMA50/200 crossover, MACD, Volume Profile, Bollinger Bands %B, and OBV trend can adjust confidence by up to ±0.15 total. Modifiers are configured in `logic_config.json → technical_confidence`.

---

## Red Team Override

After the blue team generates a signal, a red team review challenges it. The red team must meet a higher bar to *overturn* the blue team than to *confirm* it.

**Red team confidence:**
```
base = 0.58
if agrees with blue team: +0.08
if disagrees:            −0.22
per evidence item:       +0.024 (capped at +0.12)
per risk item:           −0.028 (capped at −0.14)
if source bias flagged:  −0.10
```

**Override is allowed only when:**
- Red agrees with blue → always allowed
- Red recommends HOLD → confidence ≥ 0.58 AND (≥2 evidence OR ≥2 risks)
- Red recommends opposite direction → confidence ≥ 0.64 AND evidence ≥ 3 AND evidence > risks AND no source bias

Minimum evidence counts required for overrides are stated in the red team prompt so the model doesn't waste reasoning on overrides Python will silently discard.

**Red team stop loss by urgency:**

| Urgency | Stop Loss |
|---|---|
| HIGH | 3.5% |
| MEDIUM | 2.5% |
| LOW | 1.8% |

---

## Leverage Selection

Based on the configured risk profile:

| Profile | Bullish leverage | Bearish leverage |
|---|---|---|
| Conservative | 1x | Inverse ETF (no shorting) |
| Moderate | 2x if confidence > 0.75, else 1x | Same |
| Aggressive | 3x if confidence > 0.75, else 1x | Same |
| Crazy | 3x always | Same |

### ATR-Based Volatility Caps

Regardless of risk profile, leverage is capped by the 14-day ATR %:

- **ATR % ≥ 3.0 (high vol):** capped at 1x
- **ATR % ≥ 1.5 (medium vol):** capped at 2x
- **ATR % < 1.5 (low vol):** full profile leverage applies

Conservative profile (inverse ETF) is unaffected by ATR caps.

---

## Conviction & Trade Type

Each signal is assigned a conviction level and trade type based on its directional score and confidence. A **holding window** is opened when a position is entered — the position is protected from premature closure until the window expires.

| Signal score | Confidence | Conviction | Trade Type | Default window |
|---|---|---|---|---|
| > 0.6 | > 0.7 | HIGH | POSITION | 72 hrs (4320 min) |
| Most cases | Any | MEDIUM | SWING | 12 hrs (720 min) |
| HOLD signal, or high urgency + low confidence | — | LOW | VOLATILE_EVENT | 2 hrs (120 min) |

### Window behavior (configurable in `logic_config.json → conviction`)

| Setting | Default | Description |
|---|---|---|
| `hold_signal_respects_window` | `true` | HOLD signal is blocked from closing while window is active |
| `flip_overrides_window` | `true` | Direction flip always closes and reopens, ignoring the window |
| `close_on_window_expiry` | `true` | Position auto-closes when window expires (or transitions to trailing stop if `trail_on_window_expiry` is enabled) |
| `close_expired_during_closed_hours` | `true` | Expired positions close even when market is closed |

All timing values are in **minutes** (`holding_minutes`) so SCALP and SWING trades can be tuned at minute granularity.

---

## Accumulation on Re-Confirmation

When `accumulate_on_confirmation` is enabled (default: on), re-confirmed signals (same ticker, same leverage, same direction) **add additional shares** instead of simply holding. This allows the system to build larger positions when multiple analysis runs confirm the same thesis.

### How It Works

1. On the first entry, `original_amount` is recorded on the PaperTrade row.
2. On each subsequent re-confirmation, the system computes what a **fresh entry** would be using the current signal's `size_pct` (from continuous entry sigmoid) and volatility-normalized sizing.
3. If the fresh entry amount exceeds the current position amount, the difference is added as additional shares.
4. The entry price is **blended** (weighted average of old and new prices).
5. The position is capped at `max_multiplier × original_amount` (default: 5×).

### Caps (in order of application)

| Cap | Source | Description |
|---|---|---|
| `max_multiplier` | `accumulate_on_confirmation.max_multiplier` in logic_config.json (or Admin UI) | Never exceed this multiple of the original entry amount |
| `alpaca_max_position_usd` | Admin → Alpaca settings | Per-position dollar cap |
| Portfolio cap | `vol_sizing.portfolio_cap_usd` | Total open exposure across all symbols |

### When Accumulation Does NOT Happen

- Signal strength is declining (new `size_pct` < current position allocation)
- Position has already hit the max multiplier cap
- Alpaca max position cap would be exceeded
- Portfolio cap would be exceeded
- Accumulation is disabled via Admin UI toggle
- Opening range guard is active (see [Opening Range (ORB) Guard](#opening-range-orb-guard))

### Configuration

| Setting | Default | Admin-editable |
|---|---|---|
| `accumulate_on_confirmation_enabled` | `true` | Yes (Admin → Trading Logic) |
| `accumulate_max_multiplier` | `5.0` | Yes (Admin → Trading Logic) |

---

## Opening Range (ORB) Guard

To avoid trading into the market-opening bump, new exposure is gated for the first minutes of the regular session and then checked against the day's opening range. Configured in `logic_config.json → opening_range` (values read from the JSON file only — no Admin UI field yet).

| Setting | Default | Description |
|---|---|---|
| `enabled` | `true` | Master toggle for the whole guard |
| `wait_minutes` | `15` | No NEW exposure during the first N minutes after 9:30 ET |
| `range_minutes` | `15` | Opening range = high/low of the first N minutes of 5m bars |
| `min_break_pct` | `0.2` | % beyond the range edge before a move counts as a "break" |
| `high_override` | `true` | HIGH-conviction signals may enter against the break |

### How It Works

1. **Wait phase** — for `wait_minutes` after 9:30 ET, new entries, direction-flip re-entries, and accumulation are skipped. Existing positions are untouched: stop-loss, take-profit, trailing stops, HOLD closes, and flip closes all still run.
2. **Range phase** — once the opening range has formed (first `range_minutes` of 5m bars, fetched per symbol/day and cached), the current price is classified as `above`, `below`, or `inside` the range (using `min_break_pct` as a buffer band).
3. **Follow the break** — entries whose direction is *against* the break are skipped (price broke above → no new SHORT, and vice versa), unless `high_override` allows HIGH conviction through. Price inside the range = neutral, no block.

### Fail-Open & Scope

- Missing/incomplete price data never blocks trading (fail-open).
- Applies to the regular session only — pre-market, after-hours, and overnight are never gated.
- Skipped signals log `reason: opening_wait | opening_range_against` with an `opening_range` payload (range high/low, price side, minutes since open).

---

## Position Sizing

### Volatility-Normalized Sizing (Default)

Position size is computed from a 1% daily volatility target:

```
size = (target_daily_vol_pct × base_amount) / ATR_14d_pct
size × conviction_scalar  →  final trade size
```

- **Conviction scalars:** HIGH = 1.5×, MEDIUM = 1.0×, LOW = 0.5×
- **Clamped to:** 0.25×–5.0× the configured base amount
- High-volatility assets automatically receive smaller positions
- Falls back to conviction-scaled base amount when ATR is unavailable

### Fixed Amount (Optional)

When `alpaca_fixed_order_size` is enabled in Admin, every trade uses exactly the configured baseline dollar amount regardless of volatility or conviction.

### Portfolio Cap

The **Portfolio Cap ($)** in Admin limits total open notional exposure across all symbols simultaneously. When the cap is reached, new trade opens are scaled down or skipped until capacity frees up.

---

## Paper Trading

The paper trading simulation auto-executes a volatility-normalized trade per signal during extended market hours (Mon–Fri, 4:00 AM – 8:00 PM ET).

- **Re-entry cooldown:** 120 minutes — blocks same-direction re-entry in the same symbol after a close
- **Entry confirmation:** `entry_confirmation.runs_required` (default 3) — a NEW position or direction flip requires the same direction on N consecutive analysis runs. One score blip (e.g. a single 0.74 spike) can no longer re-open a position you just closed at the top of a rally. HIGH-conviction flips and same-direction ticker/leverage upgrades are exempt (thesis continuations).
- **No flip-back cooldown:** `reentry_no_flip_minutes` (default 60) — blocks flipping back to the opposite direction within N minutes of a close.
- **Same-day no-rebuy:** `same_day_no_rebuy_minutes` (default 60) — if you just closed a position and the new entry price is within ~0.35% of that exit, it's treated as a churn round-trip and skipped.
- **Trailing-stop warmup:** `trailing_stop.warmup_minutes` (default 30) + `min_favorable_move_pct` (0.5) — a trailing stop is not armed until the position is 30 min old (or has already moved ≥0.5% favorably). Prevents the trail from firing on opening noise and forcing a same-price round-trip.
- **Opening range guard:** no new exposure during the first 15 minutes after the open, then blocks entries against the opening break (see [Opening Range (ORB) Guard](#opening-range-orb-guard))
- **Same-day exit filter:** `min_same_day_exit_edge_pct` (default 0.5%) — same-day winners below this threshold are held instead of closed
- Each unique symbol gets one open position at a time
- P&L is tracked using directional math: shorts profit from price declines
- Equity curve and win/loss stats are shown on the Trading page

---

## Customizing These Parameters

### Admin UI (easiest)
Go to **Admin → Trading Logic** to change:
- Paper trade amount
- Entry threshold
- Stop loss %
- Take profit %
- Materiality gate — min new articles and min sentiment delta
- Portfolio cap
- Continuous entry sizing toggle
- Regime adaptation toggle
- Hold decay toggle
- Trail on window expiry toggle
- Re-entry cooldown
- Same-day exit edge %
- Order sizing mode (vol-normalized or fixed)

Leave a field blank to revert to the system default.

### JSON Config (power users)
Edit `backend/config/logic_config.json` and restart the backend. All scoring weights, red-team thresholds, holding period defaults, leverage caps, decay half-lives, conviction window settings, technical confidence modifiers, trailing stop behavior, ATR bounds, and the opening range guard live here. The JSON ships with defaults that match the current production values.

Admin UI values take precedence over JSON values for the fields listed above.
