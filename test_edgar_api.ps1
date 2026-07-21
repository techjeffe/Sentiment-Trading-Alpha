# Test EDGAR API Endpoints
# Run this AFTER restarting the backend server

Write-Host "=" * 60
Write-Host "Testing EDGAR API Endpoints"
Write-Host "=" * 60
Write-Host ""

# Test 1: GET /api/v1/edgar/filings
Write-Host "Test 1: GET /api/v1/edgar/filings"
try {
    $resp = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/edgar/filings" -Method GET
    Write-Host "  [OK] Status: 200"
    Write-Host "  Total filings: $($resp.total)"
    if ($resp.filings -and $resp.filings.Count -gt 0) {
        Write-Host "  First filing: $($resp.filings[0].symbol) $($resp.filings[0].form_type)"
    }
} catch {
    Write-Host "  [FAIL] $_"
}
Write-Host ""

# Test 2: GET /api/v1/edgar/config
Write-Host "Test 2: GET /api/v1/edgar/config"
try {
    $resp = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/edgar/config" -Method GET
    Write-Host "  [OK] Status: 200"
    Write-Host "  Enabled: $($resp.enabled)"
    Write-Host "  Poll interval: $($resp.poll_interval_minutes) minutes"
} catch {
    Write-Host "  [FAIL] $_"
}
Write-Host ""

# Test 3: POST /api/v1/edgar/poll
Write-Host "Test 3: POST /api/v1/edgar/poll"
try {
    $resp = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/edgar/poll" -Method POST
    Write-Host "  [OK] Status: 200"
    Write-Host "  Message: $($resp.message)"
    Write-Host "  New filings stored: $($resp.summary.filings_stored)"
} catch {
    Write-Host "  [FAIL] $_"
}
Write-Host ""

# Test 4: POST /api/v1/edgar/process
Write-Host "Test 4: POST /api/v1/edgar/process?limit=2"
try {
    $resp = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/edgar/process?limit=2" -Method POST
    Write-Host "  [OK] Status: 200"
    Write-Host "  Message: $($resp.message)"
    Write-Host "  Summaries generated: $($resp.summary.summaries_generated)"
} catch {
    Write-Host "  [FAIL] $_"
}
Write-Host ""

Write-Host "=" * 60
Write-Host "Tests complete!"
Write-Host "=" * 60
