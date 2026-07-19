# ── migrate-to-docker.ps1 ──────────────────────────────────────────────────
# One-time migration script: Import existing databases and secrets into Docker
#
# Usage:
#   .\migrate-to-docker.ps1
#
# What it does:
#   1. Stops Docker containers if running
#   2. Copies existing SQLite databases into Docker volume
#   3. Exports keyring secrets to .env file for Docker to use
#   4. Restarts Docker with the imported data
#
# Prerequisites:
#   - Docker and Docker Compose installed
#   - Existing databases in project root (trading_system.db, decision_log.db)
#   - Python with keyring package (for secret export)
# ─────────────────────────────────────────────────────────────────────────────

Write-Host "╔══════════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   Sentiment Trading Alpha — Migrate to Docker                        ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Check for existing databases ────────────────────────────────────
Write-Host "Step 1: Checking for existing databases..." -ForegroundColor Yellow

$DB_FOUND = $false
if (Test-Path "trading_system.db") {
    Write-Host "  Found trading_system.db"
    $DB_FOUND = $true
}
if (Test-Path "decision_log.db") {
    Write-Host "  Found decision_log.db"
    $DB_FOUND = $true
}

if (-not $DB_FOUND) {
    Write-Host "  No existing databases found in project root." -ForegroundColor Yellow
    Write-Host "  Docker will create fresh databases on first run."
}

# ── Step 2: Export keyring secrets ──────────────────────────────────────────
Write-Host ""
Write-Host "Step 2: Exporting keyring secrets (if any)..." -ForegroundColor Yellow

$EXPORT_ENV_FILE = ".env.docker.migrated"
$SECRETS_FOUND = $false

# Create/truncate the export file
"" | Out-File -FilePath $EXPORT_ENV_FILE -NoNewline

# Check if export_secrets.py exists
if (Test-Path "export_secrets.py") {
    # Try to export secrets using the external Python script
    $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCmd) {
        $PythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
    }

    if ($PythonCmd) {
        Write-Host "  Using Python: $($PythonCmd.Name)"

        # Run the export script
        & $PythonCmd.Name "export_secrets.py" 2>&1 | Write-Host

        if (Test-Path $EXPORT_ENV_FILE) {
            $Content = Get-Content $EXPORT_ENV_FILE -Raw
            if ($Content -and $Content.Trim().Length -gt 0) {
                $SECRETS_FOUND = $true
                Write-Host "  Secrets exported to $EXPORT_ENV_FILE" -ForegroundColor Green
            }
        }
    } else {
        Write-Host "  Python not found. Skipping secret export." -ForegroundColor Yellow
        Write-Host "  You'll need to manually add API keys to .env file." -ForegroundColor Yellow
    }
} else {
    Write-Host "  export_secrets.py not found. Skipping secret export." -ForegroundColor Yellow
}

# ── Step 3: Merge secrets into .env ─────────────────────────────────────────
Write-Host ""
Write-Host "Step 3: Merging secrets into .env file..." -ForegroundColor Yellow

if ($SECRETS_FOUND) {
    # Backup existing .env
    if (Test-Path ".env") {
        $BackupName = ".env.backup." + (Get-Date -Format "yyyyMMdd_HHmmss")
        Copy-Item ".env" $BackupName
        Write-Host "  Backed up existing .env file to $BackupName" -ForegroundColor Green
    }

    # Copy .env.example if no .env exists
    if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
        Copy-Item ".env.example" ".env"
        Write-Host "  Created .env from .env.example" -ForegroundColor Green
    }

    # Merge exported secrets into .env (only if not already set)
    Get-Content $EXPORT_ENV_FILE | ForEach-Object {
        if ($_ -match "^([^=]+)=(.*)$") {
            $Key = $Matches[1]
            $Value = $Matches[2]

            if ($Key -and $Value) {
                # Check if key already exists in .env
                if (Select-String -Path ".env" -Pattern "^$Key=" -Quiet) {
                    Write-Host "  $Key already exists in .env (keeping existing)" -ForegroundColor Yellow
                } else {
                    Add-Content -Path ".env" -Value "$Key=$Value"
                    Write-Host "  Added $Key to .env" -ForegroundColor Green
                }
            }
        }
    }

    # Clean up temp file
    Remove-Item $EXPORT_ENV_FILE -ErrorAction SilentlyContinue
}

