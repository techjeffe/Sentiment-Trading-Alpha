from __future__ import annotations

import sys
from pathlib import Path


sys.path.append(str(Path(__file__).resolve().parents[1]))

from services import secret_store


class DummyKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, key):
        return self.values.get((service, key))

    def set_password(self, service, key, value):
        self.values[(service, key)] = value

    def delete_password(self, service, key):
        self.values.pop((service, key), None)


def test_save_and_clear_telegram_secrets(monkeypatch):
    dummy = DummyKeyring()
    monkeypatch.setattr(secret_store, "_get_keyring_module", lambda: dummy)
    
    # Also clear environment variable fallbacks
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_AUTHORIZED_USER_ID", raising=False)

    saved = secret_store.save_telegram_secrets("123456:ABCDEF", "987654321", "987654321")
    assert saved["configured"] is True
    assert saved["has_bot_token"] is True
    assert saved["has_chat_id"] is True
    assert saved["has_authorized_user_id"] is True
    assert saved["bot_token_masked"] == "***DEF"
    assert saved["chat_id_masked"] == "***321"
    assert saved["authorized_user_id_masked"] == "***321"

    creds = secret_store.get_telegram_credentials()
    assert creds["bot_token"] == "123456:ABCDEF"
    assert creds["chat_id"] == "987654321"
    assert creds["authorized_user_id"] == "987654321"

    cleared = secret_store.clear_telegram_secrets()
    assert cleared["configured"] is False
    assert cleared["has_bot_token"] is False
    assert cleared["has_chat_id"] is False
    assert cleared["has_authorized_user_id"] is False
