# Test Plan: Alpha IC & Perturbation Bug Fix Verification

## Context
**Bug Fixed:** `/api/v1/alpha/ic` and `/api/v1/alpha/perturbation` endpoints were joining `trade_snapshots` to the wrong table (`paper_trades` instead of `trades`), causing forward-return data to never match up.

**Files Changed:**
- `backend/routers/alpha.py`
- `backend/services/analysis/perturbation.py`

**Date:** 2026-05-28

---

## Pre-Test Setup

### 1. Restart Backend Server
```bash
# Navigate to project directory
cd "C:/Users/Jeff Eberhard/github/Sentiment Trading Alpha"

# Kill existing uvicorn process (if running)
# Windows:
taskkill /f /im uvicorn.exe
# Or find and kill the process
Get-Process uvicorn* | Stop-Process -Force

# Restart backend with auto-reload
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Verify backend is running:**
- Check terminal output for "Application startup complete"
- Or curl: `curl http://localhost:8000/health` (if health endpoint exists)

---

## Test Cases

### Test 1: Information Coefficient (IC) Endpoint

**Endpoint:** `GET /api/v1/alpha/ic`

**Command:**
```bash
curl -X GET "http://localhost:8000/api/v1/alpha/ic?horizons=4h,1d,3d,1w&window=30" \
  -H "Content-Type: application/json" \
  | python -m json.tool
```

**Expected Results:**
- `pairs_count` should be > 0 if any `trades` rows have matching `trade_snapshots` with non-null `blended_confidence_score` in `decision_log_symbol`
- Response should include IC values for each horizon (4h, 1d, 3d, 1w)

**If `pairs_count` is 0, debug with:**
```bash
# Check if there's snapshot data at all
sqlite3 trading_system.db "SELECT count(*) FROM trades t JOIN trade_snapshots ts ON ts.trade_id = t.id;"

# Check trades table
sqlite3 trading_system.db "SELECT count(*) FROM trades;"

# Check trade_snapshots table
sqlite3 trading_system.db "SELECT count(*) FROM trade_snapshots;"

# Check for non-null blended_confidence_score
sqlite3 trading_system.db "SELECT count(*) FROM decision_log_symbol WHERE blended_confidence_score IS NOT NULL;"
```

**Interpretation:**
- If the JOIN query returns 0 rows → data problem (not code). Horizons need time to elapse after trades are recommended before snapshots get written.
- If JOIN query returns > 0 rows but `pairs_count` is still 0 → code fix may not be working correctly.

---

### Test 2: Perturbation Analysis Endpoint

**Endpoint:** `POST /api/v1/alpha/perturbation`

**Command:**
```bash
curl -X POST "http://localhost:8000/api/v1/alpha/perturbation" \
  -H "Content-Type: application/json" \
  -d '{"nudge_pct": 0.10, "symbol": null, "horizons": ["4h","1d","3d","1w"]}' \
  | python -m json.tool
```

**Expected Results:**
- `signal_count` should be > 0 (if there are trades with snapshots)
- `avg_return_by_horizon` should contain non-null numbers for at least some horizons in:
  - `baseline`
  - `nudge_up`
  - `nudge_down`
- Each horizon entry should have a numeric value (not `null` or `"—"`)

**Success Criteria:**
- Response contains actual percentage values (e.g., `0.0234` for 2.34%) instead of `null`
- All three scenarios (baseline, nudge_up, nudge_down) return data

---

### Test 3: Frontend Alpha Dashboard

**URL:** `http://localhost:3000/alpha`

**Prerequisite:** Ensure frontend is running
```bash
# In another terminal
cd "C:/Users/Jeff Eberhard/github/Sentiment Trading Alpha/frontend"
npm run dev
```

**Visual Verification Checklist:**

#### IC Summary Chips
- [ ] IC values should show actual numbers (not "—")
- [ ] Values should update based on available data
- [ ] Multiple horizons should display if data exists

#### Rolling IC Chart
- [ ] Chart should render with data points (not empty)
- [ ] X-axis should show time periods
- [ ] Y-axis should show IC values
- [ ] Line/area should be visible (not blank chart)

#### Confidence vs Return Scatter Plot
- [ ] Scatter plot should render points
- [ ] X-axis: confidence scores
- [ ] Y-axis: forward returns
- [ ] Points should be visible (not empty plot)

#### Sensitivity/Perturbation Test Section
- [ ] Click "Run" button
- [ ] Three scenario cards should appear:
  - **Baseline**
  - **Nudge Up (+10%)**
  - **Nudge Down (-10%)**
