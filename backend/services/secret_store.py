"""
Cross-platform OS-backed secret storage helpers.
Uses Windows Credential Manager on Windows and Keychain Access on macOS via keyring.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


SECRET_SERVICE_NAME = "qwen-3.5-9b-getrich"
TELEGRAM_BOT_TOKEN_KEY = "telegram_bot_token"
TELEGRAM_CHAT_ID_KEY = "telegram_chat_id"
TELEGRAM_AUTHORIZED_USER_ID_KEY = "telegram_authorized_user_id"

# Per-mode keys (new — paper and live are independent Alpaca accounts)
ALPACA_PAPER_API_KEY_KEY    = "alpaca_paper_api_key"
ALPACA_PAPER_SECRET_KEY_KEY = "alpaca_paper_secret_key"
ALPACA_LIVE_API_KEY_KEY     = "alpaca_live_api_key"
ALPACA_LIVE_SECRET_KEY_KEY  = "alpaca_live_secret_key"

# Legacy single-slot keys kept only for backward-compat reads
ALPACA_API_KEY_KEY    = "alpaca_api_key"
ALPACA_SECRET_KEY_KEY = "alpaca_secret_key"
ALPACA_MODE_KEY       = "alpaca_trading_mode"   # "paper" | "live"

# ── Cloud LLM API keys (per-provider) ─────────────────────────────────
# Multiple providers supported: each stores its own key in the OS keychain.
# "openai" is the legacy slot; new providers use their own slots.
CLOUD_API_KEY_KEYS: Dict[str, str] = {
    "openai":    "openai_api_key",
    "anthropic": "anthropic_api_key",
    "openrouter":"openrouter_api_key",
    "google":    "google_api_key",
    "custom":    "custom_api_key",
}

# Legacy constant for backward compatibility — new code should use
# get_cloud_api_key(provider) / save_cloud_api_key(provider, key) instead.
OPENAI_API_KEY_KEY = "openai_api_key"

# Valid cloud provider identifiers
VALID_CLOUD_PROVIDERS = {"openai", "anthropic", "openrouter", "google", "custom"}


def _resolve_cloud_key_name(provider: str) -> str:
    """Return the OS keychain key name for a cloud provider."""
    p = str(provider or "").strip().lower()
    return CLOUD_API_KEY_KEYS.get(p, OPENAI_API_KEY_KEY)


def _get_keyring_module():
    try:
        import keyring  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "The 'keyring' package is required for secure UI-managed secrets. "
            "Install dependencies to enable OS keychain storage."
        ) from exc
    return keyring


def _mask_secret(value: Optional[str], *, keep: int = 3) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= keep:
        return "*" * len(raw)
    return "***" + raw[-keep:]


def _read_secret(key: str) -> str:
    """Read a secret from keyring, falling back to environment variable."""
    # Try keyring first (non-Docker / local development)
    try:
        keyring = _get_keyring_module()
        value = keyring.get_password(SECRET_SERVICE_NAME, key)
        if value:
            return str(value).strip()
    except Exception:
        pass  # Keyring unavailable (e.g., Docker), fall through to env var

    # Fallback: read from environment variable
    # Map keyring keys to env var names (uppercase, underscore-separated)
    env_var_map = {
        TELEGRAM_BOT_TOKEN_KEY: "TELEGRAM_BOT_TOKEN",
        TELEGRAM_CHAT_ID_KEY: "TELEGRAM_CHAT_ID",
        TELEGRAM_AUTHORIZED_USER_ID_KEY: "TELEGRAM_AUTHORIZED_USER_ID",
        ALPACA_PAPER_API_KEY_KEY: "ALPACA_PAPER_API_KEY",
        ALPACA_PAPER_SECRET_KEY_KEY: "ALPACA_PAPER_SECRET_KEY",
        ALPACA_LIVE_API_KEY_KEY: "ALPACA_LIVE_API_KEY",
        ALPACA_LIVE_SECRET_KEY_KEY: "ALPACA_LIVE_SECRET_KEY",
        ALPACA_API_KEY_KEY: "ALPACA_API_KEY",
        ALPACA_SECRET_KEY_KEY: "ALPACA_SECRET_KEY",
        OPENAI_API_KEY_KEY: "OPENAI_API_KEY",
        CLOUD_API_KEY_KEYS["anthropic"]: "ANTHROPIC_API_KEY",
        CLOUD_API_KEY_KEYS["openrouter"]: "OPENROUTER_API_KEY",
        CLOUD_API_KEY_KEYS["google"]: "GOOGLE_API_KEY",
        CLOUD_API_KEY_KEYS["custom"]: "CUSTOM_API_KEY",
    }
    env_var = env_var_map.get(key, key.upper())
    return os.getenv(env_var, "").strip()


def _write_secret(key: str, value: str) -> None:
    keyring = _get_keyring_module()
    keyring.set_password(SECRET_SERVICE_NAME, key, str(value or "").strip())


def _delete_secret(key: str) -> None:
    keyring = _get_keyring_module()
    try:
        keyring.delete_password(SECRET_SERVICE_NAME, key)
    except Exception:
        # Treat missing secrets as already cleared.
        pass


def get_telegram_secret_status() -> Dict[str, Any]:
    try:
        token = _read_secret(TELEGRAM_BOT_TOKEN_KEY)
        chat_id = _read_secret(TELEGRAM_CHAT_ID_KEY)
        authorized_user_id = _read_secret(TELEGRAM_AUTHORIZED_USER_ID_KEY)
        return {
            "available": True,
            "configured": bool(token and chat_id and authorized_user_id),
            "has_bot_token": bool(token),
            "has_chat_id": bool(chat_id),
            "has_authorized_user_id": bool(authorized_user_id),
            "bot_token_masked": _mask_secret(token),
            "chat_id_masked": _mask_secret(chat_id),
            "authorized_user_id_masked": _mask_secret(authorized_user_id),
            "error": "",
        }
    except Exception as exc:
        return {
            "available": False,
            "configured": False,
            "has_bot_token": False,
            "has_chat_id": False,
            "has_authorized_user_id": False,
            "bot_token_masked": "",
            "chat_id_masked": "",
            "authorized_user_id_masked": "",
            "error": str(exc),
        }


def save_telegram_secrets(bot_token: str, chat_id: str, authorized_user_id: str) -> Dict[str, Any]:
    token = str(bot_token or "").strip()
    chat = str(chat_id or "").strip()
    user_id = str(authorized_user_id or "").strip()
    if not token:
        raise ValueError("bot_token is required")
    if not chat:
        raise ValueError("chat_id is required")
    if not user_id:
        raise ValueError("authorized_user_id is required")
    if not chat.isdigit():
        raise ValueError("chat_id must be a positive numeric Telegram private chat ID")
    if not user_id.isdigit():
        raise ValueError("authorized_user_id must be a positive numeric Telegram user ID")

    _write_secret(TELEGRAM_BOT_TOKEN_KEY, token)
    _write_secret(TELEGRAM_CHAT_ID_KEY, chat)
    _write_secret(TELEGRAM_AUTHORIZED_USER_ID_KEY, user_id)
    return get_telegram_secret_status()


def clear_telegram_secrets() -> Dict[str, Any]:
    _delete_secret(TELEGRAM_BOT_TOKEN_KEY)
    _delete_secret(TELEGRAM_CHAT_ID_KEY)
    _delete_secret(TELEGRAM_AUTHORIZED_USER_ID_KEY)
    return get_telegram_secret_status()


def get_telegram_credentials() -> Dict[str, str]:
    token = _read_secret(TELEGRAM_BOT_TOKEN_KEY)
    chat_id = _read_secret(TELEGRAM_CHAT_ID_KEY)
    authorized_user_id = _read_secret(TELEGRAM_AUTHORIZED_USER_ID_KEY)
    return {
        "bot_token": token,
        "chat_id": chat_id,
        "authorized_user_id": authorized_user_id,
    }


# ── Alpaca ────────────────────────────────────────────────────────────────────

def get_alpaca_credentials_for_mode(mode: str) -> Dict[str, str]:
    """Return (api_key, secret_key) for a specific mode, with legacy fallback."""
    if mode == "live":
        api_key    = _read_secret(ALPACA_LIVE_API_KEY_KEY)
        secret_key = _read_secret(ALPACA_LIVE_SECRET_KEY_KEY)
        if not api_key:
            # Backward compat: old single-slot key stored with mode=live
            old_mode = _read_secret(ALPACA_MODE_KEY) or "paper"
            if old_mode == "live":
                api_key    = _read_secret(ALPACA_API_KEY_KEY)
                secret_key = _read_secret(ALPACA_SECRET_KEY_KEY)
    else:
        api_key    = _read_secret(ALPACA_PAPER_API_KEY_KEY)
        secret_key = _read_secret(ALPACA_PAPER_SECRET_KEY_KEY)
        if not api_key:
            # Backward compat: old single-slot key stored with mode=paper (or unset)
            old_mode = _read_secret(ALPACA_MODE_KEY) or "paper"
            if old_mode == "paper":
                api_key    = _read_secret(ALPACA_API_KEY_KEY)
                secret_key = _read_secret(ALPACA_SECRET_KEY_KEY)
    return {"api_key": api_key or "", "secret_key": secret_key or "", "mode": mode}


def get_alpaca_secret_status() -> Dict[str, Any]:
    try:
        paper = get_alpaca_credentials_for_mode("paper")
        live  = get_alpaca_credentials_for_mode("live")
        paper_ok = bool(paper["api_key"] and paper["secret_key"])
        live_ok  = bool(live["api_key"]  and live["secret_key"])
        return {
            "available":  True,
            "configured": paper_ok or live_ok,
            "paper": {
                "configured":     paper_ok,
                "api_key_masked": _mask_secret(paper["api_key"]),
            },
            "live": {
                "configured":     live_ok,
                "api_key_masked": _mask_secret(live["api_key"]),
            },
            "error": "",
        }
    except Exception as exc:
        return {
            "available":  False,
            "configured": False,
            "paper": {"configured": False, "api_key_masked": ""},
            "live":  {"configured": False, "api_key_masked": ""},
            "error": str(exc),
        }


def save_alpaca_secrets(api_key: str, secret_key: str, mode: str = "paper") -> Dict[str, Any]:
    key    = str(api_key    or "").strip()
    secret = str(secret_key or "").strip()
    m      = str(mode       or "paper").strip().lower()
    if not key:
        raise ValueError("api_key is required")
    if not secret:
        raise ValueError("secret_key is required")
    if m not in ("paper", "live"):
        raise ValueError("mode must be 'paper' or 'live'")

    if m == "paper":
        _write_secret(ALPACA_PAPER_API_KEY_KEY,    key)
        _write_secret(ALPACA_PAPER_SECRET_KEY_KEY, secret)
    else:
        _write_secret(ALPACA_LIVE_API_KEY_KEY,    key)
        _write_secret(ALPACA_LIVE_SECRET_KEY_KEY, secret)
    return get_alpaca_secret_status()


def clear_alpaca_secrets(mode: Optional[str] = None) -> Dict[str, Any]:
    """Clear credentials for a specific mode, or both if mode is None."""
    if mode in (None, "paper"):
        _delete_secret(ALPACA_PAPER_API_KEY_KEY)
        _delete_secret(ALPACA_PAPER_SECRET_KEY_KEY)
    if mode in (None, "live"):
        _delete_secret(ALPACA_LIVE_API_KEY_KEY)
        _delete_secret(ALPACA_LIVE_SECRET_KEY_KEY)
    if mode is None:
        # Also wipe legacy single-slot keys on full clear
        _delete_secret(ALPACA_API_KEY_KEY)
        _delete_secret(ALPACA_SECRET_KEY_KEY)
        _delete_secret(ALPACA_MODE_KEY)
    return get_alpaca_secret_status()


def get_alpaca_credentials() -> Dict[str, str]:
    """Return active credentials: live if configured, else paper (legacy compat)."""
    live = get_alpaca_credentials_for_mode("live")
    if live["api_key"] and live["secret_key"]:
        return live
    return get_alpaca_credentials_for_mode("paper")


# ── Cloud LLM API keys (per-provider) ──────────────────────────────────


def get_cloud_api_key(provider: str) -> str:
    """Return the raw API key for a cloud provider from the OS keychain.

    Falls back to the legacy single-slot openai_api_key for the "openai" provider.
    Returns empty string if no key is stored.
    """
    key_name = _resolve_cloud_key_name(provider)
    return _read_secret(key_name)


def get_cloud_secret_status(provider: str) -> Dict[str, Any]:
    """Return masked status of a cloud provider's API key."""
    try:
        api_key = get_cloud_api_key(provider)
        return {
            "available": True,
            "configured": bool(api_key),
            "api_key_masked": _mask_secret(api_key),
            "error": "",
        }
    except Exception as exc:
        return {
            "available": False,
            "configured": False,
            "api_key_masked": "",
            "error": str(exc),
        }


