"""
Sentiment Engine using chosen LLM
Analyzes geopolitical text for market bluster vs policy changes
"""

import os
import re
import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from pydantic import BaseModel, Field
import requests
import asyncio

from .prompts import expand_proxy_terms_for_matching, normalize_text_for_matching
from config.logic_loader import LOGIC as _L


def build_specialist_response_schema(symbol: str) -> Dict[str, Any]:
    """JSON Schema for the Stage 2 single-symbol specialist response.

    Passed to Ollama via the `format` field — Ollama constrains token generation
    to the schema, so any reasonably-capable model produces compliant output.
    Mirrors the textual schema in SYMBOL_SPECIALIST_RESPONSE_PROMPT.
    """
    # Trimmed for speed: each removed verbose field saved 100-300 tokens of
    # generation time. Per-symbol output dropped from ~700 tokens → ~150,
    # cutting Stage 2 wall time roughly 3-4×. Phrase arrays are replaced by
    # integer counts (only counts feed scoring). The analyst writeup is now
    # synthesized in Python from the structured fields below.
    return {
        "type": "object",
        "required": [
            "event_type",
            "confirmed",
            "bluster_count",
            "substance_count",
            "exposure_type",
            "symbol_relevance",
            "source_count",
            "trading_type",
        ],
        "properties": {
            "event_type": {
                "type": "string",
                "enum": [
                    "geopolitical",
                    "regulatory",
                    "monetary_policy",
                    "trade_policy",
                    "fiscal",
                    "earnings",
                    "macro_data",
                    "sector_news",
                    "noise",
                ],
            },
            "confirmed": {"type": "boolean"},
            "bluster_count": {"type": "integer"},
            "substance_count": {"type": "integer"},
            "exposure_type": {
                "type": "string",
                "enum": ["DIRECT", "INDIRECT", "BROAD", "UNRELATED"],
            },
            "symbol_relevance": {
                "type": "object",
                "required": [symbol],
                "properties": {
                    symbol: {
                        "type": "object",
                        "required": ["relevant", "direction", "mechanism"],
                        "properties": {
                            "relevant": {"type": "boolean"},
                            "direction": {
                                "type": "string",
                                "enum": ["bullish", "bearish", "neutral"],
                            },
                            "mechanism": {"type": "string"},
                        },
                    },
                },
            },
            "source_count": {"type": "integer"},
            "trading_type": {
                "type": "string",
                "enum": ["SCALP", "SWING", "POSITION", "VOLATILE_EVENT"],
            },
        },
    }


def build_keyword_response_schema() -> Dict[str, Any]:
    """JSON Schema for Stage 1 keyword generation. Constrains the response to
    `{"terms": [string, ...]}` regardless of model strength."""
    return {
        "type": "object",
        "required": ["terms"],
        "properties": {
            "terms": {"type": "array", "items": {"type": "string"}},
        },
    }


@dataclass
class SentimentAnalysisResult:
    """Result of a single sentiment analysis."""
    text_source: str
    timestamp: datetime
    is_bluster: bool
    bluster_score: float
    bluster_indicators: List[str]
    is_policy_change: bool
    policy_score: float
    policy_indicators: List[str]
    impact_severity: str
    confidence: float
    reasoning: str


class SentimentAnalysisRequest(BaseModel):
    """Request model for sentiment analysis."""
    text: str = Field(..., min_length=10, max_length=5000)
    text_source: str = Field(default="")
    include_context: bool = Field(default=False)
    context_data: Optional[Dict[str, Any]] = Field(default=None)


class SentimentAnalysisResponse(BaseModel):
    """Response model for sentiment analysis."""
    request_id: str
    timestamp: datetime
    is_bluster: bool
    bluster_score: float
    bluster_indicators: List[str]
    is_policy_change: bool
    policy_score: float
    policy_indicators: List[str]
    impact_severity: str
    confidence: float
    reasoning: str
    directional_score: float = 0.0
    signal_type: str = "HOLD"
    urgency: str = "LOW"
    entry_symbol: str = ""
    analyst_writeup: str = ""
    supporting_points: List[str] = Field(default_factory=list)
    headline_citations: List[str] = Field(default_factory=list)
    symbol_impacts: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    prompt_used: str = ""
    raw_model_response: str = ""
    parsed_payload: Dict[str, Any] = Field(default_factory=dict)


# Module-level keyword cache keyed by symbol (uppercase).
# Persists for the server session so LLM is only called once per symbol.
_keyword_cache: Dict[str, List[str]] = {}
_keyword_trace_cache: Dict[str, Dict[str, Any]] = {}
_large_model_re_cache: Dict[str, bool] = {}  # model name → is_large_model bool


