"""
MaterialityService — gates thesis flips based on article volume, sentiment delta,
and price movement thresholds.

Preserves the exact logic from the original _material_change_gate function
(lines 2923-2988 of the former analysis.py).  The most sensitive part of
the codebase — article_material vs sentiment_delta are NOT flattened.

Data Scoping Note:
  - _rolling_article_baseline queries the DB for the most recent n_runs
    analyses only — this is request-scoped, NOT all-time aggregation.
  - per_symbol_counts is computed from the current post batch only.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from database.models import AnalysisResult
from schemas.analysis import TradingSignal


class MaterialityService:
    """Encapsulates material-change gate logic from the original router."""

    def __init__(self, logic_config: dict[str, Any]) -> None:
        """
        Args:
            logic_config: The full LOGIC config dict (config.logic_loader.LOGIC).
        """
        self._mg = logic_config.get("materiality_gate", {})
        self._hyst = logic_config.get("entry_thresholds", {})

    # ── Public API ───────────────────────────────────────────────────

    def material_change_gate(
        self,
        db: Optional[Session],
        symbols: List[str],
        posts_count: int,
        sentiment_results: Dict[str, Dict[str, Any]],
        price_context: Dict[str, Any],
        quotes_by_symbol: Dict[str, Dict[str, Any]],
        previous_state: Optional[Dict[str, Any]],
        candidate_signal: Optional[TradingSignal],
        min_posts_delta: Optional[int] = None,
        min_sentiment_delta: Optional[float] = None,
        per_symbol_counts: Optional[Dict[str, int]] = None,
    ) -> bool:
        """Return True when a thesis flip is justified by meaningful news or price movement."""
        if previous_state is None:
            return True

        previous_response = previous_state.get("response")
        previous_quotes = previous_state.get("quotes_by_symbol") or {}
        previous_signal = (
            previous_response.get("blue_team_signal") or previous_response.get("trading_signal")
        ) if previous_response else None

        # ── Direction flip override: Any LONG→SHORT or SHORT→LONG flip is inherently material ──
        if candidate_signal and previous_signal and self._any_direction_flip(previous_signal, candidate_signal):
            return True

        # ── Same-direction continuation: always allow regardless of symbol-set changes ──
        if candidate_signal and previous_signal:
            _prev_dir = str(previous_signal.get("signal_type") or "HOLD").upper()
            _cur_dir = str(candidate_signal.signal_type or "HOLD").upper()
            if _prev_dir == _cur_dir and _prev_dir != "HOLD":
                return True

        if not self._signals_differ_materially(previous_signal, candidate_signal):
            return True
        if self._recommendation_structure_changed_without_thesis_flip(previous_signal, candidate_signal):
            return True

        _min_posts = min_posts_delta if min_posts_delta is not None else self._mg.get("min_posts_delta")
        _min_sent = min_sentiment_delta if min_sentiment_delta is not None else self._mg.get("min_sentiment_delta")

        # Dynamic per-symbol article baseline when history is available
        article_material = False
        if db is not None and per_symbol_counts is not None:
            _min_hist = int(self._mg.get("rolling_baseline_min_runs", 5))
            _n_runs = int(self._mg.get("rolling_baseline_runs", 20))
            _mult = float(self._mg.get("rolling_baseline_stddev_multiplier", 1.0))
            baseline = self._rolling_article_baseline(db, symbols, n_runs=_n_runs)
            for sym, count in per_symbol_counts.items():
                b = baseline.get(sym.upper(), {})
                if b.get("n", 0) >= _min_hist:
                    threshold = b["mean"] + _mult * b["stddev"]
                    if count is not None and count > threshold:
                        article_material = True
                        break
                else:
                    # Cold start: fall back to total-posts delta
                    prev_total = int(previous_response.get("posts_scraped", 0) or 0) if previous_response else 0
                    if prev_total is not None and (posts_count or 0) is not None and abs(prev_total - int(posts_count or 0)) >= (_min_posts or 0):
                        article_material = True
                        break
        else:
            posts_delta = (
                abs(int(previous_response.get("posts_scraped", 0) or 0) - int(posts_count or 0))
                if previous_response
                else 999
            )
            article_material = posts_delta >= (_min_posts or 0)

        sentiment_delta = self._max_sentiment_input_delta(sentiment_results, previous_response)
        price_move_pct = self._max_price_move_vs_previous_pct(symbols, quotes_by_symbol, previous_quotes)
        atr_pct = self._max_atr_pct(symbols, price_context)
        material_price_threshold = max(
            self._mg.get("price_move_floor_pct", 0.5),
            min(
                self._mg.get("price_move_ceiling_pct", 5.0),
                atr_pct * self._mg.get("atr_multiplier", 2.0) if atr_pct > 0 else 1.0,
            ),
        )

        return (
            article_material
            or sentiment_delta >= (_min_sent if _min_sent is not None else self._mg.get("min_sentiment_delta", 0.20))
            or price_move_pct >= material_price_threshold
        )

    # ── Helpers (private) ────────────────────────────────────────────

    def _signals_differ_materially(
        self,
        previous_signal: Optional[Dict[str, Any]],
        current_signal: Optional[TradingSignal],
    ) -> bool:
        if previous_signal is None or current_signal is None:
            return True
        if str(previous_signal.get("signal_type") or "HOLD").upper() != str(current_signal.signal_type or "HOLD").upper():
            return True
        prev_map = self._recommendations_by_underlying(previous_signal)
        cur_map = self._recommendations_by_underlying(current_signal if current_signal else previous_signal)
        if set(prev_map.keys()) != set(cur_map.keys()):
            return True
        for symbol in prev_map:
            prev = prev_map.get(symbol) or {}
            cur = cur_map.get(symbol) or {}
            if (
                str(prev.get("action") or "").upper() != str(cur.get("action") or "").upper()
                or str(prev.get("symbol") or "").upper() != str(cur.get("symbol") or "").upper()
                or str(prev.get("leverage") or "") != str(cur.get("leverage") or "")
            ):
                return True
        return False

    def _recommendation_structure_changed_without_thesis_flip(
        self,
        previous_signal: Optional[Dict[str, Any]],
        current_signal: Optional[TradingSignal],
    ) -> bool:
        if previous_signal is None or current_signal is None:
            return False
        if str(previous_signal.get("signal_type") or "HOLD").upper() != str(current_signal.signal_type or "HOLD").upper():
            return False
        prev_map = self._recommendations_by_underlying(previous_signal)
        cur_map = self._recommendations_by_underlying(current_signal)
        if set(prev_map.keys()) != set(cur_map.keys()):
            return False
        structure_changed = False
        for symbol in prev_map:
            prev = prev_map.get(symbol) or {}
            cur = cur_map.get(symbol) or {}
            prev_thesis = str(prev.get("thesis") or "").upper().strip()
            cur_thesis = str(cur.get("thesis") or "").upper().strip()
            if not prev_thesis:
                prev_thesis = "LONG" if str(prev.get("action") or "").upper() == "BUY" else "SHORT"
            if not cur_thesis:
                cur_thesis = "LONG" if str(cur.get("action") or "").upper() == "BUY" else "SHORT"
            if prev_thesis != cur_thesis:
                return False
            if (
                str(prev.get("symbol") or "").upper() != str(cur.get("symbol") or "").upper()
                or str(prev.get("leverage") or "") != str(cur.get("leverage") or "")
            ):
                structure_changed = True
        return structure_changed

    def _recommendations_by_underlying(self, signal: Optional[Any]) -> Dict[str, Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        if signal is None:
            return {}
        if isinstance(signal, dict):
            payload = signal
        elif hasattr(signal, "model_dump"):
            payload = signal.model_dump(mode="json")
        else:
            payload = {
                "recommendations": list(getattr(signal, "recommendations", None) or []),
            }

        recs: Dict[str, Dict[str, Any]] = {}
        for rec in (payload.get("recommendations") or []):
            key = str(rec.get("underlying_symbol") or rec.get("symbol") or "").upper().strip()
            if key:
                recs[key] = rec
        return recs

    def _any_direction_flip(
        self,
        previous_signal: Any,
        current_signal: TradingSignal,
    ) -> bool:
        """Return True if any single recommendation flips direction (LONG↔SHORT)."""
        if previous_signal is None or current_signal is None:
            return False
        prev_map = self._recommendations_by_underlying(previous_signal)
        cur_map = self._recommendations_by_underlying(current_signal)
        for symbol in set(prev_map.keys()) & set(cur_map.keys()):
            prev = prev_map[symbol] or {}
            cur = cur_map[symbol] or {}
            prev_thesis = str(prev.get("thesis") or "").upper().strip()
            cur_thesis = str(cur.get("thesis") or "").upper().strip()
            if not prev_thesis:
                prev_thesis = "LONG" if str(prev.get("action") or "").upper() == "BUY" else "SHORT"
            if not cur_thesis:
                cur_thesis = "LONG" if str(cur.get("action") or "").upper() == "BUY" else "SHORT"
            if prev_thesis and cur_thesis and prev_thesis != cur_thesis:
                return True
        return False

    def _max_sentiment_input_delta(
        self,
        current_sentiment_results: Dict[str, Dict[str, Any]],
        previous_response: Optional[Dict[str, Any]],
    ) -> float:
        if not previous_response or not previous_response.get("sentiment_scores"):
            return 999.0
        deltas: List[float] = []
        for symbol, current in current_sentiment_results.items():
            previous = previous_response["sentiment_scores"].get(symbol)
            if not previous:
                continue
            deltas.append(abs(float(current.get("policy_score", 0.0)) - float(previous.get("policy_change", 0.0))))
            deltas.append(abs(float(current.get("bluster_score", 0.0)) - float(previous.get("market_bluster", 0.0))))
            deltas.append(abs(float(current.get("confidence", 0.0)) - float(previous.get("confidence", 0.0))))
        return max(deltas) if deltas else 999.0

    def _max_price_move_vs_previous_pct(
        self,
        symbols: List[str],
        current_quotes: Optional[Dict[str, Dict[str, Any]]],
        previous_quotes: Optional[Dict[str, Dict[str, Any]]],
    ) -> float:
        if not current_quotes or not previous_quotes:
            return 999.0
        moves: List[float] = []
        for symbol in symbols:
            current_quote = current_quotes.get(symbol) or {}
            previous_quote = previous_quotes.get(symbol) or {}
            current_price = float(current_quote.get("current_price") or 0.0)
            previous_price = float(previous_quote.get("current_price") or 0.0)
            if current_price > 0 and previous_price > 0:
                moves.append(abs(current_price - previous_price) / previous_price * 100.0)
        return max(moves) if moves else 999.0

    def _max_atr_pct(self, symbols: List[str], price_context: Optional[Dict[str, Any]]) -> float:
        if not price_context:
            return 0.0
        atr_values: List[float] = []
        for symbol in symbols:
            indicators = price_context.get(f"technical_indicators_{str(symbol).lower()}") or {}
            try:
                atr_pct = float(indicators.get("atr_14_pct") or 0.0)
            except (TypeError, ValueError):
                atr_pct = 0.0
            if atr_pct > 0:
                atr_values.append(atr_pct)
        return max(atr_values) if atr_values else 0.0

    def _symbol_atr_pct(self, symbol: str, price_context: Optional[Dict[str, Any]]) -> float:
        if not price_context:
            return 0.0
        indicators = price_context.get(f"technical_indicators_{str(symbol).lower()}") or {}
        try:
            atr_pct = float(indicators.get("atr_14_pct") or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return atr_pct if atr_pct > 0 else 0.0

    def _count_symbol_articles(
        self,
        posts: List[Any],
        symbols: List[str],
        relevance_terms: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, int]:
        """Count articles relevant to each symbol by keyword matching."""
        from services.sentiment.prompts import expand_proxy_terms_for_matching, normalize_text_for_matching

        terms_by_symbol = relevance_terms or {}
        counts: Dict[str, int] = {}
        for symbol in symbols:
            sym_upper = symbol.upper()
            terms_raw = terms_by_symbol.get(sym_upper)
            if not terms_raw:
                counts[sym_upper] = len(posts)
                continue
            terms = expand_proxy_terms_for_matching(terms_raw)
            count = 0
            for post in posts:
                text = normalize_text_for_matching(" ".join([
                    str(getattr(post, "title", "") or ""),
                    str(getattr(post, "content", "") or ""),
                    str(getattr(post, "description", "") or ""),
                ]))
                if any(term in text for term in terms):
                    count += 1
            counts[sym_upper] = count
        return counts

    def _rolling_article_baseline(self, db: Session, symbols: List[str], n_runs: int = 20) -> Dict[str, Dict[str, float]]:
        """Compute rolling mean and stddev of per-symbol article counts from recent analysis runs."""
        recent = (
            db.query(AnalysisResult)
            .filter(AnalysisResult.run_metadata.isnot(None))
            .order_by(AnalysisResult.timestamp.desc())
            .limit(n_runs)
            .all()
        )
        history: Dict[str, List[int]] = {s.upper(): [] for s in symbols}
        for row in recent:
            counts = (row.run_metadata or {}).get("per_symbol_article_counts") or {}
            for sym in symbols:
                val = counts.get(sym.upper())
                if val is not None:
                    history[sym.upper()].append(int(val))
        stats: Dict[str, Dict[str, float]] = {}
        for sym, vals in history.items():
            if not vals:
                stats[sym] = {"mean": 0.0, "stddev": 0.0, "n": 0}
            else:
                mean_val = sum(vals) / len(vals)
                stddev = math.sqrt(sum((v - mean_val) ** 2 for v in vals) / len(vals))
                stats[sym] = {"mean": mean_val, "stddev": stddev, "n": len(vals)}
        return stats
