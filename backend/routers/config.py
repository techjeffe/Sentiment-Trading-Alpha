"""
Configuration API router.
"""

import asyncio
from typing import Any, Dict, List, Optional

from services.audit_log import record_audit_event

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database.engine import get_db
from database.models import (
    AnalysisResult, Post, PriceHistory, ScrapedArticle, Trade, TradeClose, TradeExecution,
    TradeSnapshot, TradingSignal,
)
from security import require_admin_token
from services.app_config import (
    config_to_dict_with_stats,
    get_or_create_app_config,
    update_app_config,
)
from services.ollama import get_ollama_status
from services.paper_trading import close_positions_for_removed_symbols
from services.remote_snapshot import trigger_remote_snapshot_delivery
from services.symbol_proxy_terms import ensure_symbol_proxy_terms_fresh, generate_proxy_terms_for_symbol
from services.secret_store import (
    get_telegram_credentials,
    clear_telegram_secrets,
    get_telegram_secret_status,
    save_telegram_secrets,
    get_openai_secret_status,
    save_openai_api_key,
    clear_openai_api_key,
    get_openai_api_key,
    get_cloud_secret_status,
    save_cloud_api_key,
    clear_cloud_api_key,
    get_cloud_api_key,
)
from services.openai_client import get_openai_status
from services.telegram_bot import verify_remote_control


router = APIRouter()


def _is_local_url(url: str) -> bool:
    """Check if a URL points to a local server (localhost, 127.0.0.1, etc.)."""
    import re
    return bool(re.search(r"(localhost|127\\.0\\.0\\.1|::1)", url, re.IGNORECASE))


def _fetch_models_from_backends(config, override_base_url: Optional[str] = None, override_provider: Optional[str] = None) -> Dict[str, Any]:
    """Return {'local_models': [...], 'cloud_models': [...]} from all configured backends.

    Args:
        config: AppConfig instance from the database.
        override_base_url: If provided, overrides the DB-stored openai_base_url.
            Used when the frontend user has edited the URL but not yet saved.
        override_provider: If provided, reads the API key for this provider.
            Used when the frontend user has changed provider but not yet saved.
    """
    result: Dict[str, List[str]] = {"local_models": [], "cloud_models": []}

    # Determine the inference backend
    backend = str(getattr(config, "inference_backend", "ollama") or "ollama").strip().lower()

    # 1. Local models - depends on the backend type
    if backend == "ollama":
        # Ollama backend: use Ollama API
        try:
            ollama_db_url = str(getattr(config, "ollama_url", "") or "").strip()
            ollama_url_param = ollama_db_url if ollama_db_url else None
            ollama = get_ollama_status(timeout=3, ollama_url=ollama_url_param)
            result["local_models"] = ollama.get("available_models") or []
        except Exception:
            result["local_models"] = []
    elif backend == "omlx":
        # OMLX backend: use OpenAI-compatible API (local server)
        try:
            base_url = str(getattr(config, "openai_base_url", "http://localhost:11434/v1") or "http://localhost:11434/v1")
            if override_base_url:
                base_url = override_base_url
            # omlx typically doesn't require a real API key
            api_key = get_openai_api_key() or "dummy"
            status = get_openai_status(api_key=api_key, base_url=base_url, timeout=5)
            result["local_models"] = status.get("available_models") or []
        except Exception:
            result["local_models"] = []
    elif backend == "vllm":
        # vLLM backend: use OpenAI-compatible API (local server)
        try:
            base_url = str(getattr(config, "openai_base_url", "http://localhost:8000/v1") or "http://localhost:8000/v1")
            if override_base_url:
                base_url = override_base_url
            api_key = get_openai_api_key() or "dummy"
            status = get_openai_status(api_key=api_key, base_url=base_url, timeout=5)
            result["local_models"] = status.get("available_models") or []
        except Exception:
            result["local_models"] = []

    # 2. Cloud models from OpenAI-compatible endpoint (only for 'openai' backend)
    if backend == "openai":
        def _resolve_base_url() -> str:
            if override_base_url:
                return override_base_url
            return str(getattr(config, "openai_base_url", "https://api.openai.com/v1") or "https://api.openai.com/v1")

        api_key = get_openai_api_key()
        # Also try override_provider or the DB-stored cloud_provider
        if override_provider and override_provider != "openai":
            from services.secret_store import get_cloud_api_key
            api_key = get_cloud_api_key(override_provider)
        elif not api_key:
            cloud_provider = str(getattr(config, "cloud_provider", "") or "").strip().lower()
            if cloud_provider and cloud_provider != "openai":
                from services.secret_store import get_cloud_api_key
                api_key = get_cloud_api_key(cloud_provider)
        if api_key:
            try:
                from services.openai_client import get_openai_status
                base_url = _resolve_base_url()
                status = get_openai_status(api_key=api_key, base_url=base_url, timeout=5)
                result["cloud_models"] = status.get("available_models") or []
            except Exception:
                result["cloud_models"] = []
        else:
            # Also try env var fallback
            import os
            env_key = os.getenv("OPENAI_API_KEY", "").strip()
            if env_key:
                try:
                    from services.openai_client import get_openai_status
                    base_url = _resolve_base_url()
                    status = get_openai_status(api_key=env_key, base_url=base_url, timeout=5)
                    result["cloud_models"] = status.get("available_models") or []
                except Exception:
                    result["cloud_models"] = []

    return result