class SentimentEngine:
    """
    Sentiment analysis engine using Ollama Llama-3-70b.
    
    Features:
    - Market bluster detection
    - Policy change identification
    - Trading signal generation
    - Caching for repeated analyses
    - Fallback handling
    """
    
    # Configuration — override with OLLAMA_MODEL / OLLAMA_URL / VLLM_URL env vars
    MODEL_NAME = os.getenv("OLLAMA_MODEL", "").strip()
    TEMPERATURE = 0.10
    MAX_TOKENS = 8192  # Generous limit for all backends — truncated analyses produce wrong signals
    API_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
    INFERENCE_BACKEND = os.getenv("INFERENCE_BACKEND", "ollama").strip().lower()
    VLLM_URL = os.getenv("VLLM_URL", "http://localhost:8000").rstrip("/")
    # OpenAI / OpenAI-compatible cloud LLM (env var fallbacks when secret store is empty)
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

    # Limit concurrent Ollama requests — local GPU processes one at a time, so parallel
    # requests queue up inside Ollama and each one's HTTP timeout starts ticking from
    # the moment it's sent, not when GPU work begins. Serializing here means every call
    # gets immediate GPU attention and its full timeout budget.
    # Default 1 (safe for any single-GPU box). Admins with VRAM headroom can bump
    # this via configure_parallelism(); keep in sync with OLLAMA_NUM_PARALLEL.
    _ollama_semaphore: asyncio.Semaphore = asyncio.Semaphore(1)
    _ollama_parallel_slots: int = 1

    @classmethod
    def configure_parallelism(cls, slots: int) -> None:
        """Resize the shared Ollama semaphore. Call between requests, never mid-flight.

        New permits take effect on the next acquire. In-flight calls retain their
        permit on the old semaphore — fine for the dev/single-user case where
        analyses do not overlap.
        """
        slots = max(1, min(8, int(slots)))
        if slots != cls._ollama_parallel_slots:
            cls._ollama_semaphore = asyncio.Semaphore(slots)
            cls._ollama_parallel_slots = slots

    # Caching
    _cache: Dict[str, SentimentAnalysisResponse] = {}
    _cache_ttl: int = 300  # 5 minutes

    def __init__(
        self,
        api_url: Optional[str] = None,
        model_name: Optional[str] = None,
        *,
        # Optional config overrides from the DB (admin UI settings).
        # When set (non-empty), these override the corresponding class-level
        # env-var defaults. Falls back to env var → hardcoded default.
        overrides: Optional[Dict[str, str]] = None,
    ):
        opts = overrides or {}
        # Ollama URL: DB override → env var → default
        self.api_url = (
            (opts.get("ollama_url") or "").strip()
            or api_url
            or self.API_URL
        )
        # vLLM URL: DB override → env var → default
        self.vllm_url = (opts.get("vllm_url") or "").strip() or self.VLLM_URL
        # OpenAI settings: DB override → env var → default
        openai_base_url = (opts.get("openai_base_url") or "").strip()
        openai_model = (opts.get("openai_model") or "").strip()
        cloud_provider = (opts.get("cloud_provider") or "").strip().lower() or "openai"
        self.OPENAI_BASE_URL = openai_base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
        self.OPENAI_MODEL = openai_model or os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
        # Only read cloud API key when using the OpenAI-compatible (cloud) backend.
        # Local inference (ollama/vllm) never needs a cloud API key.
        self.OPENAI_API_KEY = ""
        # FIX: Set inference_backend BEFORE using it in model_name logic
        self.inference_backend = self.INFERENCE_BACKEND
        
        if self.INFERENCE_BACKEND == "openai":
            try:
                from services.secret_store import get_cloud_api_key
                self.OPENAI_API_KEY = get_cloud_api_key(cloud_provider) or os.getenv("OPENAI_API_KEY", "").strip()
            except Exception:
                self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

        # Model name: prefer explicit parameter, then backend-specific env var, then generic env var
        if self.inference_backend == "openai":
            # For OpenAI/cloud: use OPENAI_MODEL env var or config
            self.model_name = (model_name or os.getenv("OPENAI_MODEL", "").strip()) or ""
        elif self.inference_backend == "vllm":
            # For vLLM: use VLLM_MODEL env var or config
            self.model_name = (model_name or os.getenv("VLLM_MODEL", "").strip()) or ""
        else:
            # For Ollama: use OLLAMA_MODEL env var or config
            self.model_name = (model_name or self.MODEL_NAME or "").strip()
        
        self.session = requests.Session()
        self._cache = {}
        # self.inference_backend already set above (line ~237)

    @classmethod
    def set_backend(cls, backend: str) -> None:
        """Switch the active inference backend for all future engine instances."""
        normalized = str(backend or "ollama").strip().lower()
        cls.INFERENCE_BACKEND = normalized if normalized in {"ollama", "vllm", "openai"} else "ollama"
    
    def clear_cache(self):
        """Clear all cached analysis and LLM-generated keyword results."""
        self._cache = {}
        _keyword_cache.clear()
        _keyword_trace_cache.clear()
    
    async def analyze(
        self,
        text: str,
        text_source: str = "",
        include_context: bool = False,
        context_data: Optional[Dict[str, Any]] = None,
        specialist_symbol: Optional[str] = None,
        specialist_focus: str = "",
        model_override: Optional[str] = None,
        proxy_context: str = "",
        web_research_context: str = "",
    ) -> SentimentAnalysisResponse:
        """
        Analyze text for market bluster and policy changes.
        
        Args:
            text: Text to analyze (from social media or news)
            text_source: Source identifier for caching
            include_context: Whether to include market context
            context_data: Optional market data for context-aware analysis
            
        Returns:
            SentimentAnalysisResponse with bluster and policy scores
        """
        # Check cache first
        cache_key = f"{text_source}:{specialist_symbol or 'generic'}:{text[:100]}:{web_research_context[:120]}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if (datetime.now(timezone.utc) - cached.timestamp).total_seconds() < self._cache_ttl:
                return cached
        
        # Let exceptions propagate — callers must handle Ollama being unavailable
        if include_context and context_data:
            response = await self._analyze_with_context(
                text,
                text_source,
                context_data,
                specialist_symbol=specialist_symbol,
                specialist_focus=specialist_focus,
                model_override=model_override,
                proxy_context=proxy_context,
                web_research_context=web_research_context,
            )
        else:
            response = await self._analyze_text(text, text_source, model_override=model_override)

        self._cache[cache_key] = response
        return response
    
    async def _analyze_text(
        self,
        text: str,
        text_source: str,
        model_override: Optional[str] = None,
    ) -> SentimentAnalysisResponse:
        """Analyze text using combined prompt."""
        from .prompts import format_combined_prompt

        prompt = format_combined_prompt(text)
        response_data = await self._call_ollama(prompt, model_override=model_override, force_json=True)
        parsed = self._parse_response(response_data, text_source)
        parsed.prompt_used = prompt
        return parsed

    async def _analyze_with_context(
        self,
        text: str,
        text_source: str,
        context_data: Dict[str, Any],
        specialist_symbol: Optional[str] = None,
        specialist_focus: str = "",
        model_override: Optional[str] = None,
        proxy_context: str = "",
        web_research_context: str = "",
    ) -> SentimentAnalysisResponse:
        """Analyze text with market context, optionally injecting Stage 1 proxy context."""
        from .prompts import (
            format_context_aware_prompt,
            format_symbol_specialist_context_prompt,
        )

        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        active_symbol = specialist_symbol or str(context_data.get("active_symbol", "") or "")
        active_symbol_price = context_data.get("active_symbol_price", 0.0)
        validation_context = context_data.get("validation_context", "")
        source_count = int(context_data.get("source_count", 0))

        if specialist_symbol:
            # Lean single-symbol prompt — no cross-symbol rules or basket instructions
            prompt = format_symbol_specialist_context_prompt(
                symbol=specialist_symbol,
                specialist_focus=specialist_focus,
                text=text,
                date=date,
                active_symbol_price=active_symbol_price,
                validation_context=validation_context,
                web_research_context=web_research_context,
                proxy_context=proxy_context,
                source_count=source_count,
            )
        else:
            prompt = format_context_aware_prompt(
                text=text,
                date=date,
                active_symbol=active_symbol,
                active_symbol_price=active_symbol_price,
                uso_price=context_data.get("uso_price", 0.0),
                bito_price=context_data.get("bito_price", 0.0),
                qqq_price=context_data.get("qqq_price", 0.0),
                spy_price=context_data.get("spy_price", 0.0),
                recent_sentiment=context_data.get("recent_sentiment", ""),
                validation_context=validation_context,
                web_research_context=web_research_context,
            )

        # Store technical indicators for _parse_response to pass to compute_symbol_scores
        self._last_technical_indicators = context_data.get("technical_indicators") or {}

        # Stage 2 specialist calls always target a known symbol — pin Ollama's
        # output to the specialist schema so weaker models can't return free-form
        # JSON that bypasses our scoring fields.
        schema = build_specialist_response_schema(specialist_symbol) if specialist_symbol else None
        response_data = await self._call_ollama(
            prompt,
            model_override=model_override,
            force_json=True,
            response_schema=schema,
        )
        parsed = self._parse_response(response_data, text_source)
        parsed.prompt_used = prompt
        return parsed

    @staticmethod
    def _normalize_event_type(raw: str) -> str:
        """Map free-form event_type strings to the canonical enum.

        Weaker models (or older Ollama without structured-output support) often
        return labels like "Economic Report" or "Fed Decision" instead of the
        snake_case enum values. Without this normalization, unknown labels fall
        through to a near-zero default score and the symbol shows 0/0/HOLD.
        """
        text = (raw or "").strip().lower().replace("-", " ").replace("_", " ")
        if not text:
            return "noise"
        # Exact-match the canonical enum first (after underscore normalization).
        canonical = {
            "geopolitical", "regulatory", "monetary policy", "trade policy",
            "fiscal", "earnings", "macro data", "sector news", "noise",
        }
        if text in canonical:
            return text.replace(" ", "_")
        # Keyword routing for free-form variants.
        routes = [
            (("monetary", "fed", "rate", "fomc", "central bank"), "monetary_policy"),
            (("tariff", "trade war", "trade policy", "import", "export"), "trade_policy"),
            (("fiscal", "stimulus", "tax", "spending", "budget"), "fiscal"),
            (("earning", "revenue", "guidance", "eps"), "earnings"),
            (("macro", "cpi", "ppi", "jobs", "employment", "gdp", "inflation",
              "payroll", "unemployment", "economic", "consumer"), "macro_data"),
            (("regulator", "sec ", "antitrust", "compliance", "ban"), "regulatory"),
            (("geopol", "war", "sanction", "military", "conflict"), "geopolitical"),
            (("sector", "industry"), "sector_news"),
        ]
        for keywords, target in routes:
            if any(k in text for k in keywords):
                return target
        return "noise"

    @staticmethod
    def compute_symbol_scores(
        extraction: Dict[str, Any],
        symbol: str,
        technical_indicators: Optional[Dict[str, Any]] = None,
        *,
        cloud_backend: bool = False,
    ) -> Dict[str, Any]:
        """
        Derive calibrated scores from LLM-extracted facts.
        All numerical outputs come from this function — the LLM never outputs raw floats.

        Args:
            extraction: LLM-extracted facts dict.
            symbol: The symbol being scored.
            technical_indicators: Optional TA data for confidence modulation.
            cloud_backend: When True, apply conservative-model overrides. Cloud-hosted
                models (deepseek, gemma, etc.) are fine-tuned for safety and default
                to noise/UNRELATED/false even for articles that passed Stage 1 filtering.
                These overrides treat BROAD+ as relevant and downgrade UNRELATED to BROAD.
        """
        event_type = SentimentEngine._normalize_event_type(str(extraction.get("event_type") or ""))
        # Cloud-model override: they often default to event_type="noise" with zero
        # substance/bluster counts even for articles that passed Stage 1 keyword
        # filtering. Since Stage 1 confirmed topic relevance, override to macro_data.
        if cloud_backend:
            n_blu_raw = extraction.get("bluster_count")
            n_sub_raw = extraction.get("substance_count")
            if event_type == "noise" and (n_blu_raw is None or n_sub_raw is None or (int(n_blu_raw or 0) == 0 and int(n_sub_raw or 0) == 0)):
                event_type = "macro_data"
        confirmed = bool(extraction.get("confirmed", False))
        # Prefer integer counts (new trimmed schema); fall back to array length
        # for snapshots saved under the old schema.
        n_blu = extraction.get("bluster_count")
        if n_blu is None:
            n_blu = len(extraction.get("bluster_phrases") or [])
        n_sub = extraction.get("substance_count")
        if n_sub is None:
            n_sub = len(extraction.get("substance_phrases") or [])
        n_blu = max(0, int(n_blu))
        n_sub = max(0, int(n_sub))
        source_count = max(1, min(10, int(extraction.get("source_count") or 2)))

        sym_rel = (extraction.get("symbol_relevance") or {}).get(symbol, {})
        relevant = bool(sym_rel.get("relevant", False))
        direction = str(sym_rel.get("direction") or "neutral").lower()
        exposure_type = str(extraction.get("exposure_type") or "").upper()
        if exposure_type not in {"DIRECT", "INDIRECT", "BROAD", "UNRELATED"}:
            exposure_type = "DIRECT" if relevant else "UNRELATED"
        # Cloud-model override: these models often return UNRELATED/relevant:false
        # for articles that passed Stage 1 keyword filtering. Since Stage 1 confirmed
        # topic relevance, downgrade UNRELATED to BROAD and mark it relevant.
        if cloud_backend:
            if exposure_type == "UNRELATED":
                exposure_type = "BROAD"
            if exposure_type == "BROAD" and not relevant:
                relevant = True
        # Broad-market ETFs are relevant to any non-UNRELATED macro news by definition.
        # Qwen often returns relevant:false for SPY/QQQ even when exposure_type is DIRECT/BROAD,
        # which crushes policy_score and confidence via the irrelevance multiplier.
        _BROAD_MARKET_ETFS = {"SPY", "QQQ"}
        if symbol.upper() in _BROAD_MARKET_ETFS and exposure_type != "UNRELATED":
            relevant = True
        transmission_path = str(extraction.get("transmission_path") or "").strip()
        if not transmission_path:
            transmission_path = str(sym_rel.get("mechanism") or "").strip() or "No credible transmission path."

        _ss = _L["sentiment_scoring"]

        # ── Bluster score: −1 (pure rhetoric) → +1 (pure substance) ──────────
        # Uses a linear net-count formula instead of a ratio to produce a smooth
        # gradient rather than binary extremes. Each bluster phrase subtracts,
        # each substance phrase adds, scaled by per_item_weight.
        #   n_sub=0, n_blu=0 →  0.0  (−0.15 unconfirmed)
        #   n_sub=1, n_blu=0 → +0.33 (+0.18 unconfirmed)
        #   n_sub=2, n_blu=0 → +0.66 (+0.51 unconfirmed)
        #   n_sub=3, n_blu=0 → +1.0
        #   n_sub=0, n_blu=1 → −0.33 (−0.48 unconfirmed)
        per_item_weight = float(_ss.get("bluster_per_item_weight", 0.33))
        raw_bluster = (n_sub - n_blu) * per_item_weight
        # When both bluster AND substance are present, the article is hedging —
        # apply the same skepticism penalty as unconfirmed events regardless of
        # confirmed flag, since rhetorical hedging undermines substance credibility.
        if n_blu > 0 and n_sub > 0:
            raw_bluster -= _ss["unconfirmed_bluster_penalty"]
        if not confirmed:
            raw_bluster -= _ss["unconfirmed_bluster_penalty"]
        bluster_score = round(max(-1.0, min(1.0, raw_bluster)), 3)

        # ── Policy score: 0 (irrelevant noise) → 1 (major confirmed policy) ──
        event_base: Dict[str, float] = dict(_ss["event_base_scores"])
        base_policy = event_base.get(event_type, event_base["default"])
        policy_score = base_policy * (1.0 if confirmed else _ss["unconfirmed_policy_multiplier"])
        if source_count > 0:
            source_factor = min(1.0, source_count / 3.0)
            policy_score *= 0.65 + 0.35 * source_factor
        if not relevant:
            policy_score *= _ss["irrelevance_policy_multiplier"]
        policy_cap = float((_ss.get("exposure_policy_caps") or {}).get(exposure_type, 1.0))
        policy_score = min(policy_score, policy_cap)
        policy_score = round(max(0.0, min(1.0, policy_score)), 3)

        # ── Confidence: grows with source diversity and drops if irrelevant ───
        base_conf = _ss["confidence_base"] + (source_count / 10.0) * _ss["confidence_source_weight"]
        exposure_bonus = {
            "DIRECT": 0.08,
            "INDIRECT": 0.04,
            "BROAD": 0.00,
            "UNRELATED": -0.04,
        }
        base_conf += exposure_bonus.get(exposure_type, 0.0)
        if not relevant:
            base_conf -= _ss["confidence_irrelevance_penalty"]
        if not confirmed:
            base_conf -= _ss["confidence_unconfirmed_penalty"]
        confidence_cap = float((_ss.get("exposure_confidence_caps") or {}).get(exposure_type, _ss["confidence_max"]))
        base_conf = min(base_conf, confidence_cap)
        # Don't apply the confidence floor for UNRELATED exposure — "no coverage
        # found" should score near zero so the UI can distinguish it from a genuine
        # weak signal. The floor only makes sense when there IS relevant coverage.
        min_conf = _ss["confidence_min"] if exposure_type != "UNRELATED" else 0.0
        confidence = round(max(min_conf, min(_ss["confidence_max"], base_conf)), 3)

        # ── Technical confidence modifier: align with traditional TA ──────────
        # Compute the signal type first (needed for modifier direction), then
        # apply the modifier. We compute a preliminary signal type here, apply
        # the tech modifier, then recompute signal type with the adjusted confidence.
        _prelim_signal = "HOLD"
        _prelim_allow_bluster = (
            relevant
            and exposure_type in {"DIRECT", "INDIRECT"}
            and direction != "bullish"
        )
        if (bluster_score < _ss["bluster_short_threshold"]
                and policy_score < _ss["policy_signal_threshold"]
                and _prelim_allow_bluster):
            _prelim_signal = "SHORT"
        elif policy_score >= _ss["policy_signal_threshold"] and relevant:
            if direction == "bullish" and confidence >= _ss["direction_confidence_min"]:
                _prelim_signal = "LONG"
            elif direction == "bearish" and confidence >= _ss["direction_confidence_min"]:
                _prelim_signal = "SHORT"

        tech_modifier = SentimentEngine.compute_technical_confidence_modifier(
            _prelim_signal, technical_indicators
        )
        if tech_modifier != 0.0:
            confidence = round(max(min_conf, min(_ss["confidence_max"], confidence + tech_modifier)), 3)

        # ── Signal type: rule-based from scores and direction ─────────────────
        _min_mag = _ss["directional_score_min_magnitude"]
        # bluster_short_threshold is intentionally more negative than the old -0.35
        # to require stronger bluster before auto-triggering SHORT without policy backing
        # Pure-rhetoric bearish overrides should only fire when the symbol has a
        # credible, non-broad transmission path. Otherwise broad or unrelated
        # headlines make the whole board look falsely bearish.
        allow_bluster_only_short = (
            relevant
            and exposure_type in {"DIRECT", "INDIRECT"}
            and direction != "bullish"
        )
        if (bluster_score < _ss["bluster_short_threshold"]
                and policy_score < _ss["policy_signal_threshold"]
                and allow_bluster_only_short):
            signal_type = "SHORT"
        elif policy_score >= _ss["policy_signal_threshold"] and relevant:
            if direction == "bullish" and confidence >= _ss["direction_confidence_min"]:
                signal_type = "LONG"
            elif direction == "bearish" and confidence >= _ss["direction_confidence_min"]:
                signal_type = "SHORT"
            else:
                signal_type = "HOLD"
        else:
            signal_type = "HOLD"

        # ── Directional score: signed magnitude for downstream signal gen ─────
        if signal_type == "LONG":
            directional_score = round(min(1.0, max(_min_mag, policy_score)), 3)
        elif signal_type == "SHORT":
            # Weighted blend: policy evidence (60%) + bluster magnitude (40%)
            # Prevents pure rhetoric from producing a full-strength SHORT score
            _short_mag = abs(bluster_score) * 0.4 + policy_score * 0.6
            directional_score = round(max(-1.0, min(-_min_mag, -_short_mag)), 3)
        else:
            directional_score = 0.0

        # ── Impact severity ───────────────────────────────────────────────────
        if policy_score >= _ss["impact_severity_high"]:
            impact_severity = "high"
        elif policy_score >= _ss["impact_severity_medium"]:
            impact_severity = "medium"
        else:
            impact_severity = "low"

        return {
            "bluster_score":    bluster_score,
            "policy_score":     policy_score,
            "confidence":       confidence,
            "signal_type":      signal_type,
            "directional_score": directional_score,
            "impact_severity":  impact_severity,
            "is_bluster":       bluster_score < _ss["bluster_detection_threshold"],
            "is_policy_change": policy_score >= _ss["policy_change_threshold"] and relevant,
            "exposure_type":    exposure_type,
            "transmission_path": transmission_path,
        }

    @staticmethod
    def compute_technical_confidence_modifier(
        signal_type: str,
        technical_indicators: Optional[Dict[str, Any]],
    ) -> float:
        """
        Compute a confidence modifier based on technical indicator alignment
        with the signal direction. Returns a value in [-max_total_modifier, +max_total_modifier].
        """
        if not technical_indicators or not _L.get("technical_confidence", {}).get("enabled", True):
            return 0.0

        _tc = _L["technical_confidence"]
        modifier = 0.0
        is_long = signal_type.upper() == "LONG"
        is_short = signal_type.upper() == "SHORT"

        # RSI
        rsi = technical_indicators.get("rsi_14")
        if rsi is not None:
            if rsi > 70:
                modifier += _tc["rsi_overbought_long_penalty"] if is_long else 0.0
                modifier += _tc["rsi_overbought_short_bonus"] if is_short else 0.0
            elif rsi < 30:
                modifier += _tc["rsi_oversold_long_bonus"] if is_long else 0.0
                modifier += _tc["rsi_oversold_short_penalty"] if is_short else 0.0

        # SMA cross
        cross = technical_indicators.get("cross_signal")
        if cross:
            if cross == "golden":
                modifier += _tc["golden_cross_long_bonus"] if is_long else 0.0
                modifier += _tc["golden_cross_short_penalty"] if is_short else 0.0
            elif cross == "death":
                modifier += _tc["death_cross_long_penalty"] if is_long else 0.0
                modifier += _tc["death_cross_short_bonus"] if is_short else 0.0

        # MACD histogram
        macd_hist = technical_indicators.get("macd_hist")
        if macd_hist is not None:
            if macd_hist > 0:
                modifier += _tc["macd_positive_long_bonus"] if is_long else 0.0
                modifier += _tc["macd_positive_short_penalty"] if is_short else 0.0
            else:
                modifier += _tc["macd_negative_long_penalty"] if is_long else 0.0
                modifier += _tc["macd_negative_short_bonus"] if is_short else 0.0

        # Volume ratio
        vol_ratio = technical_indicators.get("vol_ratio_20")
        if vol_ratio is not None:
            if vol_ratio > 1.5:
                modifier += _tc["volume_high_bonus"]
            elif vol_ratio < 0.7:
                modifier += _tc["volume_low_penalty"]

        # Bollinger %B
        bb_pct = technical_indicators.get("bb_pct_b")
        if bb_pct is not None:
            if bb_pct > 0.95:
                modifier += _tc["bb_above_upper_long_penalty"] if is_long else 0.0
                modifier += _tc["bb_above_upper_short_bonus"] if is_short else 0.0
            elif bb_pct < 0.05:
                modifier += _tc["bb_below_lower_long_bonus"] if is_long else 0.0
                modifier += _tc["bb_below_lower_short_penalty"] if is_short else 0.0

        # OBV trend
        obv = technical_indicators.get("obv_trend")
        if obv:
            if obv == "rising":
                modifier += _tc["obv_rising_long_bonus"] if is_long else 0.0
                modifier += _tc["obv_rising_short_penalty"] if is_short else 0.0
            elif obv == "falling":
                modifier += _tc["obv_falling_long_penalty"] if is_long else 0.0
                modifier += _tc["obv_falling_short_bonus"] if is_short else 0.0

        max_mod = float(_tc.get("max_total_modifier", 0.15))
        return round(max(-max_mod, min(max_mod, modifier)), 3)

    @staticmethod
    def compute_red_team_confidence(
        adjusted_signal: str,
        blue_signal: str,
        evidence: list,
        key_risks: list,
        source_bias_applied: bool,
    ) -> float:
        """
        Derive confidence from red-team qualitative output.
        LLM provides the categorical signal and lists of evidence/risks.
        Python converts those counts into a calibrated confidence float.
        """
        _rt = _L["red_team"]
        base = _rt["confidence_base"]
        # Agreement with blue team adds confidence; disagreement reduces it
        if adjusted_signal.upper() == blue_signal.upper():
            base += _rt["agreement_bonus"]
        else:
            base -= _rt["disagreement_penalty"]
        # More evidence → more confident; more risks → less confident
        base += min(_rt["evidence_bonus_max"], len(evidence) * _rt["evidence_bonus_per_item"])
        base -= min(_rt["risk_penalty_max"], len(key_risks) * _rt["risk_penalty_per_item"])
        if source_bias_applied:
            base -= _rt["source_bias_penalty"]
        return round(max(_rt["confidence_min"], min(_rt["confidence_max"], base)), 3)

    @staticmethod
    def compute_red_team_stop_loss(adjusted_urgency: str) -> float:
        """Rule-based stop loss from urgency — removes the LLM float guess."""
        return _L["red_team"]["stop_loss_by_urgency"].get(
            str(adjusted_urgency).upper(), 2.5
        )

    @staticmethod
    def red_team_override_is_material(
        adjusted_signal: str,
        blue_signal: str,
        evidence: list,
        key_risks: list,
        source_bias_applied: bool,
    ) -> bool:
        """Require stronger evidence before red team is allowed to overturn blue team."""
        normalized_adjusted = str(adjusted_signal or "HOLD").upper().strip()
        normalized_blue = str(blue_signal or "HOLD").upper().strip()
        if normalized_adjusted == normalized_blue:
            return True

        confidence = SentimentEngine.compute_red_team_confidence(
            adjusted_signal=normalized_adjusted,
            blue_signal=normalized_blue,
            evidence=evidence,
            key_risks=key_risks,
            source_bias_applied=source_bias_applied,
        )

        evidence_count = len(evidence or [])
        risk_count = len(key_risks or [])
        _rt = _L["red_team"]
        if normalized_adjusted == "HOLD":
            return (
                confidence >= _rt["hold_override_min_confidence"]
                and (evidence_count >= _rt["hold_override_min_evidence_or_risks"]
                     or risk_count >= _rt["hold_override_min_evidence_or_risks"])
            )
        return (
            confidence >= _rt["flip_override_min_confidence"]
            and evidence_count >= _rt["flip_override_min_evidence"]
            and evidence_count >= risk_count + 1
            and not source_bias_applied
        )

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Remove <think>...</think> blocks emitted by Qwen3 models."""
        if text is None:
            return ""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    @staticmethod
    def _sanitize_json(json_str: str) -> str:
        """
        Clean LLM-generated JSON:
        1. Strip // line comments (char-by-char to skip comments inside strings)
        2. Remove trailing commas before } or ]
        3. Insert missing commas between adjacent JSON values (most common LLM mistake)
        4. Normalize CRLF and remove stray control characters
        """
        # Normalize line endings and strip BOM
        json_str = json_str.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")

        # ── Pass 1: strip // comments outside strings ─────────────────────────
        result: list[str] = []
        in_string = False
        escaped = False
        i = 0
        while i < len(json_str):
            ch = json_str[i]
            if escaped:
                result.append(ch)
                escaped = False
                i += 1
                continue
            if ch == "\\" and in_string:
                result.append(ch)
                escaped = True
                i += 1
                continue
            if ch == '"':
                in_string = not in_string
                result.append(ch)
                i += 1
                continue
            if not in_string and ch == "/" and i + 1 < len(json_str) and json_str[i + 1] == "/":
                while i < len(json_str) and json_str[i] != "\n":
                    i += 1
                continue
            # Drop bare control characters (tab and newline are fine)
            if not in_string and ord(ch) < 0x20 and ch not in ("\n", "\t"):
                i += 1
                continue
            result.append(ch)
            i += 1
        cleaned = "".join(result)

        # ── Pass 2: trailing comma removal ────────────────────────────────────
        cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)

        # ── Pass 3: insert missing commas between adjacent values ─────────────
        # Matches: end of a value (string-close, number, bool, null, ], })
        # followed by whitespace (with or without newline) then start of a new
        # key or value ("). This is the most common cause of
        # "Expecting ',' delimiter" in LLM-generated JSON.
        _VALUE_END = r'(?:"(?:[^"\\]|\\.)*"|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[\]\}])'
        cleaned = re.sub(
            r'(' + _VALUE_END + r')(\s+)(")',
            lambda m: m.group(1) + "," + m.group(2) + m.group(3),
            cleaned,
        )

        return cleaned

    @staticmethod
    def _parse_json_with_repair(json_str: str) -> Dict[str, Any]:
        """
        Parse JSON, retrying up to 25 times by inserting a comma exactly where
        Python's json decoder reports the missing delimiter.  This is more
        reliable than regex guessing because the error position is exact.
        """
        s = json_str
        for _ in range(25):
            try:
                return json.loads(s)
            except json.JSONDecodeError as e:
                if "Expecting ',' delimiter" in str(e) and 0 < e.pos < len(s):
                    s = s[: e.pos] + "," + s[e.pos :]
                else:
                    raise
        return json.loads(s)

    @staticmethod
    def _extract_json_value(text: str) -> Any:
        """
        Robustly extract the first decodable JSON value from model output.

        Tries, in order:
        - full response as-is
        - fenced ```json ... ``` blocks
        - first decodable object/array found via raw_decode scanning

        This is safer than slicing from the first '{' to the last '}' because it
        tolerates trailing prose, stray brackets, and top-level arrays.
        """
        decoder = json.JSONDecoder()
        raw = str(text or "").strip()
        if not raw:
            raise ValueError("Empty model response")

        candidates: list[str] = [raw]
        fenced_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
        candidates.extend(block.strip() for block in fenced_blocks if block.strip())

        for candidate in candidates:
            sanitized = SentimentEngine._sanitize_json(candidate)
            # 1) Try parsing the sanitized candidate as-is.
            try:
                return SentimentEngine._parse_json_with_repair(sanitized)
            except Exception:
                pass

            # 2) Try truncation recovery on the outer {/[ before falling back to
            # greedy inner scanning. Otherwise raw_decode below would happily
            # return an inner empty array (e.g. "bluster_phrases": []) when the
            # outer object is truncated, masking the real payload.
            first = re.search(r"[\{\[]", sanitized)
            if first:
                outer = sanitized[first.start():]
                closed = SentimentEngine._close_truncated_json(outer)
                if closed != outer:
                    # Re-sanitize: closing may produce trailing commas like ",]}"
                    # which _parse_json_with_repair (missing-comma only) can't fix.
                    closed = SentimentEngine._sanitize_json(closed)
                    try:
                        return SentimentEngine._parse_json_with_repair(closed)
                    except Exception:
                        pass

            # 3) Fallback: scan for any decodable {/[ in the candidate. Handles
            # the "prose then valid embedded JSON" case.
            for match in re.finditer(r"[\{\[]", sanitized):
                try:
                    value, _ = decoder.raw_decode(sanitized[match.start():])
                    return value
                except json.JSONDecodeError:
                    continue

        raise ValueError("No decodable JSON payload found in model response")

    @staticmethod
    def _close_truncated_json(s: str) -> str:
        """Best-effort close of a token-truncated JSON string.

        Walks the string tracking open strings, arrays, and objects.  If the
        string ends inside an open string we close it first, then close any
        unclosed containers in reverse order.  This lets us parse the partial
        payload rather than discarding the whole response.
        """
        stack: list[str] = []
        in_string = False
        escaped = False
        for ch in s:
            if escaped:
                escaped = False
                continue
            if ch == "\\" and in_string:
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if not in_string:
                if ch in ("{", "["):
                    stack.append(ch)
                elif ch == "}" and stack and stack[-1] == "{":
                    stack.pop()
                elif ch == "]" and stack and stack[-1] == "[":
                    stack.pop()

        suffix = ""
        if in_string:
            suffix += '"'
        for opener in reversed(stack):
            suffix += "}" if opener == "{" else "]"
        return s + suffix

    async def _call_ollama(
        self,
        prompt: str,
        model_override: Optional[str] = None,
        force_json: bool = False,
        max_tokens: Optional[int] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # Cloud backends (OpenAI / vLLM) handle concurrency natively — no semaphore.
        # The semaphore is only needed for local Ollama (single GPU, one request at a time).
        if self.inference_backend in ("openai", "vllm"):
            return await asyncio.to_thread(
                self._call_ollama_sync,
                prompt,
                model_override,
                force_json,
                max_tokens,
                response_schema,
            )
        async with self._ollama_semaphore:
            return await asyncio.to_thread(
                self._call_ollama_sync,
                prompt,
                model_override,
                force_json,
                max_tokens,
                response_schema,
            )

    async def _generate_symbol_keywords(
        self,
        symbol: str,
        model: str,
        persisted_terms: Optional[List[str]] = None,
    ) -> List[str]:
        """Return proxy keywords for a symbol.

        Built-in symbols (USO/BITO/QQQ/SPY) return static terms instantly.
        Custom symbols call the LLM once and cache the result for the session.
        """
        from .prompts import TICKER_PROXY_MAP, format_keyword_generation_prompt

        sym = symbol.upper()

        if sym in TICKER_PROXY_MAP:
            terms = [t.lower() for t in TICKER_PROXY_MAP[sym]]
            static_prompt = (
                f"Stage 1 used the built-in static proxy map for {sym}. "
                "No LLM keyword-generation prompt was sent for this symbol.\n\n"
                f"Static proxy terms:\n- " + "\n- ".join(TICKER_PROXY_MAP[sym])
            )
            _keyword_trace_cache[sym] = {
                "symbol": sym,
                "mode": "static_map",
                "model": "",
                "cache_hit": False,
                "prompt": static_prompt,
                "raw_response": "No model response. Stage 1 used built-in proxy terms.",
                "terms": terms,
                "error": None,
            }
            return terms

        normalized_persisted = [
            str(term or "").strip().lower()
            for term in (persisted_terms or [])
            if str(term or "").strip()
        ]
        if normalized_persisted:
            deduped: List[str] = []
            for term in normalized_persisted:
                if term not in deduped:
                    deduped.append(term)
                if len(deduped) >= 50:
                    break
            _keyword_cache[sym] = deduped
            _keyword_trace_cache[sym] = {
                "symbol": sym,
                "mode": "persisted",
                "model": "",
                "cache_hit": False,
                "prompt": "Loaded persisted proxy terms from app_config.symbol_proxy_terms.",
                "raw_response": "No model response. Stage 1 used persisted proxy terms.",
                "terms": deduped,
                "error": None,
            }
            return deduped

        if sym in _keyword_cache:
            cached_trace = dict(_keyword_trace_cache.get(sym, {}))
            cached_trace["cache_hit"] = True
            _keyword_trace_cache[sym] = cached_trace
            return _keyword_cache[sym]

        print(f"Stage 1: generating keywords for custom symbol {sym} via {model}...")
        try:
            prompt = format_keyword_generation_prompt(sym)
            raw = await self._call_ollama(
                prompt,
                model_override=model,
                force_json=True,
                max_tokens=768,
                response_schema=build_keyword_response_schema(),
            )
            raw_text = self._strip_thinking(raw.get("response", ""))
            data = self._extract_json_value(raw_text)
            if not isinstance(data, dict):
                raise ValueError("Keyword generation returned non-object JSON")
            raw_terms = (
                data.get("terms") or data.get("keywords")
                or data.get("proxy_terms") or []
            )
            terms = [str(t).lower().strip() for t in raw_terms if t][:50]

            if terms:
                _keyword_cache[sym] = terms
                _keyword_trace_cache[sym] = {
                    "symbol": sym,
                    "mode": "llm",
                    "model": model,
                    "cache_hit": False,
                    "prompt": prompt,
                    "raw_response": raw.get("response", ""),
                    "terms": terms,
                    "error": None,
                }
                print(f"Stage 1: cached {len(terms)} keywords for {sym}: {', '.join(terms[:8])}{'...' if len(terms) > 8 else ''}")
                return terms

            raise ValueError("LLM returned empty terms list")

        except Exception as exc:
            print(f"Stage 1: keyword generation failed for {sym} ({exc}) — using ticker name as fallback")

        # Fallback: use the ticker in both common forms plus a handful of generic
        # equity-news terms so at least broad macro articles can be matched.
        fallback = [sym.lower(), sym.upper(), "earnings", "revenue", "guidance", "analyst"]
        _keyword_cache[sym] = fallback
        _keyword_trace_cache[sym] = {
            "symbol": sym,
            "mode": "fallback",
            "model": model,
            "cache_hit": False,
            "prompt": locals().get("prompt", ""),
            "raw_response": locals().get("raw", {}).get("response", "") if isinstance(locals().get("raw"), dict) else "",
            "terms": fallback,
            "error": str(locals().get("exc", "fallback to ticker name")),
        }
        return fallback

    async def extract_relevant_articles(
        self,
        posts: List[Any],
        symbols: List[str],
        extraction_model: str,
        persisted_proxy_terms_by_symbol: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        """
        Stage 1 — keyword-based filtering using per-symbol proxy terms.

        For built-in symbols (USO/BITO/QQQ/SPY): uses static TICKER_PROXY_MAP.
        For custom symbols (e.g. NVDA, NOW): calls the LLM once to generate
        proxy keywords, caches them for the session, then uses pure keyword matching.
        No per-article LLM calls — fast regardless of article count.
        """
        # Fetch keywords for all symbols (parallel; built-ins return immediately)
        persisted_proxy_terms_by_symbol = persisted_proxy_terms_by_symbol or {}
        tasks = [
            self._generate_symbol_keywords(
                sym,
                extraction_model,
                persisted_terms=persisted_proxy_terms_by_symbol.get(str(sym).upper(), []),
            )
            for sym in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_terms: set = set()
        proxy_terms_by_symbol: Dict[str, List[str]] = {}
        for sym, kws in zip(symbols, results):
            if isinstance(kws, Exception):
                kws = [sym.lower()]
            proxy_terms_by_symbol[sym] = list(kws)
            all_terms.update(kws)

        # Normalized keyword matching — milliseconds, no model needed
        expanded_terms = expand_proxy_terms_for_matching(list(all_terms))
        keyword_relevant: List[Any] = []
        for post in posts:
            blob = normalize_text_for_matching(
                " ".join([
                    str(getattr(post, 'title', '') or ''),
                    str(getattr(post, 'summary', '') or ''),
                    str(getattr(post, 'content', '') or ''),
                    " ".join(getattr(post, 'keywords', None) or []),
                ])
            )
            if any(term in blob for term in expanded_terms):
                keyword_relevant.append(post)

        # Minimum coverage threshold: if keyword matching retains fewer than 10% of
        # articles, fall back to the full pool. This prevents the pipeline from
        # collapsing to 1 article when 332 were fetched — the proxy terms are
        # intentionally narrow and may miss general market news that Stage 2's
        # per-symbol specialist can still analyze.
        MIN_COVERAGE_RATIO = 0.10
        if keyword_relevant and len(keyword_relevant) / max(1, len(posts)) >= MIN_COVERAGE_RATIO:
            filtered = keyword_relevant
            print(
                f"Stage 1 keyword filter: {len(keyword_relevant)}/{len(posts)} articles matched"
                f" | using keyword matches"
            )
        else:
            filtered = posts
            print(
                f"Stage 1 keyword filter: {len(keyword_relevant)}/{len(posts)} articles matched"
                f" | below {MIN_COVERAGE_RATIO:.0%} coverage — using all {len(posts)} articles to avoid starvation"
            )

        # Build per-symbol article subsets and exposure-quality hints in one pass.
        # posts_by_symbol[sym] contains only articles that matched sym's own proxy
        # terms — a much tighter pool than the global union used previously.
        # Stage 2 passes this subset to each specialist instead of the shared pool,
        # so NVDA only analyses articles about chips/AI/export bans, not SPY macro.
        exposure_hints_by_symbol: Dict[str, str] = {}
        posts_by_symbol: Dict[str, List[Any]] = {}
        for sym, terms in proxy_terms_by_symbol.items():
            if not terms:
                exposure_hints_by_symbol[sym] = "BROAD"
                posts_by_symbol[sym] = []
                continue
            sym_expanded = expand_proxy_terms_for_matching(terms)
            sym_posts = [
                post for post in filtered
                if any(
                    t in normalize_text_for_matching(
                        " ".join([
                            str(getattr(post, 'title', '') or ''),
                            str(getattr(post, 'summary', '') or ''),
                            str(getattr(post, 'content', '') or ''),
                            " ".join(getattr(post, 'keywords', None) or []),
                        ])
                    )
                    for t in sym_expanded
                )
            ]
            posts_by_symbol[sym] = sym_posts
            ratio = len(sym_posts) / max(1, len(filtered))
            if ratio >= 0.5:
                exposure_hints_by_symbol[sym] = "DIRECT"
            elif ratio >= 0.15:
                exposure_hints_by_symbol[sym] = "INDIRECT"
            else:
                exposure_hints_by_symbol[sym] = "BROAD"

        per_sym_summary = " | ".join(
            f"{sym}: {len(posts_by_symbol.get(sym, []))} articles ({exposure_hints_by_symbol.get(sym, '?')})"
            for sym in sorted(proxy_terms_by_symbol.keys())
        )
        print(f"Stage 1 per-symbol pools: {per_sym_summary}")

        # Build keyword attribution: term hit counts + source breakdown per symbol.
        keyword_attribution_by_symbol: Dict[str, Dict[str, Any]] = {}
        for sym, terms in proxy_terms_by_symbol.items():
            sym_posts = posts_by_symbol.get(sym, [])
            sym_expanded = expand_proxy_terms_for_matching(terms)
            term_counts: Dict[str, int] = {}
            source_counts: Dict[str, int] = {}
            for post in sym_posts:
                blob = normalize_text_for_matching(
                    " ".join([
                        str(getattr(post, "title", "") or ""),
                        str(getattr(post, "summary", "") or ""),
                        str(getattr(post, "content", "") or ""),
                        " ".join(getattr(post, "keywords", None) or []),
                    ])
                )
                for t in sym_expanded:
                    if t in blob:
                        term_counts[t] = term_counts.get(t, 0) + 1
                src = str(
                    getattr(post, "source", None)
                    or getattr(post, "feed_name", None)
                    or "unknown"
                )
                source_counts[src] = source_counts.get(src, 0) + 1
            top_terms = sorted(term_counts.items(), key=lambda x: -x[1])[:10]
            keyword_attribution_by_symbol[sym] = {
                "total_articles": len(sym_posts),
                "top_terms": [{"term": t, "count": c} for t, c in top_terms],
                "source_breakdown": source_counts,
            }

        return {
            "filtered_posts": filtered,
            "posts_by_symbol": posts_by_symbol,
            "proxy_terms_by_symbol": proxy_terms_by_symbol,
            "exposure_hints_by_symbol": exposure_hints_by_symbol,
            "keyword_attribution_by_symbol": keyword_attribution_by_symbol,
            "keyword_generation_trace_by_symbol": {
                sym: dict(_keyword_trace_cache.get(sym.upper(), {}))
                for sym in symbols
            },
        }

    @classmethod
    def _is_large_model(cls, model_name: str) -> bool:
        """Return True for models ≥ 7B so we can set keep_alive to prevent unloading.
        
        Results are cached per model name to avoid redundant regex scans.
        """
        if not model_name:
            return False
        global _large_model_re_cache
        if model_name in _large_model_re_cache:
            return _large_model_re_cache[model_name]
        import re
        m = re.search(r"(\d+\.?\d*)b", model_name.lower())
        result = bool(m and float(m.group(1)) >= 7)
        _large_model_re_cache[model_name] = result
        return result

    def _call_ollama_sync(
        self,
        prompt: str,
        model_override: Optional[str] = None,
        force_json: bool = False,
        max_tokens: Optional[int] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        print(f"SentimentEngine._call_ollama_sync → backend={self.inference_backend!r}, model_override={model_override!r}")
        if self.inference_backend == "vllm":
            return self._call_vllm_sync(prompt, model_override, force_json, max_tokens, response_schema)
        if self.inference_backend == "openai":
            return self._call_openai_sync(prompt, model_override, force_json, max_tokens, response_schema)
        model = (model_override or self.model_name or "").strip()
        effective_max_tokens = max_tokens if max_tokens is not None else self.MAX_TOKENS
        # Estimate prompt tokens (4 chars ≈ 1 token for English) and size the KV cache
        # to match. Ollama defaults to the model's full context window (often 32k+),
        # which wastes VRAM and adds prefill latency even for short prompts.
        estimated_prompt_tokens = len(prompt) // 4
        num_ctx = min(8192, estimated_prompt_tokens + effective_max_tokens + 256)
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,  # Disables Qwen3 thinking mode; response goes to "response" field
            "options": {
                "temperature": self.TEMPERATURE,
                "num_predict": effective_max_tokens,
                "num_ctx": num_ctx,
                "cache_prompt": True,  # KV-cache reuse across calls with same prompt prefix → ~40-60% prefill reduction
            },
        }
        # A JSON Schema in `format` constrains generation to the schema (Ollama 0.5+).
        # Falling back to "json" lets weaker models freelance and break our parser.
        if response_schema is not None:
            payload["format"] = response_schema
        elif force_json:
            payload["format"] = "json"
        # Prevent large models from unloading between batches
        if self._is_large_model(model):
            payload["keep_alive"] = "10m"

        start_time = time.time()
        response = None

        try:
            response = self.session.post(
                self.api_url,
                json=payload,
                timeout=180,
            )
            response.raise_for_status()

            result = response.json()
            latency = (time.time() - start_time) * 1000
            print(f"Ollama [{model}] completed in {latency:.1f}ms")
            return result

        except requests.exceptions.Timeout:
            raise Exception("Ollama API timeout")
        except requests.exceptions.ConnectionError:
            raise Exception("Cannot connect to Ollama. Is it running?")
        except requests.exceptions.HTTPError as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            body_text = ""
            if response is not None:
                try:
                    body_payload = response.json()
                    body_text = str(body_payload.get("error") or body_payload)
                except Exception:
                    body_text = str(getattr(response, "text", "") or "")
            detail = body_text.strip() or str(e)
            detail_lower = detail.lower()
            if status_code == 404 and "model" in detail_lower:
                raise Exception(
                    f"Model not found: `{model}`. Pull it with: ollama pull {model}"
                )
            if status_code == 404:
                raise Exception(
                    f"Ollama endpoint not found at {self.api_url}. "
                    "Verify OLLAMA_URL is correct (expected base like http://localhost:11434/api/generate)."
                )
            raise Exception(f"Ollama API HTTP {status_code}: {detail}")
        except json.JSONDecodeError as e:
            raise Exception(f"Invalid JSON response from Ollama: {e}")
        except Exception as e:
            raise Exception(f"Ollama API error: {e}")

    def _call_vllm_sync(
        self,
        prompt: str,
        model_override: Optional[str] = None,
        force_json: bool = False,
        max_tokens: Optional[int] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        model = (model_override or self.model_name or "").strip()
        effective_max_tokens = max_tokens if max_tokens is not None else self.MAX_TOKENS
        url = f"{self.vllm_url}/v1/completions"
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "max_tokens": effective_max_tokens,
            "temperature": self.TEMPERATURE,
        }
        if response_schema is not None:
            payload["guided_json"] = response_schema
        elif force_json:
            payload["response_format"] = {"type": "json_object"}

        start_time = time.time()
        response = None
        try:
            response = self.session.post(url, json=payload, timeout=180)
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["text"]
            latency = (time.time() - start_time) * 1000
            print(f"vLLM [{model}] completed in {latency:.1f}ms")
            # Wrap in the same envelope _call_ollama_sync callers expect so that
            # downstream _parse_json_with_repair / _extract_json_value work unchanged.
            return {"response": text}
        except requests.exceptions.Timeout:
            raise Exception("vLLM API timeout")
        except requests.exceptions.ConnectionError:
            raise Exception("Cannot connect to vLLM. Is it running?")
        except requests.exceptions.HTTPError as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            body_text = ""
            if response is not None:
                try:
                    body_payload = response.json()
                    body_text = str(body_payload.get("message") or body_payload)
                except Exception:
                    body_text = str(getattr(response, "text", "") or "")
            detail = body_text.strip() or str(e)
            raise Exception(f"vLLM API HTTP {status_code}: {detail}")
        except (KeyError, IndexError) as e:
            raise Exception(f"Unexpected vLLM response shape: {e}")
        except json.JSONDecodeError as e:
            raise Exception(f"Invalid JSON response from vLLM: {e}")
        except Exception as e:
            raise Exception(f"vLLM API error: {e}")

    def _call_openai_sync(
        self,
        prompt: str,
        model_override: Optional[str] = None,
        force_json: bool = False,
        max_tokens: Optional[int] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Call the OpenAI / OpenAI-compatible chat completions API.

        CRITICAL: This NEVER sends `response_schema` (json_schema response_format)
        because many non-OpenAI providers (OpenRouter, Together, etc.) do not support
        strict JSON schema mode. Instead we always use force_json=True and let the
        existing JSON repair pipeline (_sanitize_json / _extract_json_value) handle
        any formatting issues — it's robust against trailing commas, unclosed
        brackets, and other common LLM output problems.

        Resolves the API key from (in order of priority):
        1. Instance-level OPENAI_API_KEY / env var fallback
        2. Secret store (OS keychain)

        Resolves the model from (in order of priority):
        1. model_override (passed by the caller)
        2. Instance-level OPENAI_MODEL / env var fallback

        Resolves the base URL from (in order of priority):
        1. Instance-level OPENAI_BASE_URL / env var fallback
        """
        from services.openai_client import call_openai_chat_sync

        # Resolve API key: from instance var (set at init time) or env var.
        # In local mode (ollama/vllm) self.OPENAI_API_KEY is empty, so we
        # never fall through to calling get_cloud_api_key() or env var.
        api_key = self.OPENAI_API_KEY
        if not api_key and self.INFERENCE_BACKEND == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "").strip()

        effective_model = (model_override or self.model_name or self.OPENAI_MODEL or "").strip()
        effective_base_url = (self.OPENAI_BASE_URL or "https://api.openai.com/v1").strip()
        effective_max_tokens = max_tokens if max_tokens is not None else self.MAX_TOKENS

        

        if not api_key:
            raise Exception(
                "OpenAI API key not configured. Set it in the admin UI "
                "(Settings → Cloud LLM) or via the OPENAI_API_KEY environment variable."
            )
        if not effective_model:
            raise Exception(
                "OpenAI model not configured. Set it in the admin UI "
                "(Settings → Cloud LLM) or via the OPENAI_MODEL environment variable."
            )

        # Pass response_schema if provided — it constrains the model to output
        # the exact fields we need, just like Ollama's `format` field does.
        # call_openai_chat_sync will attempt json_schema format first, and fall
        # back to force_json if the provider doesn't support it.
        result = call_openai_chat_sync(
            prompt=prompt,
            model=effective_model,
            api_key=api_key,
            base_url=effective_base_url,
            force_json=force_json,
            response_schema=response_schema,
            max_tokens=effective_max_tokens,
            temperature=self.TEMPERATURE,
            timeout=180,
        )
        raw = result.get("response", "")
        return result

    def _parse_response(
        self,
        ollama_response: Dict[str, Any],
        text_source: str
    ) -> SentimentAnalysisResponse:
        """
        Parse Ollama response into structured data.
        
        Args:
            ollama_response: Raw response from Ollama API
            text_source: Source identifier
            
        Returns:
            SentimentAnalysisResponse with parsed data
        """
        # Extract the JSON from the LLM response
        raw_text = ollama_response.get("response", "")
        raw_text = self._strip_thinking(raw_text)

        try:
            data = self._extract_json_value(raw_text)
        except (ValueError, json.JSONDecodeError):
            raise ValueError(
                f"Model did not return valid JSON. Raw response:\n{raw_text[:500]}"
            )
        
        # ── Detect extraction format (new) vs legacy float format (old) ────────
        is_extraction_format = "symbol_relevance" in data or (
            "event_type" in data and "confirmed" in data
        )

        # Extract symbol early for debug logging (safe — dict .get() never throws)
        sym_keys = list((data.get("symbol_relevance") or {}).keys())
        symbol_for_scoring = sym_keys[0] if sym_keys else ""

        # Always print a compact debug line with the symbol, regardless of format

        # Determine if we're using a cloud backend for cloud-model-specific overrides
        _is_cloud = getattr(self, "inference_backend", "ollama") in ("openai", "vllm")

        # Always compute and print scores (unconditional, error-tolerant)
        try:
            tech_indicators = getattr(self, "_last_technical_indicators", None) or {}
            computed = self.compute_symbol_scores(data, symbol_for_scoring, technical_indicators=tech_indicators, cloud_backend=_is_cloud)
            _B = "\033[94m"; _R = "\033[91m"; _G = "\033[92m"; _Y = "\033[93m"; _X = "\033[0m"
            _sig = (computed or {}).get("signal_type", "")
            _sc = _R if _sig == "SHORT" else _G if _sig == "LONG" else _Y if _sig == "HOLD" else _X
            _scores_str = str(computed).replace(f"'signal_type': '{_sig}'", f"'signal_type': '{_sc}{_sig}{_X}'")
        except Exception as e:
            computed = None

        if is_extraction_format and computed is not None:
            # New path: LLM extracted facts, Python computes scores
            bluster = {
                "is_bluster": computed["is_bluster"],
                "bluster_score": computed["bluster_score"],
                "confidence": computed["confidence"],
                "reasoning": (data.get("analyst_writeup") or ""),
                "bluster_indicators": data.get("bluster_phrases") or [],
            }
            policy = {
                "is_policy_change": computed["is_policy_change"],
                "policy_score": computed["policy_score"],
                "impact_severity": computed["impact_severity"],
                "confidence": computed["confidence"],
                "reasoning": (data.get("analyst_writeup") or ""),
                "policy_indicators": data.get("substance_phrases") or [],
            }
            _trading_type = str(data.get("trading_type") or "SWING").upper()
            _exposure_type = computed.get("exposure_type", "DIRECT")
            _holding_lookup = {"SCALP": 2, "VOLATILE_EVENT": 3, "SWING": 12, "POSITION": 72}
            _urgency_map = {"SCALP": "HIGH", "VOLATILE_EVENT": "HIGH", "SWING": "MEDIUM", "POSITION": "LOW"}
            _conviction_map = {"SCALP": "LOW", "VOLATILE_EVENT": "MEDIUM", "SWING": "MEDIUM", "POSITION": "HIGH"}
            _urgency = _urgency_map.get(_trading_type, "MEDIUM")
            _conviction = _conviction_map.get(_trading_type, "MEDIUM")
            if _exposure_type == "UNRELATED":
                _conviction = "LOW"
            elif _exposure_type == "BROAD" and _conviction == "HIGH":
                _conviction = "MEDIUM"
            signal = {
                "signal_type": computed["signal_type"],
                "confidence_score": computed["confidence"],
                "urgency": _urgency,
                "entry_symbol": symbol_for_scoring,
                "reasoning": (data.get("analyst_writeup") or ""),
                "conviction_level": _conviction,
                "trading_type": _trading_type,
                "holding_period_hours": _holding_lookup.get(_trading_type, 12),
            }
            # Inject computed directional_score into data so downstream can read it
            data["_computed_directional_score"] = computed["directional_score"]
        else:
            # Legacy path: accept both nested basket-level JSON and flatter specialist JSON.
            bluster = data.get("market_bluster", {})
            policy = data.get("policy_change", {})
            signal = data.get("trading_signal", {})
            if not bluster and "bluster_score" in data:
                bluster = {
                    "is_bluster": data.get("is_bluster", False),
                    "bluster_score": data.get("bluster_score", 0.0),
                    "confidence": data.get("confidence", 0.5),
                    "reasoning": data.get("reasoning", ""),
                }
            if not policy and "policy_score" in data:
                policy = {
                    "is_policy_change": data.get("is_policy_change", False),
                    "policy_score": data.get("policy_score", 0.0),
                    "impact_severity": data.get("impact_severity", "low"),
                    "confidence": data.get("confidence", 0.5),
                    "reasoning": data.get("reasoning", ""),
                }
            if not signal and "signal_type" in data:
                signal = {
                    "signal_type": data.get("signal_type", "HOLD"),
                    "confidence_score": data.get("confidence", 0.5),
                    "urgency": data.get("urgency", "LOW"),
                    "entry_symbol": data.get("entry_symbol", ""),
                    "reasoning": data.get("reasoning", ""),
                }
        supporting_points = data.get("supporting_points", []) or []
        headline_citations = data.get("headline_citations", []) or []
        analyst_writeup = self._build_analyst_writeup(
            data=data,
            bluster=bluster,
            policy=policy,
            signal=signal,
            supporting_points=supporting_points,
            headline_citations=headline_citations,
        )
        directional_score = self._resolve_directional_score(
            data=data,
            signal=signal,
            policy=policy,
            bluster=bluster,
            reasoning=analyst_writeup,
        )
        
        return SentimentAnalysisResponse(
            request_id="",  # Would be generated by caller
            timestamp=datetime.now(timezone.utc),
            is_bluster=bluster.get("is_bluster", False),
            bluster_score=float(bluster.get("bluster_score", 0.0)),
            bluster_indicators=bluster.get("bluster_indicators", []),
            is_policy_change=policy.get("is_policy_change", False),
            policy_score=float(policy.get("policy_score", 0.0)),
            policy_indicators=policy.get("policy_indicators", []),
            impact_severity=policy.get("impact_severity", "low"),
            confidence=float(bluster.get("confidence", 0.5) * 0.6 + policy.get("confidence", 0.5) * 0.4),
            reasoning=analyst_writeup,
            directional_score=directional_score,
            signal_type=str(signal.get("signal_type", "HOLD")).upper(),
            urgency=str(signal.get("urgency", "LOW")).upper(),
            entry_symbol=str(signal.get("entry_symbol", "")),
            analyst_writeup=analyst_writeup,
            supporting_points=supporting_points,
            headline_citations=headline_citations,
            symbol_impacts=data.get("symbol_impacts", {}) or {},
            raw_model_response=raw_text,
            parsed_payload=data,
        )

    @staticmethod
    def _build_analyst_writeup(
        data: Dict[str, Any],
        bluster: Dict[str, Any],
        policy: Dict[str, Any],
        signal: Dict[str, Any],
        supporting_points: List[str],
        headline_citations: List[str],
    ) -> str:
        """Synthesize an analyst writeup from structured fields.

        With the trimmed schema (no `analyst_writeup` from the LLM) this is the
        primary path. The mechanism string per symbol carries the actual
        causal claim — we wrap it with event/exposure/direction/scores.
        Older snapshots may still contain a model-written analyst_writeup; use
        it directly when present.
        """
        analyst_writeup = (data.get("analyst_writeup") or "").strip()
        if analyst_writeup:
            return analyst_writeup

        symbol = ""
        sym_rel_root = data.get("symbol_relevance") or {}
        if isinstance(sym_rel_root, dict) and sym_rel_root:
            symbol = next(iter(sym_rel_root.keys()), "")
        sym_rel = sym_rel_root.get(symbol, {}) if symbol else {}

        event_type = str(data.get("event_type") or "").replace("_", " ") or "event"
        confirmed = bool(data.get("confirmed", False))
        exposure_type = str(data.get("exposure_type") or "").upper() or "DIRECT"
        direction = str(sym_rel.get("direction") or "neutral").lower()
        mechanism = str(sym_rel.get("mechanism") or "").strip()
        signal_type = str(signal.get("signal_type", "HOLD")).upper()
        try:
            policy_score = float(policy.get("policy_score", 0.0))
            bluster_score = float(bluster.get("bluster_score", 0.0))
            confidence = float(signal.get("confidence_score") or bluster.get("confidence", 0.5))
        except (TypeError, ValueError):
            policy_score = bluster_score = 0.0
            confidence = 0.5

        confirmed_label = "confirmed" if confirmed else "unconfirmed"
        direction_label = {
            "bullish": "Bullish",
            "bearish": "Bearish",
            "neutral": "Neutral",
        }.get(direction, "Neutral")

        if exposure_type == "UNRELATED" or signal_type == "HOLD" and policy_score < 0.05:
            _generic_fallback = "no direct catalyst found in current news"
            _mech_lower = mechanism.lower() if mechanism else ""
            _skip_mechanism = not mechanism or _mech_lower in (
                "no direct price mechanism.", "no direct price mechanism", "none", ""
            )
            mechanism_note = (
                f" {mechanism.rstrip('.')}." if not _skip_mechanism else f" {_generic_fallback.capitalize()}."
            )
            return (
                f"{exposure_type.title()} exposure to {symbol or 'this symbol'} — "
                f"{event_type}, {confirmed_label}.{mechanism_note} "
                f"Signal: {signal_type}. policy={policy_score:.2f} bluster={bluster_score:+.2f} "
                f"confidence={confidence:.0%}."
            )

        mechanism_clause = f" — {mechanism}" if mechanism and mechanism.lower() != "no direct price mechanism." else ""
        return (
            f"{exposure_type.title()} exposure to {symbol or 'this symbol'} · "
            f"{event_type}, {confirmed_label} · {direction_label}{mechanism_clause} · "
            f"Signal: {signal_type}. policy={policy_score:.2f} bluster={bluster_score:+.2f} "
            f"confidence={confidence:.0%}."
        )

    @staticmethod
    def _resolve_directional_score(
        data: Dict[str, Any],
        signal: Dict[str, Any],
        policy: Dict[str, Any],
        bluster: Dict[str, Any],
        reasoning: str,
    ) -> float:
        """Use Python-computed directional score (extraction format) or infer from legacy floats."""
        # Extraction format: Python already computed this in _parse_response
        try:
            if "_computed_directional_score" in data:
                return float(data["_computed_directional_score"])
        except (TypeError, ValueError):
            pass
        # Legacy float format
        try:
            if "directional_score" in data and data.get("directional_score") is not None:
                return max(-1.0, min(1.0, float(data.get("directional_score"))))
        except (TypeError, ValueError):
            pass

        signal_type = str(signal.get("signal_type", "HOLD")).upper().strip()
        try:
            policy_score = max(0.0, min(1.0, float(policy.get("policy_score", 0.0))))
        except (TypeError, ValueError):
            policy_score = 0.0
        try:
            bluster_score = max(-1.0, min(1.0, float(bluster.get("bluster_score", 0.0))))
        except (TypeError, ValueError):
            bluster_score = 0.0

        if signal_type == "LONG":
            return min(1.0, max(0.15, policy_score))
        if signal_type == "SHORT":
            return max(-1.0, min(-0.15, -max(abs(bluster_score), policy_score)))

        lowered = (reasoning or "").lower()
        positive_hints = ["bullish", "beneficiary", "re-rate higher", "rally", "positive for"]
        negative_hints = ["bearish", "headwind", "sell-off", "negative for", "pressure on"]
        if any(token in lowered for token in positive_hints):
            return min(1.0, max(0.1, policy_score * 0.8))
        if any(token in lowered for token in negative_hints):
            return max(-1.0, min(-0.1, -max(abs(bluster_score), policy_score * 0.8)))
        return 0.0
    
    def get_cached_result(self, key: str) -> Optional[SentimentAnalysisResponse]:
        """Get a cached result by key."""
        if key in self._cache:
            cached = self._cache[key]
            if (datetime.now(timezone.utc) - cached.timestamp).total_seconds() < self._cache_ttl:
                return cached
            else:
                del self._cache[key]
        return None
