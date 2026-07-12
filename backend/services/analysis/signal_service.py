"""
SignalService — trading signal generation and red-team consensus logic.

Encapsulates _generate_trading_signal, _build_consensus_trading_signal,
and all leverage/conviction helpers from the original router.  Uses Pydantic
schemas (TradingSignal) between service layers to avoid circular imports.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from config.logic_loader import LOGIC
from schemas.analysis import RedTeamDebug, RedTeamReview, TradingSignal
from services.sentiment.engine import SentimentEngine
from services.trading_instruments import build_execution_recommendation
from services.trading_instruments import INSTRUMENT_SPECS
from services.data_ingestion.yfinance_client import PriceClient


def _sigmoid_size_pct(
    abs_score: float,
    midpoint: float = 0.42,
    steepness: float = 6.0,
    min_size: float = 10.0,
    max_size: float = 100.0,
    skip_floor: float = 0.10,
) -> float:
    """
    Map |directional_score| to a position size percentage via sigmoid.

    At |score| = midpoint => 50% of full size.
    Below skip_floor => 0% (skip entirely).
    """
    if abs_score < skip_floor:
        return 0.0
    return min_size + (max_size - min_size) / (1.0 + math.exp(-steepness * (abs_score - midpoint)))


def _compute_ramp_stage(
    current_stage: str,
    promotion_allowed: bool,
    promotion_thresholds: dict,
    directional_score: float,
    confidence: float,
) -> str:
    """Advance ramp stage based on signal strength. Never demotes."""
    if not promotion_allowed:
        return current_stage
    score = abs(directional_score)
    stage = str(current_stage or "probe")
    if stage == "probe":
        t = promotion_thresholds.get("probe_to_building", {})
        if score >= float(t.get("min_directional_score", 0.50)) and confidence >= float(t.get("min_confidence", 0.70)):
            return "building"
    elif stage == "building":
        t = promotion_thresholds.get("building_to_full", {})
        if score >= float(t.get("min_directional_score", 0.65)) and confidence >= float(t.get("min_confidence", 0.80)):
            return "full"
    return stage


def _decay_factor(age_hours: float, half_life: float, min_factor: float = 0.10) -> float:
    """Exponential decay: factor = max(min_factor, 0.5^(age/half_life))."""
    if age_hours <= 0.0:
        return 1.0
    raw = 0.5 ** (age_hours / half_life)
    return max(min_factor, raw)


class SignalService:
    """Encapsulates blue-team signal generation and red-team consensus logic."""

    def __init__(
        self,
        logic_config: dict[str, Any],
        continuous_entry_enabled: Optional[bool] = None,
        regime_adaptation_enabled: Optional[bool] = None,
        hold_decay_enabled: Optional[bool] = None,
    ) -> None:
        self._L = logic_config
        self._continuous_entry_enabled = continuous_entry_enabled
        self._regime_adaptation_enabled = regime_adaptation_enabled
        self._hold_decay_enabled = hold_decay_enabled
        self._crazy = logic_config.get("crazy", {})

    # ── Crazy profile helpers ────────────────────────────────────────

    def _crazy_entry_thresholds(self) -> dict:
        """Return entry threshold overrides for crazy profile, or empty dict."""
        return dict(self._crazy.get("entry_thresholds") or {})

    def _crazy_continuous_entry(self) -> dict:
        """Return continuous entry overrides for crazy profile, or empty dict."""
        return dict(self._crazy.get("continuous_entry") or {})

    def _crazy_conviction(self) -> dict:
        """Return conviction threshold overrides for crazy profile, or empty dict."""
        return dict(self._crazy.get("conviction") or {})

    # ── Public API ───────────────────────────────────────────────────

    def generate_trading_signal(
        self,
        sentiment_results: Dict[str, Dict],
        quotes_by_symbol: Optional[Dict[str, Dict[str, Any]]] = None,
        risk_profile: str = "aggressive",
        previous_signal: Optional[TradingSignal] = None,
        stability_mode: str = "normal",
        entry_threshold_override: Optional[float] = None,
        price_context: Optional[Dict[str, Any]] = None,
        signal_age_hours: float = 0.0,
        crazy_ramp_context: Optional[Dict[str, Any]] = None,
        previous_posts_count: Optional[int] = None,
        current_posts_count: Optional[int] = None,
    ) -> TradingSignal:
        """Generate a blue-team TradingSignal from sentiment results."""
        if not sentiment_results:
            return TradingSignal(
                signal_type="HOLD", confidence_score=0.0,
                entry_symbol="USO",
                stop_loss_pct=self._L["stop_loss_pct"],
                take_profit_pct=self._L["take_profit_pct"],
                urgency="LOW",
                conviction_level="LOW",
                holding_period_hours=self._L["conviction"]["holding_minutes"]["VOLATILE_EVENT"] / 60,
                trading_type="VOLATILE_EVENT",
                action_if_already_in_position="HOLD"
            )

        symbols = list(sentiment_results.keys())
        recommendations: List[Dict[str, str]] = []
        urgency_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        overall_urgency = "LOW"
        strongest_symbol = symbols[0] if symbols else "USO"
        strongest_execution_symbol = strongest_symbol
        strongest_score = -1.0
        net_direction_score = 0.0
        total_weight = 0.0
        long_recommendations = 0
        short_recommendations = 0
        hold_recommendations = 0
        previous_recommendations = self._recommendations_by_underlying(previous_signal)
        is_crazy = str(risk_profile or "").lower().strip() == "crazy"
        _et = self._L["entry_thresholds"]
        _crazy_et = self._crazy_entry_thresholds()
        is_closed_hysteresis = stability_mode == "closed_market_hysteresis"
        # Crazy profile uses its own entry thresholds
        if is_crazy and _crazy_et:
            entry_threshold = _crazy_et["closed_market"] if is_closed_hysteresis else (entry_threshold_override or _crazy_et["normal"])
            keep_threshold = _crazy_et["keep_closed_market"] if is_closed_hysteresis else (entry_threshold_override or _crazy_et["keep_normal"])
        else:
            entry_threshold = _et["closed_market"] if is_closed_hysteresis else (entry_threshold_override or _et["normal"])
            keep_threshold = _et["keep_closed_market"] if is_closed_hysteresis else (entry_threshold_override or _et["keep_normal"])

        # ── Regime adaptation: adjust entry threshold based on volatility ──
        _ra = self._L.get("regime_adaptation", {})
        _regime_adaptation_active = self._regime_adaptation_enabled if self._regime_adaptation_enabled is not None else _ra.get("enabled", True)
        if _regime_adaptation_active:
            _max_atr = self._max_atr_pct(symbols, price_context)
            if _max_atr >= float(_ra.get("high_vol_atr_pct", 3.0)):
                entry_threshold *= float(_ra.get("high_vol_multiplier", 1.25))
            elif _max_atr <= float(_ra.get("low_vol_atr_pct", 1.0)):
                entry_threshold *= float(_ra.get("low_vol_multiplier", 0.80))

        # ── Continuous entry config ──
        _ce = self._L.get("continuous_entry", {})
        _crazy_ce = self._crazy_continuous_entry()
        if is_crazy and _crazy_ce:
            _ce = {**_ce, **_crazy_ce}
        _ce_enabled = self._continuous_entry_enabled if self._continuous_entry_enabled is not None else _ce.get("enabled", True)

        for sym, result in sentiment_results.items():
            directional = result.get('directional_score', 0.0)
            confidence = result['confidence']
            specialist_signal = str(result.get('signal_type', 'HOLD')).upper()
            specialist_urgency = str(result.get('urgency', 'LOW')).upper()
            previous_rec = previous_recommendations.get(sym) or {}
            previous_action = str(previous_rec.get("action", "") or "").upper().strip()

            # ── Decay: use separate hold half-life for existing positions ──
            has_existing = bool(previous_action)
            
            # Calculate P&L for momentum-adjusted decay
            _pnl_pct = 0.0
            if has_existing and quotes_by_symbol:
                _prev_price = float(previous_rec.get("entry_price", 0.0) or 0.0)
                _current_quote = quotes_by_symbol.get(sym, {}) or quotes_by_symbol.get(
                    previous_rec.get("symbol", ""), {}
                )
                _current_price = float(_current_quote.get("current_price", 0.0) or 0.0)
                if _prev_price > 0 and _current_price > 0:
                    if previous_action == "BUY":
                        _pnl_pct = (_current_price - _prev_price) / _prev_price * 100.0
                    else:  # SELL/SHORT
                        _pnl_pct = (_prev_price - _current_price) / _prev_price * 100.0
            
            decay_factor = self._compute_decay_factor(sym, signal_age_hours, _pnl_pct)
            hold_decay_factor = self._compute_hold_decay_factor(sym, signal_age_hours)
            effective_directional = directional * decay_factor if directional is not None else directional
            effective_hold_directional = directional * hold_decay_factor if directional is not None else directional

            if _ce_enabled:
                # ── Continuous entry sizing ──
                _abs = abs(effective_directional) if effective_directional is not None else 0.0
                _ce_mid = float(_ce.get("sigmoid_midpoint", 0.42))
                _ce_steep = float(_ce.get("sigmoid_steepness", 6.0))
                _ce_min = float(_ce.get("min_size_pct", 10.0))
                _ce_max = float(_ce.get("max_size_pct", 100.0))
                _ce_skip = float(_ce.get("skip_floor", 0.10))
                _ce_keep = float(_ce.get("keep_floor", 0.10))

                if effective_directional is not None and effective_directional <= -_ce_skip:
                    # Bearish direction
                    if has_existing and previous_action in ("SELL", "BUY") and effective_hold_directional >= -_ce_keep:
                        # Shrink existing position proportionally instead of closing
                        _entry_sz = _sigmoid_size_pct(abs(effective_directional), _ce_mid, _ce_steep, _ce_min, _ce_max, 0.0)
                        _prev_sz = float(previous_rec.get("size_pct", _ce_max) or _ce_max)
                        size_pct = max(_ce_min, min(_ce_max, _entry_sz * _prev_sz / max(_ce_mid, 0.01)))
                        action = "SELL"
                        urgency = specialist_urgency if specialist_signal == "SHORT" else ("HIGH" if abs(effective_directional) > 0.7 else "MEDIUM")
                        short_recommendations += 1
                    elif effective_directional <= -entry_threshold or (previous_action == "SELL" and effective_directional <= -keep_threshold):
                        size_pct = _sigmoid_size_pct(_abs, _ce_mid, _ce_steep, _ce_min, _ce_max, _ce_skip)
                        action = "SELL"
                        urgency = specialist_urgency if specialist_signal == "SHORT" else ("HIGH" if abs(effective_directional) > 0.7 else "MEDIUM")
                        short_recommendations += 1
                    elif has_existing and previous_action == "SELL" and effective_hold_directional >= -_ce_keep:
                        # Score dipped but still above keep floor — hold existing position
                        _entry_sz = _sigmoid_size_pct(_abs, _ce_mid, _ce_steep, _ce_min, _ce_max, 0.0)
                        _prev_sz = float(previous_rec.get("size_pct", _ce_max) or _ce_max)
                        size_pct = max(_ce_min, min(_ce_max, _entry_sz * _prev_sz / max(_ce_mid, 0.01)))
                        action = "SELL"
                        urgency = "LOW"
                        short_recommendations += 1
                    else:
                        action = ""
                        size_pct = 0.0
                        urgency = specialist_urgency if specialist_signal == "HOLD" else "LOW"
                        hold_recommendations += 1
                elif effective_directional is not None and effective_directional >= _ce_skip:
                    # Bullish direction
                    if has_existing and previous_action in ("BUY",) and effective_hold_directional <= _ce_keep:
                        # Shrink existing position proportionally
                        _entry_sz = _sigmoid_size_pct(_abs, _ce_mid, _ce_steep, _ce_min, _ce_max, 0.0)
                        _prev_sz = float(previous_rec.get("size_pct", _ce_max) or _ce_max)
                        size_pct = max(_ce_min, min(_ce_max, _entry_sz * _prev_sz / max(_ce_mid, 0.01)))
                        action = "BUY"
                        urgency = "LOW"
                        long_recommendations += 1
                    elif effective_directional >= entry_threshold or (previous_action == "BUY" and effective_directional >= keep_threshold):
                        size_pct = _sigmoid_size_pct(_abs, _ce_mid, _ce_steep, _ce_min, _ce_max, _ce_skip)
                        action = "BUY"
                        urgency = specialist_urgency if specialist_signal == "LONG" else ("HIGH" if effective_directional > 0.7 else "MEDIUM")
                        long_recommendations += 1
                    elif has_existing and previous_action == "BUY" and effective_hold_directional <= keep_threshold:
                        # Score dipped but still positive — hold existing position
                        _entry_sz = _sigmoid_size_pct(_abs, _ce_mid, _ce_steep, _ce_min, _ce_max, 0.0)
                        _prev_sz = float(previous_rec.get("size_pct", _ce_max) or _ce_max)
                        size_pct = max(_ce_min, min(_ce_max, _entry_sz * _prev_sz / max(_ce_mid, 0.01)))
                        action = "BUY"
                        urgency = "LOW"
                        long_recommendations += 1
                    else:
                        action = ""
                        size_pct = 0.0
                        urgency = specialist_urgency if specialist_signal == "HOLD" else "LOW"
                        hold_recommendations += 1
                else:
                    action = ""
                    size_pct = 0.0
                    urgency = specialist_urgency if specialist_signal == "HOLD" else "LOW"
                    hold_recommendations += 1
            else:
                # ── Legacy binary entry gate (continuous_entry disabled) ──
                if effective_directional is not None and (
                    effective_directional <= -entry_threshold or
                    (previous_action == "SELL" and effective_directional <= -keep_threshold)
                ):
                    action = "SELL"
                    size_pct = 100.0
                    urgency = specialist_urgency if specialist_signal == "SHORT" else ("HIGH" if abs(effective_directional) > 0.7 else "MEDIUM")
                    short_recommendations += 1
                elif effective_directional is not None and (
                    effective_directional >= entry_threshold or
                    (previous_action == "BUY" and effective_directional >= keep_threshold)
                ):
                    action = "BUY"
                    size_pct = 100.0
                    urgency = specialist_urgency if specialist_signal == "LONG" else ("HIGH" if effective_directional > 0.7 else "MEDIUM")
                    long_recommendations += 1
                else:
                    action = ""
                    size_pct = 0.0
                    urgency = specialist_urgency if specialist_signal == "HOLD" else "LOW"
                    hold_recommendations += 1

            leverage = self._resolve_leverage(
                confidence,
                risk_profile,
                action=action,
                atr_pct=self._symbol_atr_pct(sym, price_context),
            )
            recommendation = None
            if action:
                recommendation = build_execution_recommendation(sym, action, leverage)
                recommendation["size_pct"] = str(round(size_pct, 1))
                if str(risk_profile or "").lower().strip() == "crazy":
                    sym_ctx = ((crazy_ramp_context or {}).get("symbols") or {}).get(sym.upper(), {})
                    _prev_stage = str(previous_rec.get("ramp_stage") or "probe")
                    _promotion_allowed = bool(sym_ctx.get("promotion_allowed", False))
                    _promotion_thresholds = dict(sym_ctx.get("promotion_thresholds") or {})
                    recommendation["ramp_stage"] = _compute_ramp_stage(
                        _prev_stage, _promotion_allowed, _promotion_thresholds,
                        directional, confidence,
                    )
                    recommendation["ramp_threshold_bucket"] = str(sym_ctx.get("ramp_threshold_bucket", "") or "")
                    recommendation["threshold_source"] = str(sym_ctx.get("threshold_source", "fallback") or "fallback")
                    recommendation["fetch_latency_ms"] = str(sym_ctx.get("fetch_latency_ms", 0))
                    recommendation["fetch_timeout_hit"] = str(bool(sym_ctx.get("fetch_timeout_hit", False))).lower()
                    recommendation["ramp_promotion_enabled"] = str(_promotion_allowed).lower()
                recommendations.append(recommendation)

            conviction = abs(directional) * confidence
            actual_leverage_label = recommendation["leverage"] if recommendation else leverage
            leverage_weight = float(actual_leverage_label.lower().replace("x", "")) if actual_leverage_label else 1.0
            directional_weight = max(abs(directional), 0.1) * leverage_weight
            net_direction_score += directional * directional_weight
            total_weight += directional_weight

            if conviction > strongest_score:
                strongest_score = conviction
                strongest_symbol = sym
                strongest_execution_symbol = recommendation["symbol"] if action else sym

            if urgency_rank.get(urgency, 0) > urgency_rank.get(overall_urgency, 0):
                overall_urgency = urgency

        normalized_basket_score = (net_direction_score / total_weight) if total_weight > 0 else 0.0
        avg_confidence = sum(result['confidence'] for result in sentiment_results.values()) / len(sentiment_results)

        if long_recommendations == len(symbols) and len(symbols) > 0:
            signal_type = "LONG"
        elif short_recommendations == len(symbols) and len(symbols) > 0:
            signal_type = "SHORT"
        elif hold_recommendations == len(symbols) and len(symbols) > 0:
            signal_type = "HOLD"
        elif long_recommendations > 0 and short_recommendations == 0:
            signal_type = "LONG"
        elif short_recommendations > 0 and long_recommendations == 0:
            signal_type = "SHORT"
        elif recommendations:
            # Multi-leg signal: both longs and shorts exist. Use net direction
            # to determine the portfolio-level signal_type, but keep ALL valid
            # recommendations so the paper_trading engine can execute each leg
            # independently. Previously this branch picked a single winner,
            # which suppressed valid opposing signals (e.g. USO LONG was dropped
            # when LUNR SHORT had slightly higher conviction).
            net_direction = sum(
                float(sentiment_results[rec["underlying_symbol"]].get("directional_score", 0.0))
                for rec in recommendations
            )
            if net_direction > 0.15:
                signal_type = "LONG"
            elif net_direction < -0.15:
                signal_type = "SHORT"
            else:
                signal_type = "HOLD"
            # Pick the strongest recommendation for entry_symbol display purposes
            # only — all recommendations remain in the list for execution.
            strongest_recommendation = max(
                recommendations,
                key=lambda rec: abs(float(sentiment_results[rec["underlying_symbol"]].get("directional_score", 0.0)))
                * float(sentiment_results[rec["underlying_symbol"]].get("confidence", 0.0)),
            )
            strongest_symbol = strongest_recommendation["underlying_symbol"]
            strongest_execution_symbol = strongest_recommendation["symbol"]
        else:
            signal_type = "HOLD"

        if signal_type == "HOLD":
            confidence_score = avg_confidence
            overall_urgency = "LOW"
        elif recommendations:
            actionable_confidences = [
                float(sentiment_results[rec["underlying_symbol"]].get("confidence", 0.0))
                for rec in recommendations
                if rec.get("thesis") == signal_type
            ]
            confidence_score = max(actionable_confidences) if actionable_confidences else avg_confidence
        else:
            confidence_score = avg_confidence

        # ── Determine conviction level and trading type ──
        conviction_level_from_engine = None
        trading_type_from_engine = None
        holding_period_from_engine = None

        for sym, result in sentiment_results.items():
            eng_conviction = str(result.get("conviction_level", "")).upper()
            if eng_conviction in ("LOW", "MEDIUM", "HIGH"):
                conviction_level_from_engine = eng_conviction
                break
            eng_trading_type = str(result.get("trading_type", "")).upper()
            if eng_trading_type in ("SCALP", "SWING", "POSITION", "VOLATILE_EVENT"):
                trading_type_from_engine = eng_trading_type
                break
            eng_holding = result.get("holding_period_hours")
            if isinstance(eng_holding, int) and 1 <= eng_holding <= 720:
                holding_period_from_engine = eng_holding
                break

        _cv = self._L["conviction"]
        _crazy_cv = self._crazy_conviction()
        if is_crazy and _crazy_cv:
            _cv = {**_cv, **_crazy_cv}
        if conviction_level_from_engine:
            conviction_level = conviction_level_from_engine
        else:
            if signal_type == "HOLD":
                conviction_level = "LOW"
            elif strongest_score > _cv["high_score_threshold"] and confidence_score > _cv["high_confidence_threshold"]:
                conviction_level = "HIGH"
            elif overall_urgency == "HIGH" and confidence_score < _cv["high_score_threshold"]:
                conviction_level = "LOW"
            else:
                conviction_level = "MEDIUM"

        if trading_type_from_engine:
            trading_type = trading_type_from_engine
        else:
            if conviction_level == "LOW":
                trading_type = "VOLATILE_EVENT"
            elif conviction_level == "MEDIUM":
                trading_type = "SWING"
            else:
                trading_type = "POSITION"

        if holding_period_from_engine:
            holding_period_hours = holding_period_from_engine
        else:
            holding_period_hours = _cv["holding_minutes"].get(trading_type, 720) / 60

        action_if_already_in_position = "HOLD"

        # ── Data gap protection ──
        # If the signal is HOLD and the article count dropped significantly (>60% and previous had >=10),
        # mark it as data_gap_hold so paper_trading doesn't close positions.
        data_gap_hold = False
        if (
            signal_type == "HOLD"
            and previous_posts_count is not None
            and current_posts_count is not None
            and previous_posts_count >= 10
            and current_posts_count < previous_posts_count * 0.4
        ):
            data_gap_hold = True

        # ── Diagnostic logging ──
        print(f"[signal] Blue-team signal: {signal_type}, conviction={conviction_level}, confidence={confidence_score:.3f}, risk_profile={risk_profile}")
        if recommendations:
            for rec in recommendations:
                _ramp = rec.get('ramp_stage', 'N/A')
                _promoted = rec.get('ramp_promotion_enabled', 'false') == 'true'
                _ramp_info = f"ramp={_ramp}" + (" (promotion_allowed)" if _promoted else "")
                print(f"[signal]   → {rec.get('underlying_symbol', '?')} {rec.get('action', '?')} "
                      f"@{rec.get('symbol', '?')} {rec.get('leverage', '?')} "
                      f"size={rec.get('size_pct', '?')}% "
                      f"thesis={rec.get('thesis', '?')} "
                      f"{_ramp_info}")
        else:
            print(f"[signal]   No recommendations generated")

        return TradingSignal(
            signal_type=signal_type,
            confidence_score=min(confidence_score, 1.0),
            entry_symbol=strongest_execution_symbol,
            entry_price=(quotes_by_symbol or {}).get(strongest_execution_symbol, {}).get("current_price") if symbols else None,
            stop_loss_pct=self._L["stop_loss_pct"],
            take_profit_pct=self._L["take_profit_pct"],
            urgency=overall_urgency,
            conviction_level=conviction_level,
            holding_period_hours=holding_period_hours,
            trading_type=trading_type,
            action_if_already_in_position=action_if_already_in_position,
            recommendations=recommendations,
            data_gap_hold=data_gap_hold,
        )

    def build_consensus_trading_signal(
        self,
        blue_team_signal: TradingSignal,
        red_team_review: Optional[RedTeamReview],
        quotes_by_symbol: Optional[Dict[str, Dict[str, Any]]] = None,
        risk_profile: str = "moderate",
    ) -> TradingSignal:
        """Combine the blue-team signal with the red-team review."""
        if not red_team_review or not red_team_review.symbol_reviews:
            print("[red-team] no review to apply — keeping blue-team signal unchanged")
            return blue_team_signal

        print(f"[red-team] applying review: {len(red_team_review.symbol_reviews)} symbol(s) reviewed")

        recommendations: List[Dict[str, str]] = []
        urgency_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        overall_urgency = "LOW"
        strongest_symbol = blue_team_signal.entry_symbol or "USO"
        strongest_execution_symbol = strongest_symbol
        strongest_confidence = -1.0
        signed_scores: List[float] = []
        stop_loss_candidates: List[float] = []

        blue_signal_type = str(blue_team_signal.signal_type or "HOLD").upper()
        source_bias = bool(red_team_review.source_bias_penalty_applied)
        blue_rec_map = self._recommendations_by_underlying(blue_team_signal)

        total = len(red_team_review.symbol_reviews)
        for idx, review in enumerate(red_team_review.symbol_reviews, 1):
            symbol = str(review.symbol or "").upper().strip()
            adjusted_signal = str(review.adjusted_signal or "HOLD").upper().strip()
            adjusted_urgency = str(review.adjusted_urgency or "LOW").upper().strip()
            blue_rec = blue_rec_map.get(symbol) or {}
            blue_symbol_signal = str(blue_rec.get("action") or ("HOLD" if not blue_rec else blue_signal_type)).upper().strip()
            if blue_symbol_signal in {"LONG", "SHORT"}:
                blue_symbol_signal = "BUY" if blue_symbol_signal == "LONG" else "SELL"
            override_is_material = SentimentEngine.red_team_override_is_material(
                adjusted_signal=adjusted_signal,
                blue_signal=blue_symbol_signal,
                evidence=list(review.evidence or []),
                key_risks=list(review.key_risks or []),
                source_bias_applied=source_bias,
            )
            if not override_is_material:
                original_red = adjusted_signal
                adjusted_signal = blue_symbol_signal or "HOLD"
                print(f"Red Team [{idx}/{total}]: {symbol} blue={blue_symbol_signal} → red={original_red} (material=False) — keeping blue={adjusted_signal}")
            else:
                print(f"Red Team [{idx}/{total}]: {symbol} blue={blue_symbol_signal} → red={adjusted_signal} (material=True) — using red")
            adjusted_confidence = SentimentEngine.compute_red_team_confidence(
                adjusted_signal=adjusted_signal,
                blue_signal=blue_symbol_signal or blue_signal_type,
                evidence=list(review.evidence or []),
                key_risks=list(review.key_risks or []),
                source_bias_applied=source_bias,
            )
            stop_loss = SentimentEngine.compute_red_team_stop_loss(adjusted_urgency)

            if adjusted_urgency in urgency_rank and urgency_rank[adjusted_urgency] > urgency_rank[overall_urgency]:
                overall_urgency = adjusted_urgency

            stop_loss_candidates.append(stop_loss)

            if adjusted_signal == "BUY":
                action = "BUY"
                signed_scores.append(max(0.1, adjusted_confidence))
            elif adjusted_signal == "SELL":
                action = "SELL"
                signed_scores.append(-max(0.1, adjusted_confidence))
            else:
                action = ""
                signed_scores.append(0.0)

            if not action or not symbol:
                continue

            leverage = self._resolve_leverage(adjusted_confidence, risk_profile, action=action)
            recommendation = build_execution_recommendation(symbol, action, leverage)
            # Pass through size_pct from blue team recommendation if not overridden
            blue_rec = blue_rec_map.get(symbol) or {}
            recommendation["size_pct"] = blue_rec.get("size_pct", "100.0")
            recommendations.append(recommendation)

            if adjusted_confidence > strongest_confidence:
                strongest_confidence = adjusted_confidence
                strongest_symbol = symbol
                strongest_execution_symbol = recommendation["symbol"]

        computed_confidences = []
        for rv in red_team_review.symbol_reviews:
            rv_symbol = str(rv.symbol or "").upper().strip()
            rv_blue_signal = str((blue_rec_map.get(rv_symbol) or {}).get("action") or blue_signal_type).upper()
            rv_adjusted_signal = str(rv.adjusted_signal or "HOLD").upper()
            if not SentimentEngine.red_team_override_is_material(
                adjusted_signal=rv_adjusted_signal,
                blue_signal=rv_blue_signal,
                evidence=list(rv.evidence or []),
                key_risks=list(rv.key_risks or []),
                source_bias_applied=source_bias,
            ):
                rv_adjusted_signal = str((blue_rec_map.get(rv_symbol) or {}).get("action") or "HOLD").upper()
            computed_confidences.append(
                SentimentEngine.compute_red_team_confidence(
                    adjusted_signal=rv_adjusted_signal,
                    blue_signal=rv_blue_signal,
                    evidence=list(rv.evidence or []),
                    key_risks=list(rv.key_risks or []),
                    source_bias_applied=source_bias,
                )
            )

        if recommendations:
            buy_recommendations = [rec for rec in recommendations if str(rec.get("action") or "").upper() == "BUY" and str(rec.get("thesis") or "").upper() == "LONG"]
            sell_recommendations = [rec for rec in recommendations if str(rec.get("action") or "").upper() == "BUY" and str(rec.get("thesis") or "").upper() == "SHORT"]
            if buy_recommendations and not sell_recommendations:
                signal_type = "LONG"
            elif sell_recommendations and not buy_recommendations:
                signal_type = "SHORT"
            else:
                # Multi-leg consensus: both longs and shorts exist. Use net
                # signed confidence to determine portfolio-level signal_type.
                # All recommendations are preserved for independent execution.
                net_signed = sum(signed_scores)
                if net_signed > 0.15:
                    signal_type = "LONG"
                elif net_signed < -0.15:
                    signal_type = "SHORT"
                else:
                    signal_type = "HOLD"
                # Pick strongest for display purposes only
                strongest_recommendation = max(
                    recommendations,
                    key=lambda rec: abs(float(next(
                        (
                            computed_confidences[idx]
                            for idx, rv in enumerate(red_team_review.symbol_reviews)
                            if str(rv.symbol or "").upper().strip() == str(rec.get("underlying_symbol") or "").upper().strip()
                        ),
                        0.0,
                    ))),
                )
                strongest_execution_symbol = strongest_recommendation["symbol"]
            confidence_score = max(computed_confidences) if computed_confidences else 0.0
        else:
            signal_type = "HOLD"
            confidence_score = sum(computed_confidences) / max(1, len(computed_confidences))
            strongest_execution_symbol = blue_team_signal.entry_symbol or strongest_execution_symbol

        if confidence_score >= 0.75:
            conviction_level = "HIGH"
        elif confidence_score >= 0.45:
            conviction_level = "MEDIUM"
        else:
            conviction_level = "LOW"

        if signal_type == "HOLD":
            conviction_level = "LOW"

        if conviction_level == "HIGH":
            trading_type = "POSITION"
            holding_period_hours = max(24, blue_team_signal.holding_period_hours or 24)
        elif conviction_level == "MEDIUM":
            trading_type = "SWING"
            holding_period_hours = min(max(4, blue_team_signal.holding_period_hours or 12), 24)
        else:
            trading_type = "VOLATILE_EVENT"
            holding_period_hours = 2

        stop_loss_pct = sum(stop_loss_candidates) / len(stop_loss_candidates) if stop_loss_candidates else blue_team_signal.stop_loss_pct

        return TradingSignal(
            signal_type=signal_type,
            confidence_score=min(confidence_score, 1.0),
            entry_symbol=strongest_execution_symbol,
            entry_price=(quotes_by_symbol or {}).get(strongest_execution_symbol, {}).get("current_price"),
            stop_loss_pct=round(float(stop_loss_pct or blue_team_signal.stop_loss_pct or 2.0), 2),
            take_profit_pct=blue_team_signal.take_profit_pct,
            position_size_usd=blue_team_signal.position_size_usd,
            urgency=overall_urgency,
            conviction_level=conviction_level,
            holding_period_hours=holding_period_hours,
            trading_type=trading_type,
            action_if_already_in_position=blue_team_signal.action_if_already_in_position,
            recommendations=recommendations,
        )

    def build_red_team_context(
        self,
        symbols: List[str],
        posts: List[Any],
        sentiment_results: Dict[str, Dict[str, Any]],
        trading_signal: TradingSignal,
        price_context: Dict[str, Any],
        quotes_by_symbol: Dict[str, Dict[str, Any]],
        market_validation: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build context dict for the red-team review prompt."""
        latest_news = []
        for post in posts[:10]:
            latest_news.append({
                "source": str(getattr(post, "source", "") or ""),
                "title": str(getattr(post, "title", "") or ""),
                "summary": str(getattr(post, "summary", "") or getattr(post, "content", "") or "")[:400],
                "keywords": list(getattr(post, "keywords", None) or [])[:8],
            })

        source_counts: Dict[str, int] = {}
        for item in latest_news:
            source = item["source"] or "Unknown"
            source_counts[source] = source_counts.get(source, 0) + 1

        symbol_payloads = []
        rec_map = {
            (rec.get("underlying_symbol") or rec.get("symbol")): rec
            for rec in (trading_signal.recommendations or [])
        }
        for symbol in symbols:
            symbol_payloads.append({
                "symbol": symbol,
                "recommendation": rec_map.get(symbol, {}),
                "sentiment": sentiment_results.get(symbol, {}),
                "quote": quotes_by_symbol.get(symbol, {}),
                "technical_indicators": price_context.get(f"technical_indicators_{symbol.lower()}", {}),
                "technical_context": price_context.get(f"technical_context_{symbol.lower()}", ""),
                "market_validation": market_validation.get(symbol, {}),
            })

        return {
            "symbols": symbols,
            "portfolio_signal": trading_signal.model_dump(mode="json"),
            "latest_news": latest_news,
            "source_counts": source_counts,
            "symbol_context": symbol_payloads,
        }

    def run_red_team_review(
        self,
        model_name: str,
        context: Dict[str, Any],
    ) -> tuple[Optional[RedTeamReview], Optional[RedTeamDebug]]:
        """Run the red-team review against an Ollama model."""
        from services.sentiment.prompts import format_red_team_review_prompt

        print(f"[red-team] starting review — model={model_name}")
        prompt = format_red_team_review_prompt(context.get("raw_context", ""))
        engine = SentimentEngine(model_name=model_name)
        try:
            raw = engine._call_ollama_sync(prompt, model_override=model_name, force_json=True, max_tokens=700)
            raw_text = engine._strip_thinking(raw.get("response", ""))
            payload = engine._extract_json_value(raw_text)
            if not isinstance(payload, dict):
                raise ValueError("Red-team review returned non-object JSON")
            review = RedTeamReview.model_validate(payload)
        except Exception as exc:
            print(f"[red-team] review FAILED — model={model_name}: {exc}")
            raise
        debug = RedTeamDebug(
            context=context,
            prompt=prompt,
            raw_response=raw_text,
            parsed_payload=payload,
            signal_changes=[],
        )
        print(f"[red-team] review completed — {len(review.symbol_reviews or [])} symbol(s) reviewed")
        return review, debug

    def ensure_execution_quotes(
        self,
        signal: TradingSignal,
        quotes_by_symbol: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch quotes for execution tickers that aren't already available."""
        hydrated_quotes = dict(quotes_by_symbol)
        client = PriceClient()
        symbols_to_check = {signal.entry_symbol} if signal.entry_symbol else set()
        for recommendation in signal.recommendations or []:
            symbol = str(recommendation.get("symbol", "") or "").strip().upper()
            if symbol:
                symbols_to_check.add(symbol)

        missing_symbols = [s for s in symbols_to_check if s and s not in hydrated_quotes]
        for symbol in missing_symbols:
            if not symbol:
                continue
            try:
                quote = client.get_realtime_quote(symbol)
                if quote and quote.get("current_price"):
                    hydrated_quotes[symbol] = quote
            except Exception:
                pass

        return hydrated_quotes

    def build_red_team_signal_changes(
        self,
        blue_team_signal: TradingSignal,
        consensus_signal: TradingSignal,
        red_team_review: Optional[RedTeamReview],
    ) -> List[Any]:
        """Build signal change comparison list for red-team debug output."""
        from schemas.analysis import RedTeamSignalChange

        blue_map = self._recommendations_by_underlying(blue_team_signal)
        consensus_map = self._recommendations_by_underlying(consensus_signal)
        review_map = {
            str(review.symbol or "").upper().strip(): review
            for review in (red_team_review.symbol_reviews if red_team_review else [])
            if str(review.symbol or "").strip()
        }
        symbols = sorted(set(blue_map.keys()) | set(consensus_map.keys()) | set(review_map.keys()))
        changes: List[RedTeamSignalChange] = []

        for symbol in symbols:
            blue_text = self._format_recommendation_text(blue_map.get(symbol))
            consensus_text = self._format_recommendation_text(consensus_map.get(symbol))
            changed = blue_text != consensus_text
            review = review_map.get(symbol)
            if blue_text == "No recommendation" and consensus_text != "No recommendation":
                change_type = "added"
            elif blue_text != "No recommendation" and consensus_text == "No recommendation":
                change_type = "removed"
            elif changed:
                blue_action = str((blue_map.get(symbol) or {}).get("action", "") or "").upper()
                consensus_action = str((consensus_map.get(symbol) or {}).get("action", "") or "").upper()
                blue_leverage = str((blue_map.get(symbol) or {}).get("leverage", "") or "")
                consensus_leverage = str((consensus_map.get(symbol) or {}).get("leverage", "") or "")
                change_type = "direction_flip" if blue_action and consensus_action and blue_action != consensus_action else (
                    "leverage_change" if blue_leverage != consensus_leverage else "ticker_change"
                )
            else:
                change_type = "unchanged"

            changes.append(
                RedTeamSignalChange(
                    symbol=symbol,
                    blue_team_recommendation=blue_text,
                    consensus_recommendation=consensus_text,
                    changed=changed,
                    change_type=change_type,
                    rationale=str(getattr(review, "rationale", "") or ""),
                    evidence=list(getattr(review, "evidence", []) or []),
                )
            )

        return changes

    # ── Helpers (private) ───────────────────────────────────────────────

    def _recommendations_by_underlying(self, signal: Optional[TradingSignal]) -> Dict[str, Dict[str, Any]]:
        recs: Dict[str, Dict[str, Any]] = {}
        for rec in (getattr(signal, "recommendations", None) or []):
            key = str(rec.get("underlying_symbol") or rec.get("symbol") or "").upper().strip()
            if key:
                recs[key] = rec
        return recs

    def _format_recommendation_text(self, rec: Optional[Dict[str, Any]]) -> str:
        if not rec:
            return "No recommendation"
        action = str(rec.get("action", "") or "").upper().strip()
        symbol = str(rec.get("symbol", "") or "").upper().strip()
        leverage = str(rec.get("leverage", "") or "").strip()
        if not action and not symbol:
            return "No recommendation"
        parts = [part for part in [action, symbol, leverage] if part]
        return " ".join(parts)

    def _resolve_leverage(self, confidence: float, risk_profile: str, action: str = "", atr_pct: float = 0.0) -> str:
        profile = str(risk_profile or "standard").lower().strip()
        if profile in {"moderate", "aggressive"}:
            profile = "standard"
        if profile == "conservative":
            return "inverse" if str(action).upper() == "SELL" else "1x"

        if profile in {"standard", "custom"}:
            raw = 2 if confidence > 0.75 else 1
        elif profile == "crazy":
            raw = 3
        else:
            raw = 3 if confidence > 0.75 else 1

        _lev_cfg = self._L.get("leverage", {})
        high_vol = float(_lev_cfg.get("high_vol_atr_pct", 3.0))
        med_vol = float(_lev_cfg.get("medium_vol_atr_pct", 1.5))
        if atr_pct >= high_vol:
            cap = 1
        elif atr_pct >= med_vol:
            cap = 2
        else:
            cap = 3

        return f"{min(raw, cap)}x"

    def _compute_decay_factor(self, symbol: str, age_hours: float, pnl_pct: float = 0.0) -> float:
        """
        Return the decay multiplier for a signal that is `age_hours` old.

        Uses per-symbol half-lives from logic_config signal_decay block.
        At age=0 the factor is 1.0 (no decay); it approaches min_decay_factor asymptotically.
        When signal_decay.enabled is False, always returns 1.0.
        
        Momentum Adjustment: If momentum_decay_adjustment is enabled,
        adjust the effective half-life based on P&L:
        - Favorable momentum (pnl_pct > threshold): extend half-life (slower decay)
        - Adverse momentum (pnl_pct < -threshold): accelerate decay (faster decay)
        """
        decay_cfg = self._L.get("signal_decay", {})
        if not decay_cfg.get("enabled", True) or age_hours <= 0.0:
            return 1.0
        
        half_lives = decay_cfg.get("symbol_half_lives", {})
        half_life = float(half_lives.get(str(symbol).upper(), decay_cfg.get("default_half_life_hours", 3.0)))
        min_factor = float(decay_cfg.get("min_decay_factor", 0.10))
        
        # Momentum-adjusted decay
        momentum_cfg = self._L.get("momentum_decay_adjustment", {})
        if momentum_cfg.get("enabled", False) and pnl_pct != 0.0:
            favorable_threshold = float(momentum_cfg.get("favorable_threshold_pct", 1.0))
            adverse_threshold = float(momentum_cfg.get("adverse_threshold_pct", -0.5))
            favorable_extension = float(momentum_cfg.get("favorable_momentum_extension", 1.5))
            adverse_acceleration = float(momentum_cfg.get("adverse_momentum_acceleration", 0.5))
            
            if pnl_pct > favorable_threshold:
                # Price moving in favor - extend half-life (slower decay)
                half_life *= favorable_extension
            elif pnl_pct < adverse_threshold:
                # Price moving against - accelerate decay (faster decay)
                half_life *= adverse_acceleration
        
        raw = 0.5 ** (age_hours / half_life)
        return max(min_factor, raw)

    def _compute_hold_decay_factor(self, symbol: str, age_hours: float) -> float:
        """
        Slower decay for positions already held.
        Uses separate hold_half_lives from logic_config, or entry half-lives
        if hold-specific config is not set. When hold_decay_enabled is false
        or signal_decay is disabled, falls back to the standard decay factor.
        """
        decay_cfg = self._L.get("signal_decay", {})
        if not decay_cfg.get("enabled", True) or age_hours <= 0.0:
            return 1.0
        _hold_decay_active = self._hold_decay_enabled if self._hold_decay_enabled is not None else decay_cfg.get("hold_decay_enabled", False)
        if not _hold_decay_active:
            return self._compute_decay_factor(symbol, age_hours)
        hold_half_lives = decay_cfg.get("symbol_hold_half_lives", {})
        half_life = float(hold_half_lives.get(
            str(symbol).upper(),
            decay_cfg.get("default_hold_half_life_hours", decay_cfg.get("default_half_life_hours", 3.0)),
        ))
        min_factor = float(decay_cfg.get("min_decay_factor", 0.10))
        return _decay_factor(age_hours, half_life, min_factor)

    def _symbol_atr_pct(self, symbol: str, price_context: Optional[Dict[str, Any]]) -> float:
        if not price_context:
            return 0.0
        indicators = price_context.get(f"technical_indicators_{str(symbol).lower()}") or {}
        try:
            atr_pct = float(indicators.get("atr_14_pct") or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return atr_pct if atr_pct > 0 else 0.0

    def _max_atr_pct(self, symbols: List[str], price_context: Optional[Dict[str, Any]]) -> float:
        """Return the maximum ATR% across the given symbols."""
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