def save_cloud_api_key(provider: str, api_key: str) -> Dict[str, Any]:
    """Store a cloud provider's API key in the OS keychain."""
    key = str(api_key or "").strip()
    if not key:
        raise ValueError(f"API key is required for {provider}")
    key_name = _resolve_cloud_key_name(provider)
    _write_secret(key_name, key)
    return get_cloud_secret_status(provider)


def clear_cloud_api_key(provider: str) -> Dict[str, Any]:
    """Remove a cloud provider's API key from the OS keychain."""
    key_name = _resolve_cloud_key_name(provider)
    _delete_secret(key_name)
    return get_cloud_secret_status(provider)


# ── Legacy OpenAI-only convenience wrappers ───────────────────────────


def get_openai_secret_status() -> Dict[str, Any]:
    """Legacy: call get_cloud_secret_status('openai') instead."""
    return get_cloud_secret_status("openai")


def save_openai_api_key(api_key: str) -> Dict[str, Any]:
    """Legacy: call save_cloud_api_key('openai', api_key) instead."""
    return save_cloud_api_key("openai", api_key)


def clear_openai_api_key() -> Dict[str, Any]:
    """Legacy: call clear_cloud_api_key('openai') instead."""
    return clear_cloud_api_key("openai")


def get_openai_api_key() -> str:
    """Legacy: call get_cloud_api_key('openai') instead."""
    return get_cloud_api_key("openai")
