"""
3x Leveraged Sentiment-Driven Trading System.
FastAPI application entry point.
"""

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from database.engine import SessionLocal
from database.models import AnalysisResult
from database.models import init_db
from routers import router as analysis_router
from services.app_config import config_to_dict_with_stats, get_or_create_app_config
from services.pnl_tracker import PnLTracker, SCHEDULER_INTERVAL_SECONDS
from services.data_ingestion.worker import run_ingestion_cycle
from services.data_ingestion.yfinance_client import PriceClient
from services.ollama import get_ollama_status
from services.runtime_health import get_runtime_snapshot, record_data_pull, record_request

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass


class _SuppressPricesAccessLog(logging.Filter):
    """Drop uvicorn access log lines for the prices endpoint (cache-hit polling noise)."""
    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/v1/prices" not in record.getMessage()

# Skip the price-access-log suppression when VERBOSE is set so all endpoint
# activity is visible during troubleshooting.
if not os.getenv("VERBOSE"):
    logging.getLogger("uvicorn.access").addFilter(_SuppressPricesAccessLog())


async def _data_ingestion_scheduler_loop():
    """Periodically ingest RSS articles into the DB queue and refresh quotes."""
    from services.app_config import get_or_create_app_config

    client = PriceClient()
    try:
        startup_grace_seconds = max(0, int(os.getenv("INGESTION_STARTUP_GRACE_SECONDS", "20")))
    except ValueError:
        startup_grace_seconds = 20

    if startup_grace_seconds > 0:
        print(f"Data ingestion scheduler startup grace: waiting {startup_grace_seconds}s before first cycle")
        await asyncio.sleep(startup_grace_seconds)
    
    while True:
        ingestion_interval = 900
        try:
            db = SessionLocal()
            try:
                config = get_or_create_app_config(db)
                ingestion_interval = int(config.data_ingestion_interval_seconds or 900)
                now = datetime.now(timezone.utc)
                lock_request_id = str(getattr(config, "analysis_lock_request_id", "") or "").strip()
                lock_expires_at = getattr(config, "analysis_lock_expires_at", None)
                if lock_request_id and lock_expires_at and lock_expires_at > now:
                    wait_seconds = max(5, min(30, int((lock_expires_at - now).total_seconds())))
                    print(
                        "Data ingestion scheduler deferred: "
                        f"analysis lock owned by {lock_request_id} until {lock_expires_at.isoformat()}"
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
            finally:
                db.close()

            ingestion_stats = await run_ingestion_cycle()
            print(
                "Data ingestion scheduler: "
                f"stage0={ingestion_stats.get('stage0_matches', 0)} "
                f"stored={ingestion_stats.get('stored_count', 0)} "
                f"fast_lane={len(ingestion_stats.get('fast_lane_article_ids', []))}"
            )

            # Get tracked symbols from database config
            db = SessionLocal()
            quotes_ok = []
            quotes_failed = []
            try:
                config = get_or_create_app_config(db)
                symbols = config.tracked_symbols or ["USO", "IBIT", "QQQ", "SPY"]

                # Fetch real-time quotes for tracked symbols
                for symbol in symbols:
                    quote = client.get_realtime_quote(symbol)
                    if quote and quote.get("current_price"):
                        print(f"  {symbol}: ${quote['current_price']:.2f}")
                        quotes_ok.append(symbol)
                    else:
                        quotes_failed.append(symbol)
            finally:
                db.close()

            status = "ok" if not quotes_failed else "partial"
            summary = (
                f"Ingested {ingestion_stats.get('stored_count', 0)} queued articles and "
                f"fetched {len(quotes_ok)}/{len(quotes_ok) + len(quotes_failed)} quotes"
            )
            record_data_pull(
                status=status,
                source="scheduler",
                summary=summary,
                details={
                    "ingestion": ingestion_stats,
                    "quotes_ok": quotes_ok,
                    "quotes_failed": quotes_failed,
                },
                error=None if not quotes_failed else f"Missing quotes for: {', '.join(quotes_failed)}",
            )
        except Exception as e:
            print(f"Data ingestion scheduler error: {e}")
            record_data_pull(
                status="error",
                source="scheduler",
                summary="Background data ingestion failed",
                details={},
                error=str(e),
            )

        await asyncio.sleep(ingestion_interval)


async def _alpaca_poll_scheduler_loop():
    """Poll Alpaca every 5 minutes for fill-status updates on pending orders."""
    while True:
        await asyncio.sleep(300)
        try:
            from services.alpaca_broker import is_alpaca_configured, poll_unfilled_orders
            if is_alpaca_configured():
                db = SessionLocal()
                try:
                    updated = await asyncio.to_thread(poll_unfilled_orders, db)
                    if updated:
                        print(f"[alpaca] poll: updated {updated} order(s)")
                except Exception as exc:
                    print(f"[alpaca] poll error: {exc}")
                finally:
                    db.close()
        except Exception as exc:
            print(f"[alpaca] poll scheduler error: {exc}")


async def _telegram_bot_loop():
    """Long-poll Telegram for /stop /start /status /help commands.

    Credentials are re-read from the OS keychain on every poll cycle, so
    updating them via the admin UI takes effect without a backend restart.
    When credentials are missing the loop sleeps and retries, allowing the
    bot to start once credentials are saved in the admin UI.
    """
    from services.secret_store import get_telegram_credentials
    from services.telegram_bot import initialize_offset, poll_and_dispatch

    offset = None
    print("[telegram-bot] loop started (will retry if credentials are missing)")
    backoff = 1
    max_backoff = 60
    while True:
        try:
            creds   = get_telegram_credentials()
            token   = (creds.get("bot_token") or "").strip()
            chat_id = (creds.get("chat_id")   or "").strip()
            authorized_user_id = (creds.get("authorized_user_id") or "").strip()
            if not token or not chat_id or not authorized_user_id:
                # Credentials missing — sleep and retry so the bot picks up
                # credentials saved in the admin UI without a restart.
                print(f"[telegram-bot] waiting for credentials (retry in {backoff}s)")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                offset = None  # reset offset so we re-init when credentials appear
                continue
            backoff = 1  # reset backoff when credentials are present
            if offset is None:
                offset = await asyncio.to_thread(initialize_offset, token)
                print(f"[telegram-bot] initialized polling offset at {offset}")
            offset = await asyncio.to_thread(poll_and_dispatch, token, chat_id, authorized_user_id, offset)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[telegram-bot] loop error: {exc}")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


async def _pnl_scheduler_loop():
    """Resolve due trade snapshots on the same 30-minute cadence as auto-analyze."""
    tracker = PnLTracker()

    while True:
        db = SessionLocal()
        try:
            created = await asyncio.to_thread(tracker.process_due_snapshots, db)
            if created:
                print(f"P&L snapshot worker stored {created} new snapshots")
        except Exception as exc:
            db.rollback()
            print(f"P&L snapshot worker error: {exc}")
        finally:
            db.close()

        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


async def _weekly_feedback_scheduler_loop():
    """Weekly performance feedback analysis — runs Friday 4-6pm CT.
    
    Checks every 30 minutes if conditions are met (Friday 4-6pm CT,
    not already run this week). If so, runs the full analysis pipeline
    and logs results to the decision log.
    """
    from services.feedback_analysis import should_run_analysis, run_weekly_analysis

    while True:
        try:
            if should_run_analysis():
                print("[feedback] Weekly analysis window active — running performance analysis...")
                db = SessionLocal()
                try:
                    result = await asyncio.to_thread(run_weekly_analysis, db, request_id="weekly_schedule")
                    if result.get("status") == "success":
                        patterns = len(result.get("patterns", []))
                        suggestions = len(result.get("suggestions", []))
                        print(f"[feedback] Weekly analysis complete: {patterns} patterns, {suggestions} suggestions")
                    else:
                        print(f"[feedback] Weekly analysis: {result.get('message', 'no data')}")
                except Exception as exc:
                    db.rollback()
                    print(f"[feedback] Weekly analysis error: {exc}")
                finally:
                    db.close()
        except Exception as exc:
            print(f"[feedback] Scheduler error: {exc}")

        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown events."""
    data_ingestion_task      = None
    pnl_scheduler_task       = None
    alpaca_poll_task         = None
    feedback_scheduler_task  = None
    telegram_bot_task        = None

    print("=" * 60)
    print("3x Leveraged Sentiment Trading System - Starting...")
    print("=" * 60)

    init_db()
    from database.migrate import migrate
    migrate()
    print("Database initialized")
    from services.decision_logger import ensure_decision_log_tables
    ensure_decision_log_tables()
    print("Decision log tables initialized")
    from services.analysis.cache_service import get_price_cache_service
    get_price_cache_service()
    print("Price cache service initialized")

    # ── Snapshot renderer health check ───────────────────────────────────
    # Telegram snapshots render an HTML dashboard to PNG via Playwright's
    # Chromium browser. The browser binary is downloaded separately from the
    # pip package; without it, snapshot delivery silently fails while text
    # commands (/status) keep working. Surface the gap loudly at startup.
    try:
        from services.remote_snapshot import check_snapshot_renderer
        from services.runtime_health import record_data_pull
        renderer = check_snapshot_renderer()
        if renderer.get("available"):
            print("Snapshot renderer (Playwright Chromium) available")
        else:
            print(
                f"WARNING: Snapshot renderer unavailable — {renderer.get('detail')}. "
                f"Telegram image snapshots will not send. Fix: {renderer.get('remedy')}"
            )
            record_data_pull(
                status="error",
                source="remote_snapshot",
                summary="Snapshot renderer unavailable — Telegram image snapshots disabled",
                details=renderer,
                error=renderer.get("detail"),
            )
    except Exception as exc:
        print(f"Warning: snapshot renderer health check failed: {exc}")
    # ─────────────────────────────────────────────────────────────────────

    # ── Clear stale analysis locks from previous runs ────────────────────
    try:
        db = SessionLocal()
        try:
            from services.app_config import get_or_create_app_config
            config = get_or_create_app_config(db)
            if getattr(config, "analysis_lock_request_id", None) is not None:
                config.analysis_lock_request_id = None
                config.analysis_lock_acquired_at = None
                config.analysis_lock_expires_at = None
                db.add(config)
                db.commit()
                print("Cleared stale analysis lock from previous run")
        finally:
            db.close()
    except Exception as exc:
        print(f"Warning: could not clear stale analysis lock: {exc}")
    # ─────────────────────────────────────────────────────────────────────

    try:
        from services.alpaca_broker import is_alpaca_configured, reconcile_on_startup
        if is_alpaca_configured():
            db = SessionLocal()
            try:
                reconcile_on_startup(db)
            finally:
                db.close()
    except Exception as exc:
        print(f"[alpaca] startup reconciliation skipped: {exc}")

    bind_host = os.getenv("HOST", "127.0.0.1")
    cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000")
    admin_token_enabled = bool(os.getenv("ADMIN_API_TOKEN", "").strip())
    print(f"Local-first defaults: backend host={bind_host} | CORS={cors_origins}")
    if bind_host not in {"127.0.0.1", "localhost"}:
        if not admin_token_enabled:
            raise RuntimeError(
                "Refusing to bind to 0.0.0.0 or external network without ADMIN_API_TOKEN. "
                "Set ADMIN_API_TOKEN to a strong secret, or set HOST=127.0.0.1 for local-only access."
            )
        print("WARNING: Backend is configured to listen beyond localhost — admin token is required for all routes.")
    if "*" in cors_origins:
        print("WARNING: CORS_ORIGINS contains '*'. This is not recommended outside local development.")
    if not admin_token_enabled:
        print("WARNING: ADMIN_API_TOKEN is not set. Sensitive routes (config, Alpaca, trades) are UNPROTECTED.")
        print("WARNING: Set ADMIN_API_TOKEN environment variable to enable authentication.")
    else:
        print("Admin token protection enabled for config and trade execution routes.")

    data_ingestion_task = asyncio.create_task(_data_ingestion_scheduler_loop())
    print("Data ingestion scheduler started (fetching RSS feeds and stock quotes)")

    pnl_scheduler_task = asyncio.create_task(_pnl_scheduler_loop())
    print("P&L snapshot scheduler started")

    alpaca_poll_task = asyncio.create_task(_alpaca_poll_scheduler_loop())
    print("Alpaca order poll scheduler started (5 min interval)")

    feedback_scheduler_task = asyncio.create_task(_weekly_feedback_scheduler_loop())
    print("Weekly feedback analysis scheduler started (Friday 4pm CT, every 30 min check)")

    try:
        from services.app_config import get_or_create_app_config
        from services.secret_store import get_telegram_credentials
        db = SessionLocal()
        try:
            cfg = get_or_create_app_config(db)
            remote_control_enabled = bool(getattr(cfg, "telegram_remote_control_enabled", False))
        finally:
            db.close()
        _tg = get_telegram_credentials()
        token   = (_tg.get("bot_token") or "").strip()
        chat_id = (_tg.get("chat_id") or "").strip()
        user_id = (_tg.get("authorized_user_id") or "").strip()
        
        print(f"[telegram-bot] startup: enabled={remote_control_enabled}, token_present={bool(token)}, chat_id_present={bool(chat_id)}, user_id_present={bool(user_id)}")
        
        if remote_control_enabled:
            # Always start the loop — it re-reads credentials from the OS keychain
            # on every poll cycle, so saving credentials in the admin UI takes
            # effect without a restart. If credentials are missing at boot the
            # loop will back off and retry until they appear.
            telegram_bot_task = asyncio.create_task(_telegram_bot_loop())
            if token and chat_id and user_id:
                print("[telegram-bot] remote control started (polling for /status /stop /start /snapshot /help)")
            else:
                print("[telegram-bot] remote control enabled but credentials missing — bot will start automatically once credentials are saved in admin UI")
        else:
            print("[telegram-bot] skipped (disabled in admin settings)")
    except Exception as exc:
        import traceback
        print(f"[telegram-bot] startup error: {exc}")
        traceback.print_exc()

    yield

    shutdown_timeout = 5.0  # seconds to wait for each task
    for task, name in [
        (data_ingestion_task, "data_ingestion"),
        (pnl_scheduler_task, "pnl_scheduler"),
        (alpaca_poll_task, "alpaca_poll"),
        (feedback_scheduler_task, "feedback_scheduler"),
        (telegram_bot_task, "telegram_bot"),
    ]:
        if task:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=shutdown_timeout)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                print(f"[shutdown] {name} task did not finish within {shutdown_timeout}s")

    print("Shutting down gracefully...")


# ── Rate limiter ─────────────────────────────────────────────────────────────
# 60 requests/minute for most endpoints, 10/minute for sensitive mutation endpoints
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60/minute"],
    enabled=True,
)

app = FastAPI(
    title="3x Leveraged Sentiment Trading System",
    description="""
A sentiment-driven trading system that analyzes geopolitical and social media data
to generate trading signals for 3x leveraged ETFs (USO, BITO).

## Features
- Real-time sentiment analysis using Llama-3-70b
- Rolling window backtesting with VectorBT
- Single-button dashboard for rapid execution
- Risk management with stop-loss and position sizing
    """,
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── CORS ─────────────────────────────────────────────────────────────────────
# Forbid wildcard CORS when credentials are enabled
raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000")
allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

if "*" in allowed_origins:
    # Can't use credentials with '*'; downgrade to no-credentials mode
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
    )


# ── Security headers middleware ──────────────────────────────────────────────
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ── Request size limit middleware ────────────────────────────────────────────
MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB

@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BODY_SIZE:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
        except (ValueError, TypeError):
            pass
    response = await call_next(request)
    return response


# ── Request metrics middleware ───────────────────────────────────────────────
@app.middleware("http")
async def track_request_metrics(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    record_request((time.perf_counter() - started) * 1000)
    return response


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint for monitoring system status."""
    runtime = get_runtime_snapshot()
    db_status = "connected"
    config_payload = {}
    recent_run_count = 0
    avg_analysis_runtime_ms = None
    last_completed_at = None

    try:
        db = SessionLocal()
        try:
            config = get_or_create_app_config(db)
            config_payload = config_to_dict_with_stats(db, config)
            recent_run_count = db.query(AnalysisResult).count()
        finally:
            db.close()
    except Exception as exc:
        db_status = f"error: {exc}"

    recent_analysis_seconds = config_payload.get("recent_analysis_seconds") or []
    if recent_analysis_seconds:
        avg_analysis_runtime_ms = round(sum(recent_analysis_seconds) / len(recent_analysis_seconds) * 1000, 2)

    last_data_pull = (runtime.get("recent_data_pulls") or [None])[0]
    last_analysis = runtime.get("last_analysis") or {}
    if not last_analysis.get("completed_at"):
        last_analysis["completed_at"] = config_payload.get("last_analysis_completed_at")
    if not last_analysis.get("request_id"):
        last_analysis["request_id"] = config_payload.get("last_analysis_request_id")
    if not last_analysis.get("active_model"):
        try:
            ollama_status = get_ollama_status()
        except Exception as exc:
            ollama_status = {
                "reachable": False,
                "ollama_root": os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate").replace("/api/generate", ""),
                "configured_model": os.getenv("OLLAMA_MODEL", "").strip(),
                "active_model": "",
                "available_models": [],
                "resolution": "unreachable",
                "error": str(exc),
            }
    else:
        try:
            ollama_status = get_ollama_status()
        except Exception as exc:
            ollama_status = {
                "reachable": False,
                "ollama_root": os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate").replace("/api/generate", ""),
                "configured_model": os.getenv("OLLAMA_MODEL", "").strip(),
                "active_model": last_analysis.get("active_model") or "",
                "available_models": [],
                "resolution": "unreachable",
                "error": str(exc),
            }

    if not avg_analysis_runtime_ms and last_analysis.get("duration_ms"):
        avg_analysis_runtime_ms = round(float(last_analysis["duration_ms"]), 2)

    overall_status = "healthy"
    if db_status != "connected" or not ollama_status.get("reachable", False):
        overall_status = "degraded"
    if last_data_pull and last_data_pull.get("status") == "error":
        overall_status = "degraded"
    if last_analysis.get("status") == "failed":
        overall_status = "degraded"

    last_completed_at = config_payload.get("last_analysis_completed_at")

    return {
        "status": overall_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "database_status": db_status,
        "runtime": {
            "started_at": runtime.get("started_at"),
            "uptime_seconds": runtime.get("uptime_seconds"),
            "request_count": runtime.get("request_count"),
            "avg_request_latency_ms": runtime.get("avg_request_latency_ms"),
        },
        "model": ollama_status,
        "analysis": {
            "avg_runtime_ms": avg_analysis_runtime_ms,
            "recent_analysis_seconds": recent_analysis_seconds,
            "last_request_id": last_analysis.get("request_id"),
            "last_completed_at": last_analysis.get("completed_at") or last_completed_at,
            "last_status": last_analysis.get("status"),
            "last_error": last_analysis.get("error"),
            "recent_run_count": recent_run_count,
            "tracked_symbols": config_payload.get("tracked_symbols") or ["USO", "IBIT", "QQQ", "SPY"],
            "auto_run_enabled": config_payload.get("auto_run_enabled"),
            "seconds_until_next_auto_run": config_payload.get("seconds_until_next_auto_run"),
        },
        "data_pulls": {
            "latest": last_data_pull,
            "recent": runtime.get("recent_data_pulls"),
        },
    }


@app.get("/metrics", tags=["Metrics"])
async def get_metrics():
    """Get system metrics including request counts and latency stats."""
    return {
        "uptime_seconds": None,
        "total_requests": 0,
        "avg_latency_ms": 0.0,
        "database_status": "connected",
        "pnl_scheduler_interval_minutes": SCHEDULER_INTERVAL_SECONDS // 60,
    }


app.include_router(analysis_router, prefix="/api/v1", tags=["API"])


if __name__ == "__main__":
    import uvicorn

    log_level = "debug" if os.getenv("VERBOSE") else "info"

    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
        log_level=log_level,
    )
