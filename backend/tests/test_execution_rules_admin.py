"""Tests for the Admin execution-rules JSON blob:
- update_app_config validate/whitelist round-trip
- runtime merge (refresh_rule_overrides → effective_rule) honored by the rules
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database.models import AppConfig, Base
from services.app_config import get_or_create_app_config, update_app_config


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _restore_rule_overrides():
    """Snapshot/restore the process-wide regime override cache so a UI-save
    test can't leak disabled rules into other test modules."""
    from services import regime
    saved = dict(regime._RULE_OVERRIDES)
    yield
    regime._RULE_OVERRIDES = saved


def _seed(db, **overrides):
    payload = dict(
        id=1, auto_run_enabled=True, auto_run_interval_minutes=30,
        tracked_symbols=["USO", "BITO", "QQQ", "SPY"], custom_symbols=[],
        max_posts=50, include_backtest=True, lookback_days=14,
        risk_profile="moderate", paper_trade_amount=1000.0,
    )
    payload.update(overrides)
    db.add(AppConfig(**payload))
    db.commit()


def test_execution_rules_json_roundtrip_whitelists_keys(db_session):
    _seed(db_session)
    payload = {
        "execution_rules_json": json_dumps({
            "regime_filter": {"enabled": False, "choppy_leverage_cap": 3, "bogus_key": 42},
            "overnight_derisk": {"start_et": "14:00", "require_ic_strong": False},
            "unknown_section": {"enabled": True},
            "counter_trend_cooldown": {"cooldown_hours": 48},
        }),
    }
    cfg = update_app_config(db_session, payload)
    import json
    blob = json.loads(cfg.execution_rules_json)
    assert blob["regime_filter"]["enabled"] is False
    assert blob["regime_filter"]["choppy_leverage_cap"] == 3
    assert "bogus_key" not in blob["regime_filter"]       # unknown key dropped
    assert "unknown_section" not in blob                   # unknown section dropped
    assert blob["overnight_derisk"]["start_et"] == "14:00"
    assert blob["counter_trend_cooldown"]["cooldown_hours"] == 48


def test_execution_rules_json_null_clears(db_session):
    _seed(db_session)
    update_app_config(db_session, {"execution_rules_json": json_dumps({"regime_filter": {"enabled": False}})})
    cfg = get_or_create_app_config(db_session)
    assert cfg.execution_rules_json is not None
    update_app_config(db_session, {"execution_rules_json": None})
    cfg = get_or_create_app_config(db_session)
    assert cfg.execution_rules_json is None


def test_serialized_config_includes_blob_and_defaults(db_session):
    _seed(db_session)
    update_app_config(db_session, {"execution_rules_json": json_dumps({"regime_filter": {"enabled": False}})})
    cfg = get_or_create_app_config(db_session)
    from services.app_config import config_to_dict
    d = config_to_dict(cfg)
    assert d["execution_rules_json"] is not None
    assert d["logic_defaults"]["execution_rules"]["regime_filter"]["chop_ma_spread_pct"] == 1.0
    assert d["logic_defaults"]["execution_rules"]["overnight_derisk"]["start_et"] == "15:00"


def test_override_merge_reaches_runtime_rules(db_session):
    """A UI save flows into effective_rule after refresh_rule_overrides."""
    _seed(db_session)
    update_app_config(db_session, {"execution_rules_json": json_dumps({
        "regime_filter": {"enabled": False, "choppy_leverage_cap": 3},
        "overnight_derisk": {"enabled": False},
    })})
    from services import regime
    regime.refresh_rule_overrides(db_session)

    # Disabled regime filter → no throttle on a choppy market.
    assert regime.effective_rule("regime_filter", "enabled", True) is False
    assert regime.regime_leverage_cap("choppy", 3) == 3
    # Disabled derisk → a late-afternoon 3x position is not force-closed.
    assert regime.effective_rule("overnight_derisk", "enabled", True) is False
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    assert regime.overnight_derisk_required(
        datetime(2025, 6, 12, 19, 30, tzinfo=timezone.utc), "3x", "MEDIUM", False, ZoneInfo("America/New_York")
    ) is False


def json_dumps(v) -> str:
    import json
    return json.dumps(v)