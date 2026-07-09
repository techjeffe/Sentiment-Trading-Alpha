"""
Telegram bot remote control — long-polling, command-only interface.

Security model:
  - Only one private chat_id is allowed.
  - Only one Telegram user_id inside that private chat is allowed.
  - Messages from any other chat_id or sender_id are silently ignored.
  - Only /stop, /start, and /live mutate state. /snapshot requests async delivery.

Commands:
  /status      — report current alpaca_execution_mode
  /stop        — set mode to "off", save previous mode for resumption
  /start       — restore previously saved mode
  /live on     — enable live trading (real orders)
  /live off    — disable live trading, revert to paper mode
  /snapshot    — remotely request current P&L snapshot via Telegram
  /help        — list commands
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

_POLL_TIMEOUT_SECS = 30     # Telegram long-poll window per request
_ERROR_SLEEP_SECS  = 5      # back-off after network errors
_BOT_BASE          = "https://api.telegram.org/bot{token}/{method}"


# ── Low-level Telegram helpers ────────────────────────────────────────────────

def _api(token: str, method: str, **kwargs) -> dict:
    url = _BOT_BASE.format(token=token, method=method)
    response = requests.post(url, json=kwargs, timeout=_POLL_TIMEOUT_SECS + 10)
    response.raise_for_status()
    return response.json()


def _send(token: str, chat_id: str, text: str) -> None:
    try:
        _api(token, "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as exc:
        print(f"[telegram-bot] sendMessage failed: {exc}")


def _send_generic_error(token: str, chat_id: str, action: str) -> None:
    _send(token, chat_id, f"{action} failed. Check backend logs for details.")


def _set_remote_control_banner(config, message: str) -> None:
    config.telegram_remote_control_banner_active = True
    config.telegram_remote_control_banner_message = str(message or "").strip()
    config.telegram_remote_control_banner_updated_at = datetime.now(timezone.utc)


# ── Command handlers ──────────────────────────────────────────────────────────

def _handle_status(token: str, chat_id: str) -> None:
    from database.engine import SessionLocal
    from services.app_config import get_or_create_app_config
    db = SessionLocal()
    try:
        config = get_or_create_app_config(db)
        mode = (config.alpaca_execution_mode or "off").upper()
        _send(token, chat_id, f"Trading mode: <b>{mode}</b>")
    except Exception as exc:
        print(f"[telegram-bot] status error: {exc}")
        _send_generic_error(token, chat_id, "Status request")
    finally:
        db.close()


def _handle_stop(token: str, chat_id: str) -> None:
    from database.engine import SessionLocal
    from services.app_config import get_or_create_app_config, update_app_config
    db = SessionLocal()
    try:
        config = get_or_create_app_config(db)
        current = (config.alpaca_execution_mode or "off").lower()
        if current == "off":
            _send(token, chat_id, "Trading is already stopped.")
            return
        config = update_app_config(db, {"alpaca_execution_mode": "off"})
        # Persist previous mode so /start can restore it
        config.alpaca_pre_stop_mode = current
        _set_remote_control_banner(
            config,
            f"Remote Telegram control stopped trading. Previous mode {current.upper()} was saved for later resume.",
        )
        db.add(config)
        db.commit()
        _send(
            token, chat_id,
            f"Trading stopped. Previous mode (<b>{current.upper()}</b>) saved.\n"
            "Send /start to resume.",
        )
        print(f"[telegram-bot] /stop — execution mode changed from {current} → off")
    except Exception as exc:
        db.rollback()
        print(f"[telegram-bot] stop error: {exc}")
        _send_generic_error(token, chat_id, "Stop command")
    finally:
        db.close()


def _handle_start(token: str, chat_id: str) -> None:
    from database.engine import SessionLocal
    from services.app_config import get_or_create_app_config, update_app_config
    db = SessionLocal()
    try:
        config = get_or_create_app_config(db)
        current = (config.alpaca_execution_mode or "off").lower()
        if current != "off":
            _send(token, chat_id, f"Trading is already active (<b>{current.upper()}</b>).")
            return
        resume_mode = str(getattr(config, "alpaca_pre_stop_mode", None) or "").strip().lower()
        if not resume_mode:
            _send(
                token, chat_id,
                "No previous trading mode saved.\n"
                "Use the admin UI to set paper or live mode.",
            )
            return
        config = update_app_config(db, {"alpaca_execution_mode": resume_mode})
        # Clear saved mode now that it's been consumed
        config.alpaca_pre_stop_mode = None
        _set_remote_control_banner(
            config,
            f"Remote Telegram control resumed trading in {resume_mode.upper()} mode.",
        )
        db.add(config)
        db.commit()
        _send(token, chat_id, f"Trading resumed in <b>{resume_mode.upper()}</b> mode.")
        print(f"[telegram-bot] /start — execution mode restored to {resume_mode}")
    except Exception as exc:
        db.rollback()
        print(f"[telegram-bot] start error: {exc}")
        _send_generic_error(token, chat_id, "Start command")
    finally:
        db.close()


def _handle_live(token: str, chat_id: str, args: list) -> None:
    from database.engine import SessionLocal
    from services.app_config import get_or_create_app_config, update_app_config
    if not args or args[0].lower() not in ("on", "off"):
        _send(token, chat_id, "Usage: /live on  or  /live off")
        return
    action = args[0].lower()
    db = SessionLocal()
    try:
        config = get_or_create_app_config(db)
        current_mode = (config.alpaca_execution_mode or "off").lower()
        current_live = bool(getattr(config, "alpaca_live_trading_enabled", False))
        if action == "on":
            if current_live and current_mode == "live":
                _send(token, chat_id, "Live trading is already enabled.")
                return
            config = update_app_config(db, {
                "alpaca_execution_mode": "live",
                "alpaca_live_trading_enabled": True,
            })
            _set_remote_control_banner(config, "Remote Telegram control enabled live trading.")
            db.add(config)
            db.commit()
            _send(token, chat_id, "⚠️ <b>Live trading ENABLED.</b>\nOrders will hit real markets.")
            print("[telegram-bot] /live on — live trading enabled")
        else:
            if not current_live or current_mode != "live":
                _send(token, chat_id, f"Live trading is already disabled (mode: {current_mode.upper()}).")
                return
            config = update_app_config(db, {
                "alpaca_execution_mode": "paper",
                "alpaca_live_trading_enabled": False,
            })
            _set_remote_control_banner(config, "Remote Telegram control disabled live trading. Reverted to paper mode.")
            db.add(config)
            db.commit()
            _send(token, chat_id, "✅ Live trading <b>disabled</b>. Reverted to paper mode.")
            print("[telegram-bot] /live off — live trading disabled, reverted to paper mode")
    except Exception as exc:
        db.rollback()
        print(f"[telegram-bot] live command error: {exc}")
        _send_generic_error(token, chat_id, "Live trading command")
    finally:
        db.close()


def _handle_snapshot(token: str, chat_id: str) -> None:
    """Request a P&L snapshot delivery to this chat."""
    from database.engine import SessionLocal
    from database.models import AnalysisResult
    from services.remote_snapshot import trigger_remote_snapshot_delivery
    
    db = SessionLocal()
    try:
        latest_analysis = (
            db.query(AnalysisResult)
            .order_by(AnalysisResult.timestamp.desc(), AnalysisResult.id.desc())
            .first()
        )
        if not latest_analysis or not latest_analysis.request_id:
            _send(token, chat_id, "No completed analysis available yet. Run /status to check trading mode.")
            return
        
        _send(token, chat_id, "Snapshot generation initiated. Delivering to Telegram...")
        print(f"[telegram-bot] /snapshot — triggering delivery of analysis {latest_analysis.request_id}")
        trigger_remote_snapshot_delivery(latest_analysis.request_id, force=True)
    except Exception as exc:
        print(f"[telegram-bot] snapshot error: {exc}")
        _send_generic_error(token, chat_id, "Snapshot command")
    finally:
        db.close()


# ── Update dispatcher ─────────────────────────────────────────────────────────

def _dispatch(update: dict, token: str, authorized_chat_id: str, authorized_user_id: str) -> None:
    message = update.get("message")
    if not message:
        return

    chat_id = str(message.get("chat", {}).get("id", ""))
    chat_type = str(message.get("chat", {}).get("type", "")).strip().lower()
    sender_id = str(message.get("from", {}).get("id", ""))
    text = str(message.get("text") or "").strip()

    # Security gate — only one authorized operator in one private chat may control trading.
    # Defense-in-depth: verify both the chat (private + matching ID) AND the sender.
    # In private chats, chat_id == user_id, so sender_id should match both.
    if chat_type != "private":
        print(f"[telegram-bot] rejected: non-private message (chat_type={chat_type})")
        return
    if chat_id != authorized_chat_id:
        print(f"[telegram-bot] rejected: unauthorized chat_id {chat_id} (expected {authorized_chat_id})")
        return
    if sender_id != authorized_user_id:
        print(f"[telegram-bot] rejected: unauthorized sender_id {sender_id} (expected {authorized_user_id})")
        return

    # Parse command and any trailing arguments
    parts = text.split() if text else []
    raw_command = parts[0].lower() if parts else ""
    command = raw_command.split("@")[0]
    args = parts[1:]
    print(f"[telegram-bot] accepted command: {command} {args}")

    if command == "/help":
        _send(token, chat_id, (
            "<b>Remote trading controls</b>\n\n"
            "/status      — current trading mode\n"
            "/stop        — disable all Alpaca trading\n"
            "/start       — resume previous trading mode\n"
            "/live on     — enable live trading (real orders)\n"
            "/live off    — disable live trading → paper mode\n"
            "/snapshot    — request P&L snapshot\n"
            "/help        — show this message"
        ))
    elif command == "/status":
        _handle_status(token, chat_id)
    elif command == "/stop":
        _handle_stop(token, chat_id)
    elif command == "/start":
        _handle_start(token, chat_id)
    elif command == "/live":
        _handle_live(token, chat_id, args)
    elif command == "/snapshot":
        _handle_snapshot(token, chat_id)
    else:
        _send(token, chat_id, "Unknown command. Send /help for available commands.")


# ── Public polling entry-point (called via asyncio.to_thread) ─────────────────

def _get_updates(token: str, offset: int, timeout: int) -> list[dict]:
    result = _api(
        token,
        "getUpdates",
        offset=offset,
        timeout=timeout,
        allowed_updates=["message"],
    )
    return result.get("result", [])


def initialize_offset(token: str) -> int:
    """Discard any pre-existing Telegram backlog and return the next safe offset."""
    try:
        updates = _get_updates(token, 0, 0)
    except Exception as exc:
        print(f"[telegram-bot] initialize_offset error: {exc}")
        return 0

    next_offset = 0
    for update in updates:
        update_id = int(update.get("update_id", 0) or 0)
        next_offset = max(next_offset, update_id + 1)
    return next_offset


def verify_remote_control(token: str, chat_id: str, authorized_user_id: str) -> Dict[str, Any]:
    """Validate bot token, private chat, and authorized operator (defense-in-depth).
    
    Checks:
    - Bot token is valid
    - Chat is private
    - Chat ID matches
    - Authorized user ID is numeric (verified at setup; in private chats chat_id == user_id)
    """
    normalized_chat_id = str(chat_id or "").strip()
    normalized_user_id = str(authorized_user_id or "").strip()
    if not normalized_chat_id.isdigit():
        raise ValueError("chat_id must be a positive numeric Telegram private chat ID")
    if not normalized_user_id.isdigit():
        raise ValueError("authorized_user_id must be a positive numeric Telegram user ID")

    me_payload = _api(token, "getMe")
    chat_payload = _api(token, "getChat", chat_id=normalized_chat_id)
    me = me_payload.get("result", {}) if isinstance(me_payload, dict) else {}
    chat = chat_payload.get("result", {}) if isinstance(chat_payload, dict) else {}

    bot_ok = bool(me_payload.get("ok", True) if isinstance(me_payload, dict) else True)
    chat_type = str(chat.get("type") or "").strip().lower() if isinstance(chat, dict) else ""
    resolved_chat_id = str(chat.get("id") or "").strip() if isinstance(chat, dict) else ""

    is_private_chat = chat_type == "private"
    chat_matches = resolved_chat_id == normalized_chat_id
    user_matches_private_chat = normalized_chat_id == normalized_user_id

    return {
        "ok": bool(bot_ok and is_private_chat and chat_matches and user_matches_private_chat),
        "bot_username": str(me.get("username") or "").strip() if isinstance(me, dict) else "",
        "chat_type": chat_type,
        "chat_id_matches": chat_matches,
        "private_chat_required": is_private_chat,
        "authorized_user_matches_chat": user_matches_private_chat,
        "message": (
            "Telegram remote control verified."
            if bot_ok and is_private_chat and chat_matches and user_matches_private_chat
            else "Telegram setup must use a private chat where chat ID and authorized user ID match."
        ),
    }


def poll_and_dispatch(token: str, authorized_chat_id: str, authorized_user_id: str, offset: int) -> int:
    """
    One long-poll cycle against Telegram getUpdates.
    Blocks for up to _POLL_TIMEOUT_SECS seconds waiting for messages.
    Returns the updated offset for the next call.
    """
    try:
        updates = _get_updates(token, offset, _POLL_TIMEOUT_SECS)
    except Exception as exc:
        # 409 Conflict means another instance is already polling (e.g. hot-reload
        # overlap). Wait longer than the poll window so the ghost connection expires.
        if "409" in str(exc):
            print(f"[telegram-bot] 409 Conflict — another poller active, waiting {_POLL_TIMEOUT_SECS + 5}s for it to expire")
            time.sleep(_POLL_TIMEOUT_SECS + 5)
        else:
            print(f"[telegram-bot] getUpdates error: {exc}")
            time.sleep(_ERROR_SLEEP_SECS)
        return offset

    if updates:
        print(f"[telegram-bot] received {len(updates)} update(s)")
    
    for update in updates:
        update_id = update.get("update_id", 0)
        offset = max(offset, update_id + 1)
        try:
            _dispatch(update, token, authorized_chat_id, authorized_user_id)
        except Exception as exc:
            print(f"[telegram-bot] dispatch error: {exc}")

    return offset