- [ ] Each card should show:
  - [ ] Actual percentage values per horizon (4h, 1d, 3d, 1w)
  - [ ] Not "—" placeholders
  - [ ] Numeric values that make sense (e.g., -1.0 to 1.0 range for IC)

---

### Test 4: Regression Check - Attribution Endpoint

**Endpoint:** `GET /api/v1/alpha/attribution`

**Command:**
```bash
curl -X GET "http://localhost:8000/api/v1/alpha/attribution?limit=200" \
  -H "Content-Type: application/json" \
  | python -m json.tool
```

**Expected Results:**
- Endpoint should still work (it wasn't touched in this fix)
- Response should include:
  - `by_event_type`: array of objects with event types and metrics
  - `top_terms`: array of objects with terms and metrics
- Structure should match previous behavior

**Success Criteria:**
- No 500 errors
- Response format unchanged
- Data returned (if available)

---

## Automated Test Script (Optional)

Save as `test_alpha_fix.sh` (Linux/Mac) or `test_alpha_fix.ps1` (Windows):

**PowerShell version:**
```powershell
# test_alpha_fix.ps1
Write-Host "=== Alpha IC & Perturbation Fix Verification ===" -ForegroundColor Cyan

# Test 1: IC Endpoint
Write-Host "`n[Test 1] Testing IC endpoint..." -ForegroundColor Yellow
$icResponse = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/alpha/ic?horizons=4h,1d,3d,1w&window=30" -Method Get
Write-Host "Pairs count: $($icResponse.pairs_count)"
if ($icResponse.pairs_count -gt 0) {
    Write-Host "✓ IC endpoint working - pairs found" -ForegroundColor Green
} else {
    Write-Host "⚠ IC endpoint returned 0 pairs (may be data issue)" -ForegroundColor Yellow
}

# Test 2: Perturbation Endpoint
Write-Host "`n[Test 2] Testing Perturbation endpoint..." -ForegroundColor Yellow
$perturbBody = '{"nudge_pct": 0.10, "symbol": null, "horizons": ["4h","1d","3d","1w"]}'
$perturbResponse = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/alpha/perturbation" -Method Post -Body $perturbBody -ContentType "application/json"
Write-Host "Signal count: $($perturbResponse.signal_count)"
if ($perturbResponse.signal_count -gt 0) {
    Write-Host "✓ Perturbation endpoint working - signals found" -ForegroundColor Green
} else {
    Write-Host "⚠ Perturbation endpoint returned 0 signals" -ForegroundColor Yellow
}

# Test 3: Attribution Endpoint (regression)
Write-Host "`n[Test 3] Testing Attribution endpoint (regression)..." -ForegroundColor Yellow
$attrResponse = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/alpha/attribution?limit=200" -Method Get
Write-Host "✓ Attribution endpoint still working" -ForegroundColor Green

Write-Host "`n=== Verification Complete ===" -ForegroundColor Cyan
```

Run with: `.\test_alpha_fix.ps1`

---

## Troubleshooting

### Backend won't start
- Check for syntax errors in modified files
- Verify virtual environment is activated
- Check port 8000 is not in use

### Endpoints return 500 errors
- Check backend terminal for stack traces
- Verify database file exists and is accessible
- Check that `trades` table has the expected schema

### Frontend shows "—" or empty charts
- Open browser DevTools (F12) → Network tab
- Check API calls are returning data (not 404/500)
- Verify frontend is calling correct endpoints

### No data in database
- Run the sentiment pipeline to generate trades
- Wait for horizon periods to elapse (4h, 1d, 3d, 1w) after trade recommendations
- Check if `trade_snapshots` are being written by the snapshot service

---

## Success Criteria Summary

- [ ] Backend restarts without errors
- [ ] IC endpoint returns `pairs_count > 0` (or explains why 0)
- [ ] Perturbation endpoint returns non-null values in `avg_return_by_horizon`
- [ ] Frontend Alpha dashboard shows actual values (not "—")
- [ ] Perturbation scenario cards display real percentages
- [ ] Attribution endpoint still works (regression check)
- [ ] No new errors in backend or frontend logs

---

## Notes

- If `pairs_count` is 0, this is likely a **data availability issue**, not a code bug
- The fix changes which table is joined (`trades` vs `paper_trades`), so verify the correct table is being queried
- Horizon data (4h, 1d, 3d, 1w) requires time to elapse after trade generation
- Check `REFERENCE.md` for database schema details if needed
