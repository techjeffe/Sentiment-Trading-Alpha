# Sentiment Trading Alpha

A geopolitical sentiment pipeline that reads the news, reasons about it with a local (or cloud) LLM, and generates trade recommendations for USO, BITO, IBIT, QQQ, and SPY — including leveraged execution tickers when confidence is high enough to warrant it. Runs automatically on a user set schedule (default 30 minutes).

> **This is experimental software. It is not financial advice.**

Licensed under the [Apache License, Version 2.0](LICENSE).

---

## Quick Start

```bash
# Terminal 1 — Start Ollama
ollama pull qwen3.5:9b
ollama serve

# Terminal 2 — Start the backend & front-end
pip install -r requirements.txt
python -m playwright install chromium   # browser for Telegram image snapshots
cd frontend
npm install
cd ..
npm run start
```

Open [http://localhost:3000](http://localhost:3000).

That's it. The ingestion worker starts automatically when the backend boots. No database setup, no config file editing. If you want admin token protection, Alpaca integration, Telegram notifications, or custom symbols, see the [Admin Controls](#admin-controls) section.

---

## Docker Deployment (Recommended for Cloud LLM Users)

The easiest way to run Sentiment Trading Alpha with cloud LLMs (OpenAI, OpenRouter, Anthropic, etc.) is using Docker. This packages both the frontend and backend into a single container.

### Prerequisites

- **Docker Desktop** installed and running
- **API key** from a cloud LLM provider (OpenAI, OpenRouter, etc.)

### Quick Start

1. **Clone and configure:**
   ```bash
   git clone https://github.com/yourusername/Sentiment-Trading-Alpha.git
   cd Sentiment-Trading-Alpha
   cp .env.example .env
   ```

2. **Edit `.env` with your API keys:**
   ```bash
   # Choose your cloud LLM provider
   INFERENCE_BACKEND=openai
   
   # OpenAI
   OPENAI_API_KEY=sk-your-key-here
   OPENAI_BASE_URL=https://api.openai.com/v1
   OPENAI_MODEL=gpt-4o-mini
   
   # OR OpenRouter (access multiple models)
   # OPENAI_API_KEY=sk-or-v1-your-openrouter-key
   # OPENAI_BASE_URL=https://openrouter.ai/api/v1
   # OPENAI_MODEL=openai/gpt-4o-mini
   
   # OR Anthropic (via OpenAI-compatible API)
   # OPENAI_API_KEY=sk-ant-your-key
   # OPENAI_BASE_URL=https://api.anthropic.com/v1
   # OPENAI_MODEL=claude-3-5-sonnet-20241022
   
   # Set a secure admin token
   ADMIN_API_TOKEN=$(openssl rand -hex 32)
   ```

3. **Build and start:**
   ```bash
   docker compose up --build
   ```

4. **Access:**
   - **Frontend:** [http://localhost:3000](http://localhost:3000)
   - **Backend API:** [http://localhost:8000](http://localhost:8000)
   - **API Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)

### Stopping

```bash
docker compose down
```

To also remove the database volume:
```bash
docker compose down -v
```

### Cloud LLM Configuration

The Docker setup supports any OpenAI-compatible cloud provider:

| Provider | `OPENAI_BASE_URL` | Notes |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | Native support |
| OpenRouter | `https://openrouter.ai/api/v1` | Access to 200+ models |
| Anthropic | `https://api.anthropic.com/v1` | Via OpenAI-compatible API |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | Via OpenAI-compatible API |
| Custom | Your endpoint | Any OpenAI-compatible API |

**Security note:** The backend enforces HTTPS for cloud endpoints. API keys are never stored in the repo — they're passed via environment variables in `.env`.

### Data Persistence

SQLite databases (`trading_system.db`, `decision_log.db`) are persisted in a Docker volume (`trading-data`). Your analysis history and trades survive container restarts.

### Advanced: Customization

Edit `.env` to configure:
- `FRED_API_KEY` — Free economic data from FRED (recommended for better signals)
- `TELEGRAM_BOT_TOKEN` — Telegram notifications
- `CORS_ORIGINS` — Allowed frontend origins
- `ANALYSIS_MAX_SECONDS` — LLM timeout

See `.env.example` for all available options.

---

For a deeper reference covering the API, schema migrations, position sizing math, validation sources, and other advanced topics, see [REFERENCE.md](REFERENCE.md).

---

## How It Works

### The Pipeline

1. **Ingestion** — A background worker continuously polls RSS feeds, extracts full article text, and queues rows in a local SQLite database.
2. **Analysis** — Every 30 minutes (or longer/shorter - user settable) the main batch job consumes queued articles, runs a two-stage LLM pipeline (entity extraction → financial reasoning), and overlays structured validation data from FRED and EIA.
3. **Signals** — Each symbol gets its own specialist prompt and produces a BUY, SELL, or HOLD recommendation with a conviction level (LOW / MEDIUM / HIGH). A red-team review challenges the initial thesis before the final signal is shown.
4. **Paper trading** — Every signal auto-simulates a volatility-normalized paper trade. Position size is based on 14-day ATR and conviction level, not a flat dollar amount.
5. **Live trading (optional)** — Alpaca brokerage integration mirrors paper trade opens and closes to real orders in real time, with configurable guardrails.

### Signal Logic

| Bluster Score | Policy Score | Signal | Leverage |
|---|---|---|---|
| < -0.60 | < 0.40 | SELL | 3x if confidence > 75% |
| Any | ≥ 0.40 | BUY | 3x if confidence > 75% |
| Otherwise | Otherwise | HOLD | — |

Scores are computed in Python from LLM-extracted facts — the model never outputs raw floats. Unconfirmed policy news is discounted before threshold comparison.

### Execution Tickers

The analysis reasons about underlying symbols, but recommendations convert to actual broker-tradable tickers when leverage applies:

| Underlying | Bullish (1x) | Bearish (1x) | Bullish (2x) | Bearish (2x) | Bullish (3x) | Bearish (3x) |
|---|---|---|---|---|---|---|
| QQQ | QQQ | QQQ | QLD | QID | TQQQ | SQQQ |
| SPY | SPY | SPY | SSO | SDS | SPXL | SPXS |
| USO | USO | USO | UCO | SCO | USOU | USOD |
| IBIT | IBIT | IBIT | BITU | SBIT | — | — |
| BITO | BITO | BITO | BITU | SBIT | — | — |

Leverage is applied based on conviction level and risk profile. QQQ, SPY, and USO support up to 3x; IBIT and BITO are capped at 2x.

### Architecture

- **Frontend**: Next.js / React dashboard with a live article feed, signal cards, price panel, health page, trading simulation page, snapshot comparison lab, and an Alpha Analytics page (`/alpha`).
- **Backend**: FastAPI serving the analysis pipeline, config, paper trading, and optional Alpaca brokerage routes. All state lives in a local SQLite database.
- **LLM**: Ollama (local), vLLM (local OpenAI-compatible), or any OpenAI-compatible cloud provider. A Cloud/Local toggle in the Admin UI lets you switch between local and cloud inference with per-provider configuration, smart-fill URLs, and protocol validation. Tested local models: `qwen3.5:9b`, `qwen3:8b`, `0xroyce/plutus:latest`. Cloud providers: OpenRouter, Anthropic, OpenAI, Google.
- **Validation data**: EIA petroleum data for USO; FRED M2, TIPS yield, and credit spread data for IBIT, QQQ, and SPY.
- **Technical indicators**: When price history has been pulled, RSI(14), SMA50/200, MACD, Volume Profile, Bollinger Bands %B, ATR(14), and OBV trend are computed locally and injected into each specialist prompt.

---

## Setup

### Prerequisites

- **Python 3.12** — use exactly 3.12; the ingestion path is not tested on 3.14+
- **Node.js 20.9+**
- **[Ollama](https://ollama.com)** with at least one compatible model pulled

### 1. Start Ollama

Pull and serve a model:

```bash
ollama pull qwen3.5:9b
ollama serve
```

Optional — override which model the backend uses:

```bash
# Windows (PowerShell)
$env:OLLAMA_MODEL = "qwen3.5:9b"
$env:OLLAMA_URL   = "http://localhost:11434/api/generate"

# macOS (zsh/bash)
export OLLAMA_MODEL="qwen3.5:9b"
export OLLAMA_URL="http://localhost:11434/api/generate"
```

If `OLLAMA_MODEL` is unset, the backend uses whichever model Ollama is currently serving.

### 1b. Cloud LLM (Optional — Alternative to Ollama)

Instead of running Ollama locally, you can use any OpenAI-compatible cloud provider. Configure everything from the Admin UI — no environment variables required.

**Supported providers:** OpenRouter, Anthropic, OpenAI, Google, and any provider with an OpenAI-compatible chat completions API.

**To configure via the Admin UI:**

1. Start the backend and frontend normally (Ollama is not needed for cloud inference)
2. Open the Admin page and navigate to **LLM Configuration**
3. Toggle to **☁️ Cloud** mode
4. Select your provider from the dropdown (OpenRouter, Anthropic, OpenAI, Google, or Custom)
5. The API URL is auto-populated — override it if needed
6. Save your API key — it is stored in the OS keychain under a per-provider slot, never in the repo
7. Click **🔌 Test Connection** to verify connectivity
8. Cloud models are fetched automatically — the best default model for your provider is pre-selected
9. Optionally set separate models for Stage 1 (extraction) and Stage 2 (reasoning) in the **Model Orchestration** section

**Environment variable fallback** (if not set in Admin):

```bash
# Windows (PowerShell)
$env:INFERENCE_BACKEND = "openai"
$env:OPENAI_BASE_URL   = "https://api.openai.com/v1"
$env:OPENAI_MODEL      = "gpt-4o-mini"
$env:OPENAI_API_KEY    = "sk-..."

# macOS (zsh/bash)
export INFERENCE_BACKEND="openai"
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="gpt-4o-mini"
export OPENAI_API_KEY="sk-..."
```

> **Security note:** The backend enforces HTTPS for public cloud endpoints. HTTP is only allowed for local/private IPs (e.g. `http://localhost:8080`). This prevents API key leakage over unencrypted connections.

### 2. Start the Backend

Create and activate a Python 3.12 virtual environment, then:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

> **Note:** `python -m playwright install chromium` downloads the headless browser used to render Telegram image snapshots. The `playwright` pip package alone does **not** include the browser binary, and it must be re-run after a Playwright version bump. Without it, text Telegram commands (e.g. `/status`) still work but image snapshots silently fail. The backend logs a `WARNING: Snapshot renderer unavailable` line at startup if the browser is missing.

**Windows:**

```powershell
python run.py
```

**macOS:**

```bash
python3.12 run.py
```

#### Optional: Admin API Token

If `ADMIN_API_TOKEN` is set, these routes require an `X-Admin-Token` header: `GET /api/v1/config`, `PUT /api/v1/config`, `POST /api/v1/trades/{trade_id}/execute`, and all `/api/v1/alpaca/*` routes.

**Windows:**

```powershell
$env:ADMIN_API_TOKEN                 = "choose-a-long-random-string"
$env:INGESTION_STARTUP_GRACE_SECONDS = "20"
python run.py
```

**macOS:**

```bash
export ADMIN_API_TOKEN="choose-a-long-random-string"
export INGESTION_STARTUP_GRACE_SECONDS="20"
python3.12 run.py
```

Telegram and Alpaca credentials are saved from the Admin UI and stored in the OS keychain via `keyring` (Windows Credential Manager on Windows, Keychain Access on macOS) — never in the repo.

### 3. Start the Frontend

```bash
cd frontend
npm install
npm run dev
```

If `ADMIN_API_TOKEN` is set on the backend, set it here too:

**Windows (PowerShell):**

```powershell
$env:ADMIN_API_TOKEN = "choose-a-long-random-string"
npm run dev
```

**macOS (bash/zsh):**

```bash
export ADMIN_API_TOKEN="choose-a-long-random-string"
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

> Restart the dev server once after the first `npm install` so PostCSS picks up the Tailwind config. If you have stale `node_modules` or `.next` output from a previous install, clear those before debugging anything.

---

## Admin Controls

The Admin page is where you configure everything. Changes persist in the database and survive restarts.

- **LLM Configuration** — Choose Cloud or Local mode, then pick your provider (OpenRouter, Anthropic, OpenAI, Google, Ollama, vLLM, llama.cpp, or Custom). The API URL is auto-populated. Cloud API keys are stored per-provider in the OS keychain via `keyring`. Test connectivity with the built-in button.
- **Analysis Depth** — Light / Normal / Detailed controls article count per feed and pipeline behavior
- **Model Orchestration** — Stage 1 (extraction) and Stage 2 (reasoning) model selectors; optional Light Web Research toggle. When using the Cloud LLM backend, model dropdowns include both local and cloud models.
- **Trading Logic** — session hours, base trade amount, entry threshold, stop loss, take profit, re-entry cooldown, trailing stop behavior, portfolio cap, and strategy feature toggles (continuous entry sizing, regime adaptation, separate hold decay)
- **Symbols** — enable/disable default symbols (USO, BITO, IBIT, QQQ, SPY); add up to 3 custom symbols
- **RSS Sources** — enable/disable built-in feeds; add up to 3 custom feeds with display labels
- **Prompt Overrides** — per-symbol specialist prompt guidance
- **Scheduling & System** — auto-run cadence, snapshot retention limit, display timezone
- **Telegram** — bot token, private chat ID, authorized user ID stored in OS keychain; enable Remote Snapshots and Remote Control independently. Remote Control commands: `/status`, `/stop`, `/start`, `/live on`, `/live off`, `/snapshot`, `/help`
- **Price History** — pull and view per-symbol OHLCV history; used for technical indicator computation
- **Live Trading (Alpaca)** — API key entry, paper/live mode, guardrails (position cap, total exposure cap, daily loss limit, consecutive-loss circuit breaker), and the enable/disable toggle with a "type LIVE to confirm" modal

---

## Live Trading Guardrails

> Live Alpaca execution is **alpha functionality**. It is untested in the real world. Do not use it with money you care about.

When Alpaca keys are configured and live trading is enabled, every paper trade open and close is mirrored to Alpaca in real time. Guardrails include:

- Per-symbol position cap in USD
- Total open exposure cap in USD
- Daily realized loss limit
- Consecutive-loss circuit breaker (auto-disables live trading when hit)
- Dynamic notional capping — orders are sized to fit within available buying power (95% buffer)
- Automatic wash trade resolution — conflicting orders are cancelled and the order retried
- Available shares check — close orders never sell more than Alpaca allows
- Circuit breaker recovery UI — amber banner with one-click re-enable when the breaker fires
- All order attempts written to an audit log regardless of outcome
- Telegram remote control — `/live on` and `/live off` commands to arm or disarm live execution from your phone without opening the Admin UI

---

## Security

This repo is designed for **local single-user use**.

- The backend binds to `127.0.0.1` by default. Do not expose port `8000` publicly without adding auth and rate limiting.
- Sensitive admin routes can be protected with `ADMIN_API_TOKEN` (see [Setup](#2-start-the-backend)).
- API keys for Telegram and Alpaca are stored in the OS keychain via `keyring` — never in the repo.
- Generated databases, caches, and build output are excluded from git.

---

## Troubleshooting / Verbose Mode

When things aren't working as expected, start the backend and/or frontend with debug logging to see every request, response, and internal detail.

### Backend

```bash
# Windows
python run.py --verbose

# macOS
python3.12 run.py --verbose

# Or via environment variable
$env:VERBOSE = "1"      # Windows
export VERBOSE="1"       # macOS
```

Verbose mode:
- Sets uvicorn's log level to `debug` (showing every HTTP request including price polling)
- Removes the price-endpoint access-log suppression filter so all endpoint activity is visible
- Sets the `VERBOSE` environment variable for other backend modules to check

### Frontend

```bash
cd frontend
npm run dev:verbose
```

The `dev:verbose` script sets `NEXT_PUBLIC_VERBOSE=true` so frontend code can conditionally enable `console.debug()` output.

### Both at Once (Root-Level Convenience Scripts)

```bash
# Normal startup — runs backend + frontend concurrently
npm run start

# Verbose startup — runs both with debug logging
npm run start:verbose
```

Individual component scripts are also available:

| Script | Description |
|---|---|
| `npm run start:backend` | Start backend only (normal) |
| `npm run start:backend:verbose` | Start backend with debug logging |
| `npm run start:frontend` | Start frontend only (normal) |
| `npm run start:frontend:verbose` | Start frontend with verbose mode |

---

## Upgrading

The backend runs schema migrations automatically on every startup. If you're pulling new code, just restart:

```bash
# Windows
python run.py

# macOS
python3.12 run.py
```

No manual SQL required.

---

## Alpha Analytics

The `/alpha` page (accessible via the **α Alpha** link that appears when Advanced mode is enabled on the main dashboard) audits the actual alpha generated by sentiment signals:

- **Feature Attribution** — shows which news category (earnings, macro_data, geopolitical, etc.) drove each signal, which keyword terms matched, and which RSS sources contributed the most articles.
- **Rolling Information Coefficient (IC)** — Spearman correlation between `blended_confidence_score` and subsequent price movement at 4h, 1d, 3d, and 1w horizons. A rolling window chart shows whether predictive power is decaying over time.
- **Signal Confidence vs. Return scatter** — plots every signal's confidence against its realized return, colored by event type. Use this to see if high-confidence signals actually outperform.
- **Sensitivity / Perturbation Testing** — nudges the entry threshold by ±N% on your historical signals and shows how signal count and average return change per scenario. Detects curve-fitting to noise.

> **Note:** The IC and scatter charts require `TradeSnapshot` records to exist (populated automatically 1h/4h/1d/3d/1w after each paper trade). The attribution breakdown populates after the next analysis run following upgrade.

---

## Disclaimer

Educational and entertainment use only. Trading leveraged ETFs carries significant risk of loss. This software is not financial advice.
