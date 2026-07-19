#!/usr/bin/env python3
# ── export_secrets.py ─────────────────────────────────────────────────────────
# Export secrets from OS keyring to .env format for Docker migration
#
# Usage:
#   python export_secrets.py
#
# Output:
#   Writes secrets to .env.docker.migrated file in current directory
# ─────────────────────────────────────────────────────────────────────────────

import sys

try:
    import keyring
except ImportError:
    print("ERROR: keyring package not installed")
    print("Install with: pip install keyring")
    sys.exit(1)

# Secret keys to export (keyring key -> env var name)
SECRETS = {
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "telegram_authorized_user_id": "TELEGRAM_AUTHORIZED_USER_ID",
    "alpaca_paper_api_key": "ALPACA_PAPER_API_KEY",
    "alpaca_paper_secret_key": "ALPACA_PAPER_SECRET_KEY",
    "alpaca_live_api_key": "ALPACA_LIVE_API_KEY",
    "alpaca_live_secret_key": "ALPACA_LIVE_SECRET_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "google_api_key": "GOOGLE_API_KEY",
}

SERVICE_NAME = "qwen-3.5-9b-getrich"
OUTPUT_FILE = ".env.docker.migrated"

def export_secrets():
    """Export all available secrets from keyring to .env format."""
    exported = []

    with open(OUTPUT_FILE, "w") as f:
        for keyring_key, env_var in SECRETS.items():
            try:
                value = keyring.get_password(SERVICE_NAME, keyring_key)
                if value:
                    f.write(f"{env_var}={value}\n")
                    exported.append(env_var)
                    print(f"  Exported {env_var}")
            except Exception:
                pass

    if exported:
        print(f"\nSuccessfully exported {len(exported)} secrets to {OUTPUT_FILE}")
        return 0
    else:
        print("\nNo secrets found in keyring (or keyring not available)")
        return 1

if __name__ == "__main__":
    sys.exit(export_secrets())
