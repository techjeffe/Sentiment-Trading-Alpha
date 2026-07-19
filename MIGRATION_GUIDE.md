# Docker Migration Guide

## Overview
If you have an existing Sentiment Trading Alpha installation (databases, secrets in keyring) and want to migrate to Docker, use the provided migration scripts.

## Prerequisites
- Docker and Docker Compose installed
- Existing databases (`trading_system.db`, `decision_log.db`) in project root
- Python with `keyring` package installed (for secret export)

## Quick Start

### macOS / Linux
```bash
# 1. Make script executable (first time only)
chmod +x migrate-to-docker.sh

# 2. Run migration
./migrate-to-docker.sh
```

### Windows (PowerShell)
```powershell
# 1. Open PowerShell as Administrator
# 2. Run migration
.\migrate-to-docker.ps1
```

## What the Script Does

1. **Detects existing databases** - Looks for `trading_system.db` and `decision_log.db` in project root
2. **Exports keyring secrets** - Reads your existing secrets (Alpaca API keys, Telegram tokens, etc.) from your OS keyring and adds them to `.env` file
3. **Stops Docker containers** - If already running
4. **Copies databases to Docker volume** - Imports your existing data
5. **Starts Docker** - With all your migrated data

## After Migration

### Verify It Worked
```bash
# Check container is running
docker ps

# View container logs
docker compose logs -f

# Check databases were copied
docker exec -it sentiment-trading ls -la /data
```

### Access Your System
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000/docs
- **All your existing data** should be there (trades, config, etc.)

## Keyring Secrets Migration

The script exports these secrets from your OS keyring to `.env`:
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_AUTHORIZED_USER_ID`
- `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_SECRET_KEY`
- `ALPACA_LIVE_API_KEY`, `ALPACA_LIVE_SECRET_KEY`
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `GOOGLE_API_KEY`

**Note**: Your original keyring data is **not deleted** - the script only reads and copies to `.env`.

## Troubleshooting

### "Docker is not running"
Start Docker Desktop (Windows/Mac) or Docker daemon (Linux).

### "No existing databases found"
The script will still work - Docker will create fresh databases. You can copy databases manually later:
```bash
docker cp trading_system.db sentiment-trading:/data/trading_system.db
docker cp decision_log.db sentiment-trading:/data/decision_log.db
```

### "Python not found" or "keyring not installed"
Secrets export will be skipped. You'll need to manually add your API keys to `.env`:
```bash
# Edit .env and add your keys
nano .env
# or on Windows: notepad .env
```

### Permission errors (Linux/Mac)
```bash
# Make sure you own the .env file
chmod 644 .env
chmod +x migrate-to-docker.sh
```

### Windows execution policy error
```powershell
# Allow script execution (run as Administrator)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

## Manual Migration (Without Script)

If the script doesn't work, you can migrate manually:

### 1. Export Databases
```bash
# Start Docker temporarily
docker compose up -d

# Copy databases
docker cp trading_system.db sentiment-trading:/data/trading_system.db
docker cp decision_log.db sentiment-trading:/data/decision_log.db

# Restart
docker compose restart
```

### 2. Export Secrets
```bash
# List all secrets in keyring
python -c "import keyring; print(keyring.get_password('qwen-3.5-9b-getrich', 'openai_api_key'))"

# Manually add to .env file
echo "OPENAI_API_KEY=your-key-here" >> .env
```

## Reverting Migration

If something goes wrong:
1. Stop Docker: `docker compose down`
2. Restore `.env` backup: `cp .env.backup.* .env`
3. Remove Docker volumes: `docker volume rm sentimenttradingalpha_trading-data`
4. Restart fresh: `docker compose up --build`

## Cross-Platform Notes

### macOS
- Uses macOS Keychain for keyring (secrets export should work automatically)
- Docker Desktop for Mac required
- Script tested on macOS 10.15+

### Windows
- Uses Windows Credential Manager for keyring
- Docker Desktop for Windows required
- Run PowerShell as Administrator for best results
- Paths use forward slashes (Docker-compatible)

### Linux
- Uses secretstorage/libsecret for keyring
- May need to install: `sudo apt-get install python3-keyring libsecret-1-0`
- Script tested on Ubuntu 20.04+

## Support

If you encounter issues:
1. Check `docker compose logs` for errors
2. Verify `.env` file has all required variables
3. Ensure databases are valid SQLite files: `sqlite3 trading_system.db "SELECT COUNT(*) FROM trades;"`