# ── Step 4: Stop Docker if running ──────────────────────────────────────────
Write-Host ""
Write-Host "Step 4: Stopping Docker containers (if running)..." -ForegroundColor Yellow

# Try both docker compose syntaxes (newer vs older Docker versions)
$COMPOSE_CMD = ""
if (docker compose version 2>$null) {
    $COMPOSE_CMD = "docker compose"
} elseif (docker-compose version 2>$null) {
    $COMPOSE_CMD = "docker-compose"
} else {
    Write-Host "  Docker Compose not found. Please install Docker Compose." -ForegroundColor Red
    exit 1
}

Write-Host "  Using: $COMPOSE_CMD" -ForegroundColor Gray

# Stop containers if running
$ContainersRunning = Invoke-Expression "$COMPOSE_CMD ps -q" 2>$null
if ($ContainersRunning) {
    Invoke-Expression "$COMPOSE_CMD down"
    Write-Host "  Docker containers stopped" -ForegroundColor Green
} else {
    Write-Host "  No running containers found"
}

# ── Step 5: Copy databases into Docker volume ───────────────────────────────
Write-Host ""
Write-Host "Step 5: Copying databases into Docker volume..." -ForegroundColor Yellow

if ($DB_FOUND) {
    # Ensure Docker is running
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Docker is not running. Please start Docker Desktop first." -ForegroundColor Red
        exit 1
    }

    # Start containers temporarily to ensure volume exists
    Write-Host "  Ensuring Docker volume exists..."
    Invoke-Expression "$COMPOSE_CMD up -d --no-build" 2>$null
    Start-Sleep -Seconds 5

    # Copy databases using docker cp
    if (Test-Path "trading_system.db") {
        Write-Host "  Copying trading_system.db..."
        docker cp trading_system.db sentiment-trading:/data/trading_system.db 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  Copied trading_system.db to Docker volume" -ForegroundColor Green
        } else {
            Write-Host "  Failed to copy trading_system.db" -ForegroundColor Red
        }
    }

    if (Test-Path "decision_log.db") {
        Write-Host "  Copying decision_log.db..."
        docker cp decision_log.db sentiment-trading:/data/decision_log.db 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  Copied decision_log.db to Docker volume" -ForegroundColor Green
        } else {
            Write-Host "  Failed to copy decision_log.db" -ForegroundColor Red
        }
    }

    # Stop temporary containers
    Invoke-Expression "$COMPOSE_CMD down" 2>$null
} else {
    Write-Host "  No databases to copy"
}

# ── Step 6: Start Docker with migrated data ─────────────────────────────────
Write-Host ""
Write-Host "Step 6: Starting Docker with migrated data..." -ForegroundColor Yellow

Invoke-Expression "$COMPOSE_CMD up -d --build"

# Wait for container to be ready
Write-Host ""
Write-Host "Waiting for container to start..."
$RetryCount = 0
while ($RetryCount -lt 15) {
    $ContainerRunning = docker ps | Select-String "sentiment-trading"
    if ($ContainerRunning) {
        Write-Host "  Container is running" -ForegroundColor Green
        break
    }
    Start-Sleep -Seconds 2
    $RetryCount++
}

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   Migration Complete!                                                ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Green
Write-Host "  1. Check container logs: docker compose logs -f"
Write-Host "  2. Access frontend: http://localhost:3000"
Write-Host "  3. Access backend API: http://localhost:8000/docs"
Write-Host ""
Write-Host "If you had existing secrets in keyring:" -ForegroundColor Yellow
Write-Host "  - They've been added to your .env file"
Write-Host "  - Docker will use these instead of keyring"
Write-Host "  - Your original keyring data is unchanged"
Write-Host ""
Write-Host "To verify migration:" -ForegroundColor Green
Write-Host "  docker exec -it sentiment-trading ls -la /data"
Write-Host ""