def _merge_models_with_label(local: List[str], cloud: List[str]) -> List[str]:
    """Merge local and cloud models into a single flat list of raw model IDs.
    All entries are kept as their original model IDs (no prefix in the value).
    The caller can cross-reference against local_models / cloud_models to determine origin."""
    seen: set = set()
    merged: List[str] = []
    for m in local + cloud:
        if m not in seen:
            seen.add(m)
            merged.append(m)
    return merged


@router.get("/config", tags=["Config"])
async def get_config(
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    config = get_or_create_app_config(db)
    payload = config_to_dict_with_stats(db, config)

    model_info = _fetch_models_from_backends(config)
    local_models = model_info["local_models"]
    cloud_models = model_info["cloud_models"]

    payload["local_models"] = local_models
    payload["cloud_models"] = cloud_models
    # Legacy flat list for backward compat — merged with local: / cloud: prefix
    payload["available_models"] = _merge_models_with_label(local_models, cloud_models)

    return payload


@router.get("/admin/models", tags=["Admin"])
async def get_admin_models(
    request: Request,
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Refresh and return models from all configured backends on demand.

    Query parameters:
      - base_url: override the DB-stored openai_base_url (for when the
        user has edited the URL in the UI but not yet saved config).
      - provider: override the DB-stored cloud_provider (for when the
        user has switched providers but not yet saved config).
    """
    config = get_or_create_app_config(db)
    base_url_override = request.query_params.get("base_url")
    provider_override = request.query_params.get("provider")
    model_info = _fetch_models_from_backends(config, override_base_url=base_url_override, override_provider=provider_override)
    return {
        "local_models": model_info["local_models"],
        "cloud_models": model_info["cloud_models"],
        "available_models": _merge_models_with_label(model_info["local_models"], model_info["cloud_models"]),
    }


def _pull_history_background(symbols: List[str]) -> None:
    """Pull price history for newly added symbols in a fresh DB session."""
    from database.engine import SessionLocal
    from services.data_ingestion.yfinance_client import PriceClient
    db = SessionLocal()
    try:
        client = PriceClient()
        client.pull_and_store_history(symbols=symbols, db=db, delay_seconds=1.0)
    except Exception as exc:
        print(f"Background price-history pull error: {exc}")
    finally:
        db.close()

async def _generate_symbol_keywords_background(symbols: List[str], model_name: str) -> None:
    """Generate/persist symbol proxy terms in a fresh DB session."""
    from database.engine import SessionLocal

    db = SessionLocal()
    try:
        config = get_or_create_app_config(db)
        await ensure_symbol_proxy_terms_fresh(
            db=db,
            config=config,
            symbols=symbols,
            model_name=model_name,
            force=True,
        )
    except Exception as exc:
        print(f"Background symbol keyword generation error: {exc}")
    finally:
        db.close()


@router.post("/config/tracked-symbols/add", tags=["Config"])
async def add_tracked_symbol(
    payload: Dict[str, str],
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Add a symbol to tracked symbols (custom_symbols)."""
    symbol = str(payload.get("symbol", "")).upper().strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required")
    
    config = get_or_create_app_config(db)
    custom_symbols = list(config.custom_symbols or [])
    
    if symbol in custom_symbols:
        return {"ok": True, "message": f"{symbol} is already tracked", "tracked_symbols": config.tracked_symbols}
    
    custom_symbols.append(symbol)
    config.custom_symbols = custom_symbols
    db.add(config)
    db.commit()
    db.refresh(config)
    
    # Trigger background tasks for new symbol
    from services.data_ingestion.yfinance_client import PriceClient
    try:
        client = PriceClient()
        client.pull_and_store_history(symbols=[symbol], db=db, delay_seconds=1.0)
    except Exception as e:
        print(f"Price history pull error for {symbol}: {e}")
    
    record_audit_event(
        action="add_tracked_symbol",
        resource="config",
        detail=f"Added {symbol} to tracked symbols",
        event_metadata={"symbol": symbol},
    )
    
    return {
        "ok": True,
        "message": f"{symbol} added to tracked symbols",
        "tracked_symbols": config.tracked_symbols,
    }


@router.put("/config", tags=["Config"])
async def put_config(
    payload: Dict[str, Any],
    background_tasks: BackgroundTasks,
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    existing_config = get_or_create_app_config(db)
    previous_custom_symbols = {
        str(symbol or "").upper().strip()
        for symbol in (getattr(existing_config, "custom_symbols", []) or [])
        if str(symbol or "").strip()
    }
    previous_telegram_enabled = bool(getattr(existing_config, "telegram_remote_control_enabled", False))
    
    config = update_app_config(db, payload)
    current_telegram_enabled = bool(getattr(config, "telegram_remote_control_enabled", False))
    
    current_custom_symbols = {
        str(symbol or "").upper().strip()
        for symbol in (getattr(config, "custom_symbols", []) or [])
        if str(symbol or "").strip()
    }
    added_custom_symbols = sorted(current_custom_symbols - previous_custom_symbols)
    removed_custom_symbols = sorted(previous_custom_symbols - current_custom_symbols)
    notices: List[str] = []
    
    # Notify when Telegram remote control is enabled
    if current_telegram_enabled and not previous_telegram_enabled:
        notices.append("Telegram remote control enabled. Restart the backend to activate long-polling.")
    
    if removed_custom_symbols:
        closed_positions = close_positions_for_removed_symbols(db, removed_custom_symbols)
        if closed_positions:
            closed_underlyings = sorted({str(item.get("underlying") or "").upper() for item in closed_positions if item.get("underlying")})
            symbol_list = ", ".join(closed_underlyings)
            noun = "paper trade was" if len(closed_underlyings) == 1 else "paper trades were"
            notices.append(f"Removed custom symbol{'' if len(closed_underlyings) == 1 else 's'} {symbol_list}; matching open {noun} closed.")
    if added_custom_symbols:
        background_tasks.add_task(_pull_history_background, added_custom_symbols)
        notices.append(f"Pulling price history for {', '.join(added_custom_symbols)} in the background.")
        model_name = str(getattr(config, "extraction_model", "") or "").strip()
        if model_name:
            background_tasks.add_task(_generate_symbol_keywords_background, added_custom_symbols, model_name)
            notices.append(f"Generating symbol proxy terms for {', '.join(added_custom_symbols)} in the background.")
        else:
            notices.append("Skipped symbol keyword generation because extraction model is not configured.")
    response = config_to_dict_with_stats(db, config)
    if notices:
        response["notices"] = notices
    
    record_audit_event(
        action="config_update",
        resource="config",
        detail="Application configuration updated",
        event_metadata={"changed_keys": list(payload.keys()), "notices": notices},
    )
    return response


@router.post("/config/custom-symbol-keywords/refresh", tags=["Config"])
async def refresh_custom_symbol_keywords(
    payload: Dict[str, Any],
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    config = get_or_create_app_config(db)
    extraction_model = str(getattr(config, "extraction_model", "") or "").strip()
    if not extraction_model:
        raise HTTPException(status_code=400, detail="Configure extraction_model before refreshing symbol keywords.")

    custom_symbols = {
        str(symbol or "").upper().strip()
        for symbol in (getattr(config, "custom_symbols", []) or [])
        if str(symbol or "").strip()
    }
    requested_symbols = [
        str(symbol or "").upper().strip()
        for symbol in (payload.get("symbols") or [])
        if str(symbol or "").strip()
    ]
    symbols = sorted(set(requested_symbols or list(custom_symbols)))
    if not symbols:
        raise HTTPException(status_code=400, detail="No custom symbols available to refresh.")

    invalid = [symbol for symbol in symbols if symbol not in custom_symbols]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Symbols are not configured as custom: {', '.join(invalid)}")

    symbol_proxy_terms = dict(getattr(config, "symbol_proxy_terms", {}) or {})
    traces: Dict[str, Dict[str, Any]] = {}
    for symbol in symbols:
        result = await generate_proxy_terms_for_symbol(
            symbol=symbol,
            model_name=extraction_model,
            force_refresh=True,
        )
        normalized_symbol = str(result.get("symbol") or symbol).upper().strip()
        terms = list(result.get("terms") or [])
        if normalized_symbol and terms:
            symbol_proxy_terms[normalized_symbol] = terms
        traces[normalized_symbol] = dict(result.get("trace") or {})

    config.symbol_proxy_terms = symbol_proxy_terms
    db.add(config)
    db.commit()
    db.refresh(config)

    return {
        "ok": True,
        "refreshed_symbols": symbols,
        "symbol_proxy_terms": {symbol: symbol_proxy_terms.get(symbol, []) for symbol in symbols},
        "trace": traces,
    }


@router.get("/admin/remote-snapshot-secrets", tags=["Admin"])
async def get_remote_snapshot_secrets(
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    return get_telegram_secret_status()


@router.put("/admin/remote-snapshot-secrets", tags=["Admin"])
async def put_remote_snapshot_secrets(
    payload: Dict[str, Any],
    background_tasks: BackgroundTasks,
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    try:
        previous = get_telegram_credentials()
        next_bot_token = str(payload.get("bot_token") or "")
        next_chat_id = str(payload.get("chat_id") or "")
        next_authorized_user_id = str(payload.get("authorized_user_id") or "")
        changed = (
            str(previous.get("bot_token") or "").strip() != next_bot_token.strip()
            or str(previous.get("chat_id") or "").strip() != next_chat_id.strip()
            or str(previous.get("authorized_user_id") or "").strip() != next_authorized_user_id.strip()
        )
        saved = save_telegram_secrets(
            bot_token=str(payload.get("bot_token") or ""),
            chat_id=str(payload.get("chat_id") or ""),
            authorized_user_id=str(payload.get("authorized_user_id") or ""),
        )
        config = get_or_create_app_config(db)

        latest_analysis = (
            db.query(AnalysisResult)
            .order_by(AnalysisResult.timestamp.desc(), AnalysisResult.id.desc())
            .first()
        )
        if changed and latest_analysis and latest_analysis.request_id:
            background_tasks.add_task(trigger_remote_snapshot_delivery, latest_analysis.request_id, True)
            saved["test_delivery_started"] = True
            saved["test_delivery_request_id"] = latest_analysis.request_id
        else:
            saved["test_delivery_started"] = False
            saved["test_delivery_request_id"] = None
            if changed and not latest_analysis:
                saved["test_delivery_note"] = "No completed analysis run is available yet."
        saved["remote_snapshot_enabled"] = bool(getattr(config, "remote_snapshot_enabled", False))
        saved["telegram_remote_control_enabled"] = bool(getattr(config, "telegram_remote_control_enabled", False))
        
        # Note: No restart is needed — the bot polls credentials from the OS keychain
        # on every cycle and picks up changes automatically.
        
        return saved
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/admin/remote-snapshot-secrets/verify", tags=["Admin"])
async def verify_remote_snapshot_secrets(
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    try:
        creds = get_telegram_credentials()
        token = str(creds.get("bot_token") or "").strip()
        chat_id = str(creds.get("chat_id") or "").strip()
        authorized_user_id = str(creds.get("authorized_user_id") or "").strip()
        if not token or not chat_id or not authorized_user_id:
            raise HTTPException(status_code=400, detail="Telegram bot token, private chat ID, and authorized user ID must all be saved first.")
        return verify_remote_control(token, chat_id, authorized_user_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/admin/telegram-remote-control-banner/acknowledge", tags=["Admin"])
async def acknowledge_telegram_remote_control_banner(
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    try:
        config = get_or_create_app_config(db)
        config.telegram_remote_control_banner_active = False
        config.telegram_remote_control_banner_message = None
        db.add(config)
        db.commit()
        return {"ok": True}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc))


@router.delete("/admin/remote-snapshot-secrets", tags=["Admin"])
async def delete_remote_snapshot_secrets(
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    try:
        return clear_telegram_secrets()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ── Cloud LLM Secrets (per-provider) ──────────────────────────────


@router.get("/admin/openai-secrets", tags=["Admin"])
async def get_openai_secrets(
    request: Request,
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Return masked status of a cloud provider's API key.

    Query params:
      - provider: cloud provider name (openai, anthropic, openrouter, google, custom).
        Defaults to "openai" for backward compatibility.
    """
    provider = request.query_params.get("provider", "openai")
    return get_cloud_secret_status(provider)


@router.put("/admin/openai-secrets", tags=["Admin"])
async def put_openai_secrets(
    payload: Dict[str, Any],
    request: Request,
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Store a cloud provider's API key in the OS keychain.

    Query params:
      - provider: cloud provider name. Defaults to "openai".
    Body: {"api_key": "sk-..."}
    """
    try:
        provider = request.query_params.get("provider", "openai") if hasattr(request, 'query_params') else "openai"
        api_key = str(payload.get("api_key") or "")
        if not api_key:
            raise ValueError(f"API key is required for {provider}")
        return save_cloud_api_key(provider, api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.delete("/admin/openai-secrets", tags=["Admin"])
async def delete_openai_secrets(
    request: Request,
    _admin: None = Depends(require_admin_token),
) -> Dict[str, Any]:
    """Remove a cloud provider's API key from the OS keychain.

    Query params:
      - provider: cloud provider name. Defaults to "openai".
    """
    try:
        provider = request.query_params.get("provider", "openai")
        return clear_cloud_api_key(provider)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ── Cloud LLM Connection Test ────────────────────────────────────────────


@router.post("/admin/openai-test-connection", tags=["Admin"])
async def test_openai_connection(
    payload: Dict[str, Any],
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Test connectivity to the configured OpenAI-compatible endpoint.

    Accepts optional api_key and base_url overrides; when omitted, falls back
    to the saved OS keychain key and the DB-stored base URL.
    """
    config = get_or_create_app_config(db)
    provider = str(payload.get("provider") or config.cloud_provider or "openai")
    api_key = str(payload.get("api_key") or "").strip() or get_cloud_api_key(provider)
    base_url = str(payload.get("base_url") or "").strip() or str(getattr(config, "openai_base_url", "https://api.openai.com/v1") or "https://api.openai.com/v1")
    timeout = min(int(payload.get("timeout", 8)), 15)

    if not api_key:
        raise HTTPException(status_code=400, detail="No API key configured. Save an API key in the admin UI or set OPENAI_API_KEY.")

    try:
        result = get_openai_status(api_key=api_key, base_url=base_url, timeout=timeout)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        return {
            "reachable": False,
            "openai_root": base_url,
            "configured_model": "",
            "active_model": "",
            "available_models": [],
            "running_models": [],
            "resolution": "unreachable",
            "api_key_configured": bool(api_key),
            "error": str(exc),
        }


# ── Remote Snapshot / Data Management ─────────────────────────────────────


@router.post("/admin/remote-snapshot-send", tags=["Admin"])
async def send_remote_snapshot_now(
    background_tasks: BackgroundTasks,
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    latest_analysis = (
        db.query(AnalysisResult)
        .order_by(AnalysisResult.timestamp.desc(), AnalysisResult.id.desc())
        .first()
    )
    if not latest_analysis or not latest_analysis.request_id:
        raise HTTPException(status_code=400, detail="No completed analysis run is available yet.")

    background_tasks.add_task(trigger_remote_snapshot_delivery, latest_analysis.request_id, True)
    return {
        "ok": True,
        "request_id": latest_analysis.request_id,
        "message": "Remote snapshot send has been queued.",
    }


@router.post("/admin/reset-data", tags=["Admin"])
async def reset_data(
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Wipe all analysis, trade, and post data while preserving app config.
    Deletion order respects FK constraints (children before parents).
    """
    counts: Dict[str, int] = {}
    for model in (TradeClose, TradeExecution, TradeSnapshot, TradingSignal, Trade, AnalysisResult, ScrapedArticle, Post):
        deleted = db.query(model).delete(synchronize_session=False)
        counts[model.__tablename__] = deleted

    # Clear last-run metadata so the dashboard doesn't think a stale run is current
    config = get_or_create_app_config(db)
    config.last_analysis_started_at = None
    config.last_analysis_completed_at = None
    config.last_analysis_request_id = None
    config.last_remote_snapshot_sent_at = None
    config.last_remote_snapshot_request_id = None
    config.last_remote_snapshot_net_pnl = None
    config.last_remote_snapshot_recommendation_fingerprint = None
    config.analysis_lock_request_id = None
    config.analysis_lock_acquired_at = None
    config.analysis_lock_expires_at = None
    db.add(config)
    db.commit()

    total = sum(counts.values())
    
    record_audit_event(
        action="data_reset",
        resource="database",
        detail=f"Wiped {total} rows from analysis, trade, and post tables",
        event_metadata={"deleted": counts, "total_rows_deleted": total},
    )
    return {"ok": True, "deleted": counts, "total_rows_deleted": total}


@router.get("/admin/price-history/status", tags=["Admin"])
async def price_history_status(
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Return per-symbol row counts and date ranges for all stored price history."""
    from sqlalchemy import func, distinct
    config = get_or_create_app_config(db)
    tracked: set = set(config.tracked_symbols or ["USO", "BITO", "QQQ", "SPY"])

    # All symbols with stored history (including previously removed ones)
    stored_symbols = [
        row[0] for row in db.query(distinct(PriceHistory.symbol)).all()
    ]
    symbols = sorted(tracked | set(stored_symbols))

    per_symbol: Dict[str, Any] = {}
    for symbol in symbols:
        q = db.query(PriceHistory).filter(PriceHistory.symbol == symbol)
        count = q.count()
        if count > 0:
            earliest = q.order_by(PriceHistory.date.asc()).first().date
            latest   = q.order_by(PriceHistory.date.desc()).first().date
        else:
            earliest = latest = None
        per_symbol[symbol] = {
            "rows": count,
            "earliest_date": earliest,
            "latest_date": latest,
            "ready": count >= 200,
            "tracked": symbol in tracked,
        }

    return {
        "symbols": per_symbol,
        "total_rows": sum(v["rows"] for v in per_symbol.values()),
        "all_ready": all(v["ready"] for v in per_symbol.values() if v["tracked"]),
    }


@router.post("/admin/price-history/pull", tags=["Admin"])
async def pull_price_history(
    payload: Optional[Dict[str, Any]] = None,
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Pull historical OHLCV for all tracked symbols from yfinance (slow, resumable)."""
    from services.data_ingestion.yfinance_client import PriceClient

    config  = get_or_create_app_config(db)
    symbols: List[str] = list(config.tracked_symbols or ["USO", "BITO", "QQQ", "SPY"])
    if payload and payload.get("symbols"):
        symbols = [str(s).upper().strip() for s in payload["symbols"] if s]
    delay = float((payload or {}).get("delay_seconds", 3.0))

    client  = PriceClient()
    results = await asyncio.to_thread(
        client.pull_and_store_history,
        symbols=symbols,
        db=db,
        delay_seconds=delay,
    )

    total_rows    = sum(r.get("rows", 0) for r in results.values())
    rate_limited  = any(r.get("status") == "rate_limited" for r in results.values())

    return {
        "ok": not rate_limited,
        "rate_limited": rate_limited,
        "symbols": results,
        "total_rows_added": total_rows,
    }
@router.post("/admin/clear-analysis-lock", tags=["Admin"])
async def clear_analysis_lock(
    _admin: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    """Clear the analysis lock to allow new analysis runs.
    
    Use this when an analysis run crashes or gets stuck and leaves
    a stale lock in the database.
    """
    try:
        config = get_or_create_app_config(db)
        
        # Clear all lock fields
        config.analysis_lock_request_id = None
        config.analysis_lock_acquired_at = None
        config.analysis_lock_expires_at = None
        
        db.add(config)
        db.commit()
        
        return {
            "status": "success",
            "message": "Analysis lock cleared successfully"
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear analysis lock: {str(exc)}"
        )
