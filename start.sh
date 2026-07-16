#!/bin/bash
# ── Start both backend and frontend in one container ─────────────
set -e

echo "╔══════════════════════════════════════════════════════════╗"
echo "║   Sentiment Trading Alpha — Starting services           ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Backend: FastAPI on port 8000 ────────────────────────────────
echo "[backend]  Starting FastAPI on :8000 ..."
cd /app
export HOST=${HOST:-0.0.0.0}
export PORT=${PORT:-8000}
python run.py &
BACKEND_PID=$!

# Wait for backend to be ready
echo "[backend]  Waiting for API to be ready..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/docs > /dev/null 2>&1; then
        echo "[backend]  Ready ✓"
        break
    fi
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        echo "[backend]  FAILED to start (exit after ${i}s)"
        exit 1
    fi
    sleep 1
done

# ── Frontend: Next.js on port 3000 ───────────────────────────────
echo "[frontend] Starting Next.js on :3000 ..."
cd /app/frontend
NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-http://localhost:8000}" \
    npx next start -p 3000 &
FRONTEND_PID=$!

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Services running:                                      ║"
echo "║    Frontend  → http://localhost:3000                    ║"
echo "║    Backend   → http://localhost:8000                    ║"
echo "║    API docs  → http://localhost:8000/docs               ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Keep container alive; stop both on exit ──────────────────────
cleanup() {
    echo "[shutdown] Stopping services..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
    wait 2>/dev/null
    echo "[shutdown] Done."
}
trap cleanup SIGTERM SIGINT EXIT

wait
