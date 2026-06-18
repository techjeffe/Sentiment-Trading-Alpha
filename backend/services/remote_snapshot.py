"""
Remote snapshot rendering and delivery helpers.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from database.engine import SessionLocal
from database.models import AnalysisResult
from services.app_config import get_or_create_app_config
from services.paper_trading import get_summary as get_paper_trading_summary
from services.secret_store import get_telegram_credentials
from services.runtime_health import record_data_pull


def _build_live_trading_summary(db) -> Dict[str, Any]:
    """Compute a P&L summary dict from real Alpaca data (account + AlpacaOrder records)."""
    from database.models import AlpacaOrder
    from services.alpaca_broker import get_broker_from_keychain

    result: Dict[str, Any] = {
        "equity": None,
        "last_equity": None,
        "day_pnl": None,
        "realized_pnl": 0.0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0.0,
        "total_trades": 0,
        "open_positions": 0,
    }

    # Live account stats
    try:
        broker = get_broker_from_keychain(mode="live")
        if broker:
            account = broker.get_account()
            equity = float(account.get("equity") or 0)
            last_equity = float(account.get("last_equity") or 0)
            result["equity"] = equity
            result["last_equity"] = last_equity
            result["day_pnl"] = equity - last_equity
    except Exception as exc:
        print(f"[snapshot] live account fetch failed: {exc}")

    # Realized P&L + win rate from AlpacaOrder round-trips
    try:
        from datetime import timezone as _tz
        _epoch = datetime(1970, 1, 1, tzinfo=_tz.utc)
        orders = (
            db.query(AlpacaOrder)
            .filter(
                AlpacaOrder.trading_mode == "live",
                AlpacaOrder.status == "filled",
                AlpacaOrder.filled_avg_price.isnot(None),
                AlpacaOrder.filled_qty.isnot(None),
                AlpacaOrder.paper_trade_id.isnot(None),
            )
            .all()
        )
        by_trade: Dict[int, list] = {}
        for o in orders:
            by_trade.setdefault(o.paper_trade_id, []).append(o)

        realized_pnl = 0.0
        wins = losses = open_count = 0
        for trade_orders in by_trade.values():
            buys = [o for o in trade_orders if o.side == "buy"]
            sells = [o for o in trade_orders if o.side == "sell"]
            if not (buys and sells):
                open_count += 1
                continue
            buy = max(buys, key=lambda o: o.filled_at or _epoch)
            sell = max(sells, key=lambda o: o.filled_at or _epoch)
            qty = min(float(buy.filled_qty), float(sell.filled_qty))
            if qty <= 0:
                continue
            pnl = (float(sell.filled_avg_price) - float(buy.filled_avg_price)) * qty
            realized_pnl += pnl
            if pnl > 0:
                wins += 1
            else:
                losses += 1

        total = wins + losses
        result["realized_pnl"] = realized_pnl
        result["win_count"] = wins
        result["loss_count"] = losses
        result["win_rate"] = (wins / total * 100) if total > 0 else 0.0
        result["total_trades"] = total
        result["open_positions"] = open_count
    except Exception as exc:
        print(f"[snapshot] live order P&L computation failed: {exc}")

    return result


def _ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_timezone(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return "UTC"
    try:
        ZoneInfo(candidate)
        return candidate
    except Exception:
        return "UTC"


def _format_ts(value: Optional[datetime], timezone_name: str) -> str:
    dt = _ensure_utc(value) or datetime.now(timezone.utc)
    tz = ZoneInfo(_safe_timezone(timezone_name))
    return dt.astimezone(tz).strftime("%Y-%m-%d %I:%M %p %Z")


def _format_money(value: Any) -> str:
    try:
        amount = float(value or 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    sign = "+" if amount > 0 else ""
    return f"{sign}${amount:,.2f}"


def _normalize_recommendation(rec: Dict[str, Any]) -> Dict[str, str]:
    return {
        "underlying_symbol": str(rec.get("underlying_symbol") or rec.get("symbol") or "").upper().strip(),
        "action": str(rec.get("action") or "").upper().strip(),
        "symbol": str(rec.get("symbol") or "").upper().strip(),
        "leverage": str(rec.get("leverage") or "").strip(),
        "thesis": str(rec.get("thesis") or "").upper().strip(),
    }


def _recommendation_key(rec: Dict[str, Any]) -> str:
    normalized = _normalize_recommendation(rec)
    return "|".join(
        [
            normalized["underlying_symbol"],
            normalized["action"],
            normalized["symbol"],
            normalized["leverage"],
            normalized["thesis"],
        ]
    )


def _recommendation_label(rec: Dict[str, Any]) -> str:
    normalized = _normalize_recommendation(rec)
    parts = [normalized["action"], normalized["symbol"], normalized["leverage"]]
    rendered = " ".join(part for part in parts if part).strip()
    return rendered or "No recommendation"


def _recommendation_fingerprint(recommendations: List[Dict[str, Any]]) -> str:
    keys = sorted(_recommendation_key(rec) for rec in recommendations if _recommendation_key(rec))
    return "||".join(keys)


def _recommendation_changes(
    current_recommendations: List[Dict[str, Any]],
    previous_recommendations: List[Dict[str, Any]],
) -> List[str]:
    current_map = {
        _normalize_recommendation(rec)["underlying_symbol"]: _recommendation_label(rec)
        for rec in current_recommendations
        if _normalize_recommendation(rec)["underlying_symbol"]
    }
    previous_map = {
        _normalize_recommendation(rec)["underlying_symbol"]: _recommendation_label(rec)
        for rec in previous_recommendations
        if _normalize_recommendation(rec)["underlying_symbol"]
    }
    changes: List[str] = []
    for symbol in sorted(set(current_map) | set(previous_map)):
        current_label = current_map.get(symbol, "No recommendation")
        previous_label = previous_map.get(symbol, "No recommendation")
        if current_label != previous_label:
            changes.append(f"{symbol}: {previous_label} -> {current_label}")
    return changes


def _filter_closed_trades_since_last_send(
    closed_trades: List[Dict[str, Any]],
    last_sent_at: Optional[datetime],
) -> List[Dict[str, Any]]:
    if last_sent_at is None:
        return list(closed_trades)

    last_sent_utc = _ensure_utc(last_sent_at)
    filtered: List[Dict[str, Any]] = []
    for trade in closed_trades:
        closed_at_raw = trade.get("exited_at") or trade.get("closed_at")
        if not closed_at_raw:
            continue
        try:
            closed_at = closed_at_raw if isinstance(closed_at_raw, datetime) else datetime.fromisoformat(str(closed_at_raw).replace("Z", "+00:00"))
        except Exception:
            continue
        closed_at_utc = _ensure_utc(closed_at)
        if closed_at_utc and last_sent_utc and closed_at_utc > last_sent_utc:
            filtered.append(trade)
    return filtered


def _has_position_changes_since_last_send(
    open_positions: List[Dict[str, Any]],
    closed_trades: List[Dict[str, Any]],
    last_sent_at: Optional[datetime],
) -> bool:
    if last_sent_at is None:
        return False

    last_sent_utc = _ensure_utc(last_sent_at)
    if not last_sent_utc:
        return False

    for pos in open_positions:
        entered_at_raw = pos.get("entered_at")
        if not entered_at_raw:
            continue
        try:
            entered_at = entered_at_raw if isinstance(entered_at_raw, datetime) else datetime.fromisoformat(str(entered_at_raw).replace("Z", "+00:00"))
        except Exception:
            continue
        entered_at_utc = _ensure_utc(entered_at)
        if entered_at_utc and entered_at_utc > last_sent_utc:
            return True

    return bool(closed_trades)


def build_remote_snapshot_payload(db, request_id: Optional[str] = None) -> Dict[str, Any]:
    config = get_or_create_app_config(db)
    query = db.query(AnalysisResult).order_by(AnalysisResult.timestamp.desc(), AnalysisResult.id.desc())
    current = query.filter(AnalysisResult.request_id == request_id).first() if request_id else query.first()
    if not current:
        raise ValueError("No saved analysis runs found")

    signal = current.signal or {}
    recommendations = list(signal.get("recommendations") or [])
    if not recommendations:
        # Not an error — zero-recommendation runs are valid (e.g., all-HOLD,
        # no actionable signals). Return a lightweight sentinel so callers
        # can handle this case without a noisy traceback.
        return {
            "request_id": current.request_id,
            "timestamp": _ensure_utc(current.timestamp),
            "timestamp_label": _format_ts(current.timestamp, _safe_timezone(getattr(config, "display_timezone", "") or "UTC")),
            "timezone": _safe_timezone(getattr(config, "display_timezone", "") or "UTC"),
            "last_sent_at": None,
            "last_sent_label": "",
            "models": {"model_name": "", "extraction_model": "", "reasoning_model": ""},
            "recommendations": [],
            "all_recommendations": [],
            "previous_recommendations": [],
            "recommendation_changes": [],
            "recommendation_fingerprint": "",
            "pnl_summary": {},
            "live_summary": None,
            "positions": [],
            "closed_trades": [],
            "closed_trades_since_last_send": [],
            "market": {},
            "_no_recommendations": True,
        }

    previous = (
        db.query(AnalysisResult)
        .filter(AnalysisResult.id != current.id)
        .order_by(AnalysisResult.timestamp.desc(), AnalysisResult.id.desc())
        .first()
    )
    previous_recommendations = list((previous.signal or {}).get("recommendations") or []) if previous else []

    paper = get_paper_trading_summary(db)
    paper_summary = dict(paper.get("summary") or {})
    open_positions = list(paper.get("open_positions") or [])
    closed_trades = list(paper.get("closed_trades") or [])
    metadata = current.run_metadata or {}
    timezone_name = _safe_timezone(getattr(config, "display_timezone", "") or "UTC")
    max_recommendations = max(1, int(getattr(config, "remote_snapshot_max_recommendations", 4) or 4))
    last_sent_at = _ensure_utc(getattr(config, "last_remote_snapshot_sent_at", None))

    # Gate detection: always filter by time regardless of image-inclusion setting
    closed_trades_since_last_send = _filter_closed_trades_since_last_send(closed_trades, last_sent_at)
    # Image rendering: only include closed trades if the user opted in
    closed_trades_for_render = closed_trades_since_last_send if bool(getattr(config, "remote_snapshot_include_closed_trades", False)) else []

    is_live = getattr(config, "alpaca_execution_mode", None) == "live"
    live_summary = _build_live_trading_summary(db) if is_live else None

    return {
        "request_id": current.request_id,
        "timestamp": _ensure_utc(current.timestamp),
        "timestamp_label": _format_ts(current.timestamp, timezone_name),
        "timezone": timezone_name,
        "last_sent_at": last_sent_at,
        "last_sent_label": _format_ts(last_sent_at, timezone_name) if last_sent_at else "",
        "models": {
            "model_name": str(metadata.get("model_name") or ""),
            "extraction_model": str((metadata.get("dataset_snapshot") or {}).get("extraction_model") or ""),
            "reasoning_model": str((metadata.get("dataset_snapshot") or {}).get("reasoning_model") or ""),
        },
        "recommendations": recommendations[:max_recommendations],
        "all_recommendations": recommendations,
        "previous_recommendations": previous_recommendations,
        "recommendation_changes": _recommendation_changes(recommendations, previous_recommendations),
        "recommendation_fingerprint": _recommendation_fingerprint(recommendations),
        "pnl_summary": paper_summary,
        "live_summary": live_summary,
        "positions": open_positions,
        "closed_trades": closed_trades_for_render,
        "closed_trades_since_last_send": closed_trades_since_last_send,
        "market": dict(paper.get("market") or {}),
    }


def build_remote_snapshot_caption(payload: Dict[str, Any]) -> str:
    live = payload.get("live_summary")
    change_lines = list(payload.get("recommendation_changes") or [])[:3]
    if live:
        day_pnl = live.get("day_pnl")
        equity = live.get("equity")
        realized = live.get("realized_pnl", 0.0)
        win_rate = live.get("win_rate", 0.0)
        pnl_line = (
            f"[LIVE] Equity {_format_money(equity)} | Day P&L {_format_money(day_pnl)} | "
            f"Realized {_format_money(realized)} | Win Rate {win_rate:.1f}%"
        )
    else:
        summary = payload.get("pnl_summary") or {}
        pnl_line = f"Net P&L {_format_money(summary.get('total_pnl'))} | Open {_format_money(summary.get('open_pnl'))} | Realized {_format_money(summary.get('realized_pnl'))}"
    base_lines = [
        f"{payload.get('timestamp_label', '')} | {payload.get('request_id', '')}",
        pnl_line,
    ]
    if change_lines:
        base_lines.extend(change_lines)
    else:
        recs = payload.get("recommendations") or []
        if recs:
            base_lines.extend(_recommendation_label(rec) for rec in recs[:3])
    return "\n".join(line[:1024] for line in base_lines[:5])[:1024]


def _build_snapshot_html(payload: Dict[str, Any]) -> str:
    live = payload.get("live_summary")
    summary = payload.get("pnl_summary") or {}
    recommendations = payload.get("recommendations") or []
    positions = payload.get("positions") or []
    closed_trades = payload.get("closed_trades") or []
    model_bits = payload.get("models") or {}
    closed_section_title = "Closed Since Last Update" if payload.get("last_sent_at") else "Closed Trades"
    closed_empty_label = (
        "No trades have closed since the last snapshot."
        if payload.get("last_sent_at")
        else "No closed trades yet."
    )

    def metric_card(label: str, value: str, accent: str) -> str:
        return (
            f"<div class='metric'><div class='label'>{escape(label)}</div>"
            f"<div class='value {accent}'>{escape(value)}</div></div>"
        )

    recommendation_rows = "".join(
        "<tr>"
        f"<td>{escape(str(rec.get('underlying_symbol') or rec.get('symbol') or ''))}</td>"
        f"<td>{escape(_recommendation_label(rec))}</td>"
        f"<td>{escape(str(rec.get('thesis') or ''))}</td>"
        "</tr>"
        for rec in recommendations
    ) or "<tr><td colspan='3'>No recommendations</td></tr>"

    position_rows = "".join(
        "<tr>"
        f"<td>{escape(str(pos.get('underlying') or ''))}</td>"
        f"<td>{escape(str(pos.get('execution_ticker') or ''))}</td>"
        f"<td>{escape(str(pos.get('signal_type') or ''))}</td>"
        f"<td>{escape(_format_money(pos.get('unrealized_pnl')))}</td>"
        "</tr>"
        for pos in positions
    ) or "<tr><td colspan='4'>No open positions</td></tr>"

    closed_rows = "".join(
        "<tr>"
        f"<td>{escape(str(pos.get('underlying') or ''))}</td>"
        f"<td>{escape(str(pos.get('execution_ticker') or ''))}</td>"
        f"<td>{escape(_format_money(pos.get('realized_pnl')))}</td>"
        "</tr>"
        for pos in closed_trades
    )

    model_label = str(model_bits.get("reasoning_model") or model_bits.get("model_name") or "unknown")
    extraction = str(model_bits.get("extraction_model") or "").strip()
    reasoning = str(model_bits.get("reasoning_model") or "").strip()
    if extraction and reasoning and extraction != reasoning:
        model_label = f"{extraction} -> {reasoning}"

    # Build metric cards — use live account data when available, else paper summary
    if live:
        def _acc(key: str, fallback: float = 0.0) -> float:
            v = live.get(key)
            return float(v) if v is not None else fallback

        metric_cards_html = "".join([
            metric_card("Equity", _format_money(_acc("equity")) if live.get("equity") is not None else "—", "neutral"),
            metric_card("Day P&L", _format_money(_acc("day_pnl")), "positive" if _acc("day_pnl") > 0 else ("negative" if _acc("day_pnl") < 0 else "neutral")),
            metric_card("Realized P&L", _format_money(_acc("realized_pnl")), "positive" if _acc("realized_pnl") > 0 else ("negative" if _acc("realized_pnl") < 0 else "neutral")),
            metric_card("Win Rate", f"{_acc('win_rate'):.1f}%", "neutral"),
        ])
        trades_footer = f"Live trades {int(_acc('total_trades'))} | Open ~{int(_acc('open_positions'))} | Closed {int(_acc('win_count')) + int(_acc('loss_count'))}"
        live_badge_html = "<span style='display:inline-block;margin-left:10px;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;background:#be123c;color:#fff;'>LIVE</span>"
    else:
        metric_cards_html = "".join([
            metric_card("Net P&L", _format_money(summary.get("total_pnl")), "positive" if float(summary.get("total_pnl") or 0) > 0 else ("negative" if float(summary.get("total_pnl") or 0) < 0 else "neutral")),
            metric_card("Open P&L", _format_money(summary.get("open_pnl")), "positive" if float(summary.get("open_pnl") or 0) > 0 else ("negative" if float(summary.get("open_pnl") or 0) < 0 else "neutral")),
            metric_card("Realized P&L", _format_money(summary.get("realized_pnl")), "positive" if float(summary.get("realized_pnl") or 0) > 0 else ("negative" if float(summary.get("realized_pnl") or 0) < 0 else "neutral")),
            metric_card("Win Rate", f"{float(summary.get('win_rate') or 0):.1f}%", "neutral"),
        ])
        trades_footer = f"Total trades {int(summary.get('total_trades') or 0)} | Open {int(summary.get('open_positions') or 0)} | Closed {int(summary.get('closed_trades') or 0)}"
        live_badge_html = ""

    return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background: {
          "linear-gradient(135deg, #1a0010 0%, #3b0020 60%, #5c0030 100%)"
          if live else
          "linear-gradient(135deg, #07111f 0%, #102238 60%, #173253 100%)"
      };
      color: #f8fafc;
    }}
    .wrap {{ width: 920px; padding: 28px; }}
    .panel {{
      background: rgba(10, 18, 34, 0.88);
      border: 1px solid {'"rgba(190,18,60,0.35)"' if live else '"rgba(148,163,184,0.18)"'};
      border-radius: 22px;
      padding: 24px;
      box-shadow: 0 18px 60px rgba(0, 0, 0, 0.3);
    }}
    .header {{ display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; }}
    .eyebrow {{ font-size: 12px; letter-spacing: 0.18em; text-transform: uppercase; color: #94a3b8; }}
    h1 {{ margin: 10px 0 6px; font-size: 34px; line-height: 1.05; }}
    .sub {{ color: #cbd5e1; font-size: 14px; }}
    .req {{ text-align: right; font-size: 13px; color: #cbd5e1; }}
    .metrics {{
      margin-top: 20px;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }}
    .metric {{
      background: rgba(15, 23, 42, 0.85);
      border: 1px solid rgba(148, 163, 184, 0.12);
      border-radius: 16px;
      padding: 14px 16px;
    }}
    .label {{ color: #94a3b8; font-size: 11px; text-transform: uppercase; letter-spacing: 0.14em; }}
    .value {{ margin-top: 10px; font-size: 24px; font-weight: 700; }}
    .positive {{ color: #4ade80; }}
    .negative {{ color: #f87171; }}
    .neutral {{ color: #f8fafc; }}
    .grid {{
      margin-top: 20px;
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 16px;
    }}
    .section {{
      background: rgba(15, 23, 42, 0.72);
      border: 1px solid rgba(148, 163, 184, 0.12);
      border-radius: 18px;
      padding: 18px;
    }}
    .section h2 {{
      margin: 0 0 12px;
      font-size: 13px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: #cbd5e1;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    td, th {{ padding: 8px 4px; border-bottom: 1px solid rgba(148, 163, 184, 0.12); text-align: left; }}
    th {{ color: #94a3b8; font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; }}
    .changes {{ margin: 0; padding-left: 18px; color: #e2e8f0; font-size: 13px; line-height: 1.6; }}
    .footer {{ margin-top: 16px; display: flex; justify-content: space-between; color: #94a3b8; font-size: 12px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <div class="header">
        <div>
          <div class="eyebrow">Remote Snapshot</div>
          <h1>Recommendations + P&amp;L {live_badge_html}</h1>
          <div class="sub">{escape(str(payload.get("timestamp_label") or ""))} | Model {escape(model_label)}</div>
        </div>
        <div class="req">
          <div>Request</div>
          <div><strong>{escape(str(payload.get("request_id") or ""))}</strong></div>
          <div style="margin-top:8px;">Market {escape(str((payload.get("market") or {}).get("label") or "Unknown"))}</div>
        </div>
      </div>
      <div class="metrics">
        {metric_cards_html}
      </div>
      <div class="grid">
        <div class="section">
          <h2>Latest Recommendations</h2>
          <table>
            <thead><tr><th>Underlying</th><th>Recommendation</th><th>Thesis</th></tr></thead>
            <tbody>{recommendation_rows}</tbody>
          </table>
        </div>
        <div class="section">
          <h2>What Changed</h2>
          <ul class="changes">
            {''.join(f"<li>{escape(change)}</li>" for change in (payload.get("recommendation_changes") or [])[:5]) or "<li>No recommendation change vs previous run.</li>"}
          </ul>
        </div>
      </div>
      <div class="grid">
        <div class="section">
          <h2>Open Positions</h2>
          <table>
            <thead><tr><th>Underlying</th><th>Ticker</th><th>Side</th><th>Unrealized</th></tr></thead>
            <tbody>{position_rows}</tbody>
          </table>
        </div>
        <div class="section">
          <h2>{escape(closed_section_title)}</h2>
          <table>
            <thead><tr><th>Underlying</th><th>Ticker</th><th>Realized</th></tr></thead>
            <tbody>{closed_rows or f"<tr><td colspan='3'>{escape(closed_empty_label)}</td></tr>"}</tbody>
          </table>
        </div>
      </div>
      <div class="footer">
        <div>{trades_footer}</div>
        <div>Private outbound delivery only</div>
      </div>
    </div>
  </div>
</body>
</html>
"""


