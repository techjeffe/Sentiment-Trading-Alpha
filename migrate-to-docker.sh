#!/bin/bash
# ── migrate-to-docker.sh ─────────────────────────────────────────────────────
# One-time migration script: Import existing databases and secrets into Docker
#
# Usage:
#   ./migrate-to-docker.sh
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
#
# Compatibility:
#   - macOS (native Bash or zsh with Bash compatibility)
#   - Linux (any distribution)
#   - Windows (Git Bash, WSL, or Cygwin)
# ─────────────────────────────────────────────────────────────────────────────

set -e  # Exit on error

# Detect script directory (works on Mac, Linux, and Windows with Git Bash)
if [[ -n "${BASH_SOURCE[0]}" ]]; then
    PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    PROJECT_DIR="$(pwd)"
fi
cd "$PROJECT_DIR"

# Detect OS for platform-specific adjustments
OS_TYPE="$(uname -s)"

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║   Sentiment Trading Alpha — Migrate to Docker                        ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Check for existing databases ────────────────────────────────────
echo "Step 1: Checking for existing databases..."

DB_FOUND=false
if [ -f "trading_system.db" ]; then
    echo "  ✓ Found trading_system.db"
    DB_FOUND=true
fi
if [ -f "decision_log.db" ]; then
    echo "  ✓ Found decision_log.db"
    DB_FOUND=true
fi

if [ "$DB_FOUND" = false ]; then
    echo "  ⚠ No existing databases found in project root."
    echo "  Docker will create fresh databases on first run."
fi

# ── Step 2: Export keyring secrets ──────────────────────────────────────────
echo ""
echo "Step 2: Exporting keyring secrets (if any)..."

EXPORT_ENV_FILE=".env.docker.migrated"
SECRETS_FOUND=false

# Create/truncate the export file
> "$EXPORT_ENV_FILE"

# Try to export secrets using the external Python script
if [[ -f "export_secrets.py" ]]; then
    # Check for Python (try multiple names for cross-platform compatibility)
    PYTHON_CMD=""
    for cmd in python3 python /usr/bin/python3 /usr/local/bin/python3; do
        if command -v "$cmd" &> /dev/null; then
            PYTHON_CMD="$cmd"
            break
        fi
    done

    if [[ -n "$PYTHON_CMD" ]]; then
        echo "  Using Python: $PYTHON_CMD"
        
        # Run the export script
        $PYTHON_CMD export_secrets.py 2>&1
        
        if [[ -s "$EXPORT_ENV_FILE" ]]; then
            SECRETS_FOUND=true
            echo "  Secrets exported to $EXPORT_ENV_FILE"
        fi
    else
        echo "  Python not found. Skipping secret export."
        echo "  You'll need to manually add API keys to .env file."
    fi
else
    echo "  export_secrets.py not found. Skipping secret export."
fi

# ── Step 3: Merge secrets into .env ─────────────────────────────────────────
echo ""
echo "Step 3: Merging secrets into .env file..."

if [ "$SECRETS_FOUND" = true ]; then
    # Backup existing .env
    if [ -f ".env" ]; then
        cp .env .env.backup.$(date +%Y%m%d_%H%M%S)
        echo "  ✓ Backed up existing .env file"
    fi

    # Copy .env.example if no .env exists
    if [ ! -f ".env" ] && [ -f ".env.example" ]; then
        cp .env.example .env
        echo "  ✓ Created .env from .env.example"
    fi

    # Merge exported secrets into .env (only if not already set)
    while IFS='=' read -r key value; do
        if [ -n "$key" ] && [ -n "$value" ]; then
            # Check if key already exists in .env
            if grep -q "^${key}=" .env 2>/dev/null; then
                echo "  - $key already exists in .env (keeping existing)"
            else
                echo "$key=$value" >> .env
                echo "  ✓ Added $key to .env"
            fi
        fi
    done < "$EXPORT_ENV_FILE"

    # Clean up temp file
    rm "$EXPORT_ENV_FILE"
fi

# ── Step 4: Stop Docker if running ──────────────────────────────────────────
echo ""
echo "Step 4: Stopping Docker containers (if running)..."

if docker compose ps -q 2>/dev/null | grep -q .; then
    docker compose down
    echo "  ✓ Docker containers stopped"
else
    echo "  - No running containers found"
fi

# ── Step 5: Copy databases into Docker volume ───────────────────────────────
echo ""
echo "Step 5: Copying databases into Docker volume..."

if [ "$DB_FOUND" = true ]; then
    # Ensure Docker is running
    if ! docker info > /dev/null 2>&1; then
        echo "  ✗ Docker is not running. Please start Docker Desktop first."
        exit 1
    fi

    # Start containers temporarily to ensure volume exists
    echo "  Ensuring Docker volume exists..."
    docker compose up -d --no-build 2>/dev/null || docker-compose up -d --no-build 2>/dev/null || true
    sleep 3

    # Copy databases using docker cp
    if [ -f "trading_system.db" ]; then
        echo "  Copying trading_system.db..."
        docker cp trading_system.db sentiment-trading:/data/trading_system.db 2>/dev/null || \
        docker cp trading_system.db sentimenttradingalpha-app:/data/trading_system.db 2>/dev/null
        if [ $? -eq 0 ]; then
            echo "  ✓ Copied trading_system.db to Docker volume"
        else
            echo "  ✗ Failed to copy trading_system.db (container may not be running)"
        fi
    fi

    if [ -f "decision_log.db" ]; then
        echo "  Copying decision_log.db..."
        docker cp decision_log.db sentiment-trading:/data/decision_log.db 2>/dev/null || \
        docker cp decision_log.db sentimenttradingalpha-app:/data/decision_log.db 2>/dev/null
        if [ $? -eq 0 ]; then
            echo "  ✓ Copied decision_log.db to Docker volume"
        else
            echo "  ✗ Failed to copy decision_log.db (container may not be running)"
        fi
    fi

    # Stop temporary containers
    docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true
else
    echo "  - No databases to copy"
fi

# ── Step 6: Start Docker with migrated data ─────────────────────────────────
echo ""
echo "Step 6: Starting Docker with migrated data..."

# Try both docker compose syntaxes (newer vs older Docker versions)
if docker compose version &> /dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif docker-compose version &> /dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
else
    echo "  ✗ Docker Compose not found. Please install Docker Compose."
    exit 1
fi

echo "  Using: $COMPOSE_CMD"
$COMPOSE_CMD up -d --build

# Wait for container to be ready
echo ""
echo "Waiting for container to start..."
for i in {1..30}; do
    if docker ps | grep -q sentiment-trading; then
        echo "  ✓ Container is running"
        break
    fi
    sleep 2
done

echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║   Migration Complete!                                                ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. Check container logs: docker compose logs -f"
echo "  2. Access frontend: http://localhost:3000"
echo "  3. Access backend API: http://localhost:8000/docs"
echo ""
echo "If you had existing secrets in keyring:"
echo "  - They've been added to your .env file"
echo "  - Docker will use these instead of keyring"
echo "  - Your original keyring data is unchanged"
echo ""
echo "To verify migration:"
echo "  docker exec -it sentiment-trading ls -la /data"
echo ""
