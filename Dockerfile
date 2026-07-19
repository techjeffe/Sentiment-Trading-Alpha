# ── Sentiment Trading Alpha — Single Container ───────────────────
# Builds and runs both the Python backend (FastAPI) and
# the Next.js frontend inside one container.

FROM python:3.12-slim

# Prevent .pyc writes and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── 1. Install system deps: build tools + Node.js 20 ────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libxml2 libxml2-dev libxslt1-dev \
        curl \
        ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# ── 2. Install Python dependencies ──────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chromium && \
    playwright install-deps chromium

# ── 3. Copy frontend source and build Next.js ───────────────────
COPY frontend/ ./frontend/
RUN cd frontend && npm ci --no-audit --fund && \
    npm run build

# ── 4. Copy application source ──────────────────────────────────
COPY backend/ ./backend/
COPY run.py start.sh ./
RUN chmod +x start.sh

# ── 5. Expose ports (frontend: 3000, backend: 8000) ─────────────
EXPOSE 3000 8000

# ── 6. Start both services ──────────────────────────────────────
#    Backend  → uvicorn on :8000
#    Frontend → next start on :3000
CMD ["/app/start.sh"]