def check_snapshot_renderer() -> Dict[str, Any]:
    """Verify the Playwright Chromium browser needed for PNG snapshots is installed.

    Returns a dict with ``available`` (bool) and a human-readable ``detail``.
    The Python ``playwright`` package being importable is NOT sufficient — the
    browser binary is downloaded separately via ``playwright install chromium``.
    Snapshot delivery silently fails without it, while text-only Telegram
    commands (e.g. /status) keep working, which makes the gap easy to miss.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "available": False,
            "detail": f"playwright package not importable: {exc}",
            "remedy": "pip install -r requirements.txt",
        }

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
        return {"available": True, "detail": "Chromium renderer available"}
    except Exception as exc:
        return {
            "available": False,
            "detail": f"Chromium browser not launchable: {str(exc).splitlines()[0]}",
            "remedy": "python -m playwright install chromium",
        }


def render_remote_snapshot_png(payload: Dict[str, Any]) -> bytes:
    html = _build_snapshot_html(payload)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(f"Playwright is unavailable for remote snapshot rendering: {exc}") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 960, "height": 1200}, device_scale_factor=2)
        page.set_content(html, wait_until="domcontentloaded")
        png_bytes = page.locator("body").screenshot(type="png")
        browser.close()
        return png_bytes


def _deliver_via_telegram_photo(png_bytes: bytes, caption: str) -> Dict[str, Any]:
    token = ""
    chat_id = ""
    try:
        creds = get_telegram_credentials()
        token = str(creds.get("bot_token") or "").strip()
        chat_id = str(creds.get("chat_id") or "").strip()
    except Exception:
        token = ""
        chat_id = ""
    if not token:
        token = str(os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not chat_id:
        chat_id = str(os.getenv("TELEGRAM_CHAT_ID", "") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram secrets are not configured. Save them in the Admin UI or set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    last_error = None
    for _ in range(2):
        try:
            response = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": ("remote-snapshot.png", png_bytes, "image/png")},
                timeout=20,
            )
            if response.ok:
                return {"mode": "telegram", "delivered": True}
            last_error = f"{response.status_code}: {response.text[:300]}"
            if response.status_code not in {429, 500, 502, 503, 504}:
                break
        except Exception as exc:
            last_error = str(exc)
    raise RuntimeError(f"Telegram delivery failed: {last_error}")


def deliver_remote_snapshot(png_bytes: bytes, caption: str) -> Dict[str, Any]:
    return _deliver_via_telegram_photo(png_bytes, caption)


def should_send_remote_snapshot(config, payload: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    current_request_id = str(payload.get("request_id") or "").strip()
    if getattr(config, "last_remote_snapshot_request_id", None) == current_request_id and getattr(config, "last_remote_snapshot_sent_at", None):
        return {
            "should_send": False,
            "reason": "already_sent_for_request",
            "position_changed": False,
            "interval_elapsed": False,
        }

    last_sent_at = _ensure_utc(getattr(config, "last_remote_snapshot_sent_at", None))
    interval_minutes = max(
        15,
        int(
            getattr(
                config,
                "remote_snapshot_interval_minutes",
                getattr(config, "remote_snapshot_heartbeat_minutes", 360),
            ) or 360
        ),
    )
    interval_elapsed = last_sent_at is None or (now - last_sent_at) >= timedelta(minutes=interval_minutes)
    position_changed = False
    if bool(getattr(config, "remote_snapshot_send_on_position_change", True)):
        position_changed = _has_position_changes_since_last_send(
            list(payload.get("positions") or []),
            list(payload.get("closed_trades_since_last_send") or payload.get("closed_trades") or []),
            last_sent_at,
        )
    return {
        "should_send": bool(interval_elapsed or position_changed),
        "reason": "position_change" if position_changed else "interval",
        "position_changed": position_changed,
        "interval_elapsed": interval_elapsed,
        "current_pnl": float((payload.get("pnl_summary") or {}).get("total_pnl") or 0.0),
    }


def process_remote_snapshot_delivery(request_id: str, force: bool = False) -> None:
    db = SessionLocal()
    try:
        config = get_or_create_app_config(db)
        if not bool(getattr(config, "remote_snapshot_enabled", False)) and not force:
            return

        payload = build_remote_snapshot_payload(db, request_id=request_id)
        # Silently skip when the analysis produced zero recommendations —
        # there is nothing meaningful to snapshot or deliver.
        if payload.get("_no_recommendations"):
            return
        gate = should_send_remote_snapshot(config, payload)
        if not gate["should_send"] and not force:
            record_data_pull(
                status="ok",
                source="remote_snapshot",
                summary=f"Remote snapshot skipped for {request_id}",
                details=gate,
                error=None,
            )
            return

        if force:
            gate = {
                **gate,
                "should_send": True,
                "reason": "forced_test_delivery",
                "forced": True,
            }

        caption = build_remote_snapshot_caption(payload)
        png_bytes = render_remote_snapshot_png(payload)
        delivery = deliver_remote_snapshot(png_bytes, caption)

        config.last_remote_snapshot_sent_at = datetime.now(timezone.utc)
        config.last_remote_snapshot_request_id = request_id
        config.last_remote_snapshot_net_pnl = float((payload.get("pnl_summary") or {}).get("total_pnl") or 0.0)
        config.last_remote_snapshot_recommendation_fingerprint = str(payload.get("recommendation_fingerprint") or "")
        db.add(config)
        db.commit()

        record_data_pull(
            status="ok",
            source="remote_snapshot",
            summary=f"Delivered remote snapshot via {delivery.get('mode', 'unknown')}",
            details={
                "request_id": request_id,
                "gate": gate,
                "delivery": delivery,
            },
            error=None,
        )
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        record_data_pull(
            status="error",
            source="remote_snapshot",
            summary=f"Remote snapshot delivery failed for {request_id}",
            details={"request_id": request_id},
            error=str(exc),
        )
        print(f"Remote snapshot delivery error for {request_id}: {exc}")
    finally:
        db.close()


def trigger_remote_snapshot_delivery(request_id: str, force: bool = False) -> None:
    thread = threading.Thread(target=process_remote_snapshot_delivery, args=(request_id, force), daemon=True)
    thread.start()
