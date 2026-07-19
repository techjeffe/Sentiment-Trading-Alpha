# ── migrate-to-docker.ps1 ──────────────────────────────────────────────────
# One-time migration script: Import existing databases and secrets into Docker

Write-Host "============================================"
Write-Host "Sentiment Trading Alpha - Migrate to Docker"
Write-Host "============================================"
Write-Host ""

# Step 1: Check for existing databases
Write-Host "Step 1: Checking for existing databases..."

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
    Write-Host "  No existing databases found." -ForegroundColor Yellow
}

# Step 2: Export keyring secrets
Write-Host ""
Write-Host "Step 2: Exporting keyring secrets (if any)..."

$EXPORT_ENV_FILE = ".env.docker.migrated"
$SECRETS_FOUND = $false

"" | Out-File -FilePath $EXPORT_ENV_FILE -NoNewline

if (Test-Path "export_secrets.py") {
    $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCmd) {
        $PythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
    }

    if ($PythonCmd) {
        Write-Host "  Using Python: $($PythonCmd.Name)"
        & $PythonCmd.Name "export_secrets.py" 2>&1 | Write-Host
        
        if (Test-Path $EXPORT_ENV_FILE) {
            $Content = Get-Content $EXPORT_ENV_FILE -Raw
            if ($Content -and $Content.Trim().Length -gt 0) {
                $SECRETS_FOUND = $true
                Write-Host "  Secrets exported to $EXPORT_ENV_FILE" -ForegroundColor Green
            } else {
                Write-Host "  No secrets found in keyring" -ForegroundColor Yellow
            }
        }
    } else {
        Write-Host "  Python not found. Skipping secret export." -ForegroundColor Yellow
    }
} else {
    Write-Host "  export_secrets.py not found." -ForegroundColor Yellow
}

# Step 3: Merge secrets into .env
Write-Host ""
Write-Host "Step 3: Merging secrets into .env file..."

if ($SECRETS_FOUND) {
    if (Test-Path ".env") {
        $BackupName = ".env.backup." + (Get-Date -Format "yyyyMMdd_HHmmss")
        Copy-Item ".env" $BackupName
        Write-Host "  Backed up .env to $BackupName" -ForegroundColor Green
    }

    if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
        Copy-Item ".env.example" ".env"
        Write-Host "  Created .env from .env.example" -ForegroundColor Green
    }

    Get-Content $EXPORT_ENV_FILE | ForEach-Object {
        if ($_ -match "^([^=]+)=(.*)$") {
            $Key = $Matches[1]
            $Value = $Matches[2]

            if ($Key -and $Value) {
                if (Select-String -Path ".env" -Pattern "^$Key=" -Quiet) {
                    Write-Host "  $Key already exists in .env (keeping existing)" -ForegroundColor Yellow
                } else {
                    Add-Content -Path ".env" -Value "$Key=$Value"
                    Write-Host "  Added $Key to .env" -ForegroundColor Green
                }
            }
        }
    }

    Remove-Item $EXPORT_ENV_FILE -ErrorAction SilentlyContinue
}

# Step 4: Stop Docker if running
Write-Host ""
Write-Host "Step 4: Stopping Docker containers..."

$COMPOSE_CMD = ""
if (docker compose version 2>$null) {
    $COMPOSE_CMD = "docker compose"
} elseif (docker-compose version 2>$null) {
    $COMPOSE_CMD = "docker-compose"
} else {
    Write-Host "  Docker Compose not found." -ForegroundColor Red
    exit 1
}

$ContainersRunning = Invoke-Expression "$COMPOSE_CMD ps -q" 2>$null
if ($ContainersRunning) {
    Invoke-Expression "$COMPOSE_CMD down"
    Write-Host "  Docker containers stopped" -ForegroundColor Green
}

# Step 5: Copy databases into Docker volume
Write-Host ""
Write-Host "Step 5: Copying databases into Docker volume..."

if ($DB_FOUND) {
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Docker is not running. Please start Docker." -ForegroundColor Red
        exit 1
    }

    Write-Host "  Starting Docker temporarily..."
    Invoke-Expression "$COMPOSE_CMD up -d --no-build" 2>$null
    Start-Sleep -Seconds 5

    # Copy databases - include WAL files
    if (Test-Path "trading_system.db") {
        Write-Host "  Copying trading_system.db..."
        docker cp trading_system.db sentiment-trading:/data/trading_system.db 2>$null
        if (Test-Path "trading_system.db-wal") {
            docker cp trading_system.db-wal sentiment-trading:/data/ 2>$null
        }
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  Copied trading_system.db" -ForegroundColor Green
        } else {
            Write-Host "  WARNING: Failed to copy trading_system.db" -ForegroundColor Yellow
        }
    }

    if (Test-Path "decision_log.db") {
        Write-Host "  Copying decision_log.db..."
        docker cp decision_log.db sentiment-trading:/data/decision_log.db 2>$null
        if (Test-Path "decision_log.db-wal") {
            docker cp decision_log.db-wal sentiment-trading:/data/ 2>$null
        }
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  Copied decision_log.db" -ForegroundColor Green
        } else {
            Write-Host "  WARNING: Failed to copy decision_log.db" -ForegroundColor Yellow
        }
    }

    Invoke-Expression "$COMPOSE_CMD down" 2>$null
    
    Write-Host ""
    Write-Host "  IMPORTANT: If you get database errors:" -ForegroundColor Yellow
    Write-Host "    1. Stop Docker: docker compose down" -ForegroundColor Yellow
    Write-Host "    2. Remove corrupted databases" -ForegroundColor Yellow
    Write-Host "    3. Let Docker create fresh databases" -ForegroundColor Yellow
}

# Step 6: Start Docker with migrated data
Write-Host ""
Write-Host "Step 6: Starting Docker with migrated data..."
Invoke-Expression "$COMPOSE_CMD up -d --build"

Write-Host ""
Write-Host "============================================"
Write-Host "Migration Complete!"
Write-Host "============================================"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Check logs: docker compose logs -f"
Write-Host "  2. Frontend: http://localhost:3000"
Write-Host "  3. Backend: http://localhost:8000/docs"
Write-Host ""
