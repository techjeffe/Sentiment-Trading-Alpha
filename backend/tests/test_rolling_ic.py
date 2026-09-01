"""Regression tests for services/rolling_ic:

- Bug: _paired_series used an undefined `bindparam` → NameError, caught by
  fetch_sizing_ic, so IC sizing/derisk never operated once a symbol had rows.
- Bug: ORDER BY started_at ASC + LIMIT returned the OLDEST rows, so the
  trailing window was stale for symbols with >cap rows.

Both are covered by seeding 500 rows (newest 30 strongly IC-positive, older
rows anti-correlated) and asserting the trailing IC reflects the NEWEST data.
Also: fetch_sizing_ic returns strong only when the current window clears its
own (newest) 90th-percentile bar.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database.models import (
    Base,
    DecisionLogBase,
    DecisionLogRun,
    DecisionLogSymbol,
    Trade,
    TradeSnapshot,
)
from services.rolling_ic import fetch_sizing_ic, trailing_ic


@pytest.fixture()
def dbs():
    """In-memory main DB (trades/trade_snapshots) + decision-log DB."""
    main_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=main_engine)
    main = sessionmaker(bind=main_engine)()

    dl_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    DecisionLogBase.metadata.create_all(bind=dl_engine)
    dl = sessionmaker(bind=dl_engine)()

    try:
        yield dl, main
    finally:
        dl.close()
        main.close()


def _seed_history(dl, main, n_total=500, n_newest_correlated=30):
    """
    Rows 0..(n_total - n_newest-1): confidence anti-correlated with return.
    Newest n_newest rows: confidence positively correlated with return.
    Trailing window of 30 should land entirely in the newest correlated block.
    """
    base_ts = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def _conf_for(i):
        # cycle 0.40–0.78 so the older block has real variance (anti-correlated)
        return 0.40 + (i % 40) / 100.0

    for i in range(n_total):
        run_id = f"run-{i:04d}"
        started = base_ts + timedelta(minutes=10 * i)
        dl.add(DecisionLogRun(run_id=run_id, started_at=started))

    newest_start = n_total - n_newest_correlated
    for i in range(n_total):
        run_id = f"run-{i:04d}"
        if i < newest_start:
            confidence = _conf_for(i)
            ret = -confidence * 8.0          # anti-correlated block
        else:
            # correlated: confidence ramps up, return ramps up with it
            confidence = 0.40 + (i - newest_start) / n_newest_correlated * 0.35
            ret = confidence * 8.0
        dl.add(DecisionLogSymbol(
            run_id=run_id,
            symbol="USO",
            blended_confidence_score=confidence,
            blended_directional_score=0.5,
            final_signal_type="LONG",
        ))
        t = Trade(
            analysis_id=1,
            request_id=run_id,
            underlying_symbol="USO",
            symbol="USO",
            action="BUY",
            leverage="1x",
            signal_type="LONG",
            recommended_at=base_ts + timedelta(minutes=10 * i),
            entry_price=100.0,
            entry_price_timestamp=base_ts + timedelta(minutes=10 * i),
        )
        main.add(t)
        main.flush()
        main.add(TradeSnapshot(
            trade_id=t.id,
            horizon_label="1d",
            horizon_minutes=1440,
            target_timestamp=base_ts + timedelta(minutes=10 * i + 1440),
            observed_price=100.0 * (1 + ret / 100.0),
            observed_at=base_ts + timedelta(minutes=10 * i + 1440),
            source_interval="15m",
            raw_return_pct=ret,
            leveraged_return_pct=ret,
        ))
    dl.commit()
    main.commit()


def test_trailing_ic_uses_newest_rows_when_history_exceeds_cap(dbs):
    """500 rows: the trailing 30 must be the newest (correlated) block → IC ≈ +1,
    not the stale oldest/mid block (which is anti-correlated → IC ≈ -1)."""
    dl, main = dbs
    _seed_history(dl, main)
    ic = trailing_ic(dl, main, "USO", horizon="1d", window=30)
    assert ic is not None
    assert ic > 0.5, f"trailing IC should reflect the newest correlated window, got {ic}"


def test_fetch_sizing_ic_strong_uses_newest_percentile(dbs):
    """The newest window clears its own (newest) 90th-pct bar → ic_strong=True."""
    dl, main = dbs
    _seed_history(dl, main)
    pkg = fetch_sizing_ic(dl, main, "USO")
    assert pkg["ic_score"] is not None
    assert pkg["ic_strong"] is True


def test_fetch_sizing_ic_empty_history_neutral(dbs):
    """No rows → ic_score None / ic_strong False (de-risk stays fail-closed)."""
    dl, main = dbs
    pkg = fetch_sizing_ic(dl, main, "NVDA")
    assert pkg["ic_score"] is None
    assert pkg["ic_strong"] is False