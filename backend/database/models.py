"""
SQLAlchemy ORM models for the trading system database.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float, JSON, Boolean, ForeignKey,
    Index, UniqueConstraint
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
from .engine import engine

Base = declarative_base()


class Post(Base):
    """
    Model representing a scraped post from social media or news feeds.
    Stores raw content before sentiment analysis.
    """
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)
    source = Column(String(50), nullable=False)  # e.g., "truth_social", "reuters_rss"
    author = Column(String(200), nullable=True)
    timestamp = Column(DateTime(timezone=True), default=func.now())
    sentiment_analysis = Column(JSON, nullable=True)
    is_analyzed = Column(Boolean, default=False)

    __table_args__ = (
        Index("ix_posts_source_timestamp", "source", "timestamp"),
        Index("ix_posts_is_analyzed", "is_analyzed"),
    )


class ScrapedArticle(Base):
    """
    Queue-backed article store for the producer/consumer ingestion flow.
    Articles are inserted by the ingestion worker and consumed by analysis runs.
    """
    __tablename__ = "scraped_articles"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(100), nullable=False)
    url = Column(Text, nullable=False, unique=True)
    title = Column(Text, nullable=False, default="")
    summary = Column(Text, nullable=False, default="")
    full_content = Column(Text, nullable=False, default="")
    published_at = Column(DateTime(timezone=True), nullable=True)
    discovered_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    processed = Column(Boolean, nullable=False, default=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    fast_lane_triggered = Column(Boolean, nullable=False, default=False)
    content_hash = Column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_scraped_articles_processed", "processed"),
        Index("ix_scraped_articles_published_at", "published_at"),
        Index("ix_scraped_articles_discovered_at", "discovered_at"),
        Index("ix_scraped_articles_content_hash", "content_hash"),
    )


class AnalysisResult(Base):
    """
    Model storing complete analysis results from the sentiment engine.
    Links multiple posts to a single analysis run.
    """
    __tablename__ = "analysis_results"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String(36), unique=True, index=True, nullable=False)
    timestamp = Column(DateTime(timezone=True), default=func.now())
    
    # Sentiment data stored as JSON for flexibility
    sentiment_data = Column(JSON, nullable=False)
    
    # Trading signal generated from analysis
    signal = Column(JSON, nullable=False)
    
    # Backtest results if run
    backtest_results = Column(JSON, nullable=True)

    # Metadata about the analysis run (named run_metadata to avoid shadowing SQLAlchemy's Base.metadata)
    run_metadata = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_analysis_timestamp", "timestamp"),
    )


class TradingSignal(Base):
    """
    Model for individual trading signals with execution tracking.
    Stores signal generation and subsequent trade execution details.
    """
    __tablename__ = "trading_signals"

    id = Column(Integer, primary_key=True, index=True)
    analysis_id = Column(Integer, ForeignKey("analysis_results.id"), nullable=False)
    
    symbol = Column(String(10), nullable=False)  # e.g., "USO", "BITO"
    signal_type = Column(String(20), nullable=False)  # "LONG", "SHORT", "HOLD"
    confidence_score = Column(Float, nullable=True)
    
    entry_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    position_size = Column(Float, nullable=True)  # In dollars or shares
    
    status = Column(String(20), default="PENDING")  # PENDING, EXECUTED, CANCELLED, STOPPED
    execution_timestamp = Column(DateTime(timezone=True), nullable=True)
    
    notes = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_signals_analysis_id", "analysis_id"),
        Index("ix_signals_status", "status"),
    )


class Trade(Base):
    """
    Immutable recommendation-time trade entry used for forward P&L tracking.
    One row is created per actionable recommendation.
    Tracks conviction level and expected holding period to reduce churn.
    
    CRITICAL: `symbol` is the EXECUTION symbol (e.g., SBIT, SPXS, UCO) that was actually bought/sold.
    `underlying_symbol` is the base symbol (e.g., BITO, QQQ, USO) for reference.
    P&L is calculated using the execution symbol's prices.
    """
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    analysis_id = Column(Integer, ForeignKey("analysis_results.id"), nullable=False)
    request_id = Column(String(36), nullable=False)

    symbol = Column(String(10), nullable=False)  # EXECUTION symbol (SBIT, SPXS, UCO, etc.)
    underlying_symbol = Column(String(10), nullable=True)  # BASE symbol (BITO, QQQ, USO, etc.) for reference
    action = Column(String(10), nullable=False)  # BUY or SELL
    leverage = Column(String(10), nullable=False, default="1x")
    signal_type = Column(String(20), nullable=False)
    confidence_score = Column(Float, nullable=True)

    recommended_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    entry_price = Column(Float, nullable=False)  # Price for the EXECUTION symbol
    entry_price_timestamp = Column(DateTime(timezone=True), nullable=False)

    stop_loss_pct = Column(Float, nullable=True)
    take_profit_pct = Column(Float, nullable=True)
    
    # Conviction and holding period fields
    conviction_level = Column(String(20), nullable=True, default="MEDIUM")  # LOW, MEDIUM, HIGH
    holding_period_hours = Column(Integer, nullable=True, default=4)
    trading_type = Column(String(20), nullable=True, default="SWING")  # SCALP, SWING, POSITION, VOLATILE_EVENT
    holding_window_until = Column(DateTime(timezone=True), nullable=True)  # calculated: recommended_at + holding_period_hours

    __table_args__ = (
        Index("ix_trades_analysis_id", "analysis_id"),
        Index("ix_trades_symbol_recommended_at", "symbol", "recommended_at"),
        Index("ix_trades_underlying_symbol", "underlying_symbol"),
        Index("ix_trades_request_id", "request_id"),
        Index("ix_trades_holding_window_until", "holding_window_until"),
        Index("ix_trades_conviction_level", "conviction_level"),
    )


class TradeSnapshot(Base):
    """
    Immutable forward-price observation for a single trade horizon.
    Written once when a valid first-at-or-after-target price is found.
    """
    __tablename__ = "trade_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)

    horizon_label = Column(String(10), nullable=False)  # 1h, 4h, 1d, 3d, 1w
    horizon_minutes = Column(Integer, nullable=False)
    target_timestamp = Column(DateTime(timezone=True), nullable=False)

    observed_price = Column(Float, nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    source_interval = Column(String(10), nullable=False, default="15m")

    raw_return_pct = Column(Float, nullable=False)
    leveraged_return_pct = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("trade_id", "horizon_label", name="uq_trade_snapshots_trade_horizon"),
        Index("ix_trade_snapshots_trade_id", "trade_id"),
        Index("ix_trade_snapshots_horizon_label", "horizon_label"),
        Index("ix_trade_snapshots_target_timestamp", "target_timestamp"),
    )


class TradeExecution(Base):
    """
    User-recorded execution for a recommendation trade.
    Stores the actual side and fill price the user took.
    """
    __tablename__ = "trade_executions"

    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)

    executed_action = Column(String(10), nullable=False)  # BUY or SELL
    executed_price = Column(Float, nullable=False)
    executed_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    notes = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("trade_id", name="uq_trade_executions_trade_id"),
        Index("ix_trade_executions_trade_id", "trade_id"),
        Index("ix_trade_executions_executed_at", "executed_at"),
    )


class TradeClose(Base):
    """
    User-recorded closing trade for a recommendation.
    When present, this price is used as the definitive realized P&L.
    """
    __tablename__ = "trade_closes"

    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)

    closed_price = Column(Float, nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    notes = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("trade_id", name="uq_trade_closes_trade_id"),
        Index("ix_trade_closes_trade_id", "trade_id"),
    )


class PriceHistory(Base):
    """
    Daily OHLCV price history for tracked symbols.
    Persisted independently of analysis data — never cleared by reset-data.
    Used to compute technical indicators (RSI, MACD, SMA, etc.) without repeated yfinance calls.
    """
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(10), nullable=False)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    open = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    close = Column(Float, nullable=True)
    adj_close = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    source = Column(String(20), nullable=False, default="yfinance")
    fetched_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_price_history_symbol_date"),
        Index("ix_price_history_symbol_date", "symbol", "date"),
    )


class PaperTrade(Base):
    """
    Auto-executed paper trade simulating $100 per signal during market hours.
    One open position per underlying symbol at a time.
    Closed and replaced when the recommendation changes ticker, leverage, or direction.
    Closed without replacement on a HOLD signal.
    """
    __tablename__ = "paper_trades"

    id = Column(Integer, primary_key=True, index=True)
    underlying = Column(String(10), nullable=False)          # USO, QQQ, BITO, SPY, NVDA …
    execution_ticker = Column(String(10), nullable=False)    # UCO, TQQQ, SPXL, BITU …
    signal_type = Column(String(10), nullable=False)         # LONG or SHORT
    leverage = Column(String(10), nullable=False, default="1x")
    market_session = Column(String(20), nullable=True)       # open, pre-market, after-hours

    amount = Column(Float, nullable=False, default=100.0)    # dollars invested
    shares = Column(Float, nullable=False)                   # amount / entry_price
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)                # None = still open

    entered_at = Column(DateTime(timezone=True), nullable=False)
    exited_at = Column(DateTime(timezone=True), nullable=True)   # None = still open

    realized_pnl = Column(Float, nullable=True)              # None = still open
    realized_pnl_pct = Column(Float, nullable=True)

    analysis_request_id = Column(String(64), nullable=True)

    conviction_level = Column(String(10), nullable=True)       # HIGH, MEDIUM, LOW
    trading_type = Column(String(20), nullable=True)           # POSITION, SWING, VOLATILE_EVENT, SCALP
    holding_period_hours = Column(Integer, nullable=True)      # intended hold in hours
    holding_window_until = Column(DateTime(timezone=True), nullable=True)  # expiry of conviction window

    close_reason = Column(String(40), nullable=True)           # hold_signal, direction_flip, symbol_removed, window_expired, trailing_stop_hit

    trailing_stop_price = Column(Float, nullable=True)         # set when HOLD fires; position closes when price crosses this level
    best_price_seen = Column(Float, nullable=True)             # high-water mark (LONG) or low-water mark (SHORT) for trailing stop

    original_amount = Column(Float, nullable=True)             # initial entry amount before any accumulation; used to cap accumulation at max_multiplier × original

    ramp_stage = Column(String(20), nullable=True, default="probe")            # probe → building → full
    ramp_promotion_count = Column(Integer, nullable=True, default=0)           # number of stage promotions

    __table_args__ = (
        Index("ix_paper_trades_underlying", "underlying"),
        Index("ix_paper_trades_entered_at", "entered_at"),
        Index("ix_paper_trades_exited_at", "exited_at"),
    )


class AppConfig(Base):
    """
    Singleton application configuration and run-timing metadata.
    Stores autorun cadence, tracked symbols, prompt overrides, and last run timestamps.
    """
    __tablename__ = "app_config"

    id = Column(Integer, primary_key=True, default=1)

    auto_run_enabled = Column(Boolean, nullable=False, default=True)
    auto_run_interval_minutes = Column(Integer, nullable=False, default=30)
    tracked_symbols = Column(JSON, nullable=False, default=["USO", "IBIT", "QQQ", "SPY"])
    custom_symbols = Column(JSON, nullable=False, default=[])
    max_posts = Column(Integer, nullable=False, default=50)
    include_backtest = Column(Boolean, nullable=False, default=True)
    lookback_days = Column(Integer, nullable=False, default=14)
    symbol_prompt_overrides = Column(JSON, nullable=False, default={})
    symbol_company_aliases = Column(JSON, nullable=False, default={})
    symbol_proxy_terms = Column(JSON, nullable=False, default={})
    # ISO8601 timestamp per symbol of when its proxy terms were last generated —
    # drives the 30-day TTL recheck in ensure_symbol_proxy_terms_fresh().
    symbol_proxy_terms_generated_at = Column(JSON, nullable=False, default={})
    # EDGAR CIK cache: ticker → 10-digit zero-padded CIK string.
    # Populated once per symbol via EdgarClient.get_cik_for_ticker().
    symbol_edgar_ciks = Column(JSON, nullable=False, default={})
    display_timezone = Column(String(64), nullable=False, default="")
    enabled_rss_feeds = Column(JSON, nullable=False, default=[])
    custom_rss_feeds = Column(JSON, nullable=False, default=[])
    custom_rss_feed_labels = Column(JSON, nullable=False, default={})
    rss_article_detail_mode = Column(String(20), nullable=False, default="normal")
    rss_article_limits = Column(JSON, nullable=False, default={"light": 5, "normal": 10, "detailed": 20})
    data_ingestion_interval_seconds = Column(Integer, nullable=False, default=900)
    snapshot_retention_limit = Column(Integer, nullable=False, default=12)
    extraction_model = Column(String(128), nullable=False, default="")
    reasoning_model = Column(String(128), nullable=False, default="")
    # Parallel Ollama slots for Stage 2 specialist calls. 1 = serialized (safe
    # for any single-GPU box). >1 only when you have GPU VRAM headroom AND have
    # set OLLAMA_NUM_PARALLEL on the Ollama side to match.
    ollama_parallel_slots = Column(Integer, nullable=False, default=1)
    # Toggle the red-team adversarial review pass. Off saves ~one Ollama call
    # per analysis at the cost of losing the bias/risk countercheck.
    red_team_enabled = Column(Boolean, nullable=False, default=True)
    # LLM inference backend. "ollama" uses /api/generate; "vllm" uses the
    # OpenAI-compatible /v1/completions API (set VLLM_URL to point at it);
    # "openai" uses the chat completions API via services/openai_client.py.
    inference_backend = Column(String(16), nullable=False, default="ollama")
    # URL overrides for each inference backend.
    # When set (non-empty), these override the corresponding env var.
    # When empty, the engine falls back to the env var, then the hardcoded default.
    ollama_url = Column(String(256), nullable=False, default="http://localhost:11434/api/generate")
    vllm_url = Column(String(256), nullable=False, default="http://localhost:8000")
    # OpenAI / OpenAI-compatible cloud LLM settings.
    # API key is stored in the OS keychain via secret_store.py, not in the DB.
    openai_base_url = Column(String(256), nullable=False, default="https://api.openai.com/v1")
    openai_model = Column(String(128), nullable=False, default="gpt-4o-mini")
    # Cloud/Local toggle state (new UI fields, persisted for round-trip fidelity)
    api_mode = Column(String(16), nullable=False, default="local")
    cloud_provider = Column(String(32), nullable=False, default="openai")
    local_provider = Column(String(32), nullable=False, default="ollama")
    risk_profile = Column(String(20), nullable=False, default="standard")
    risk_policy = Column(JSON, nullable=False, default={})
    web_research_enabled = Column(Boolean, nullable=False, default=True)
    allow_extended_hours_trading = Column(Boolean, nullable=False, default=True)
    remote_snapshot_enabled = Column(Boolean, nullable=False, default=False)
    telegram_remote_control_enabled = Column(Boolean, nullable=False, default=False)
    telegram_remote_control_banner_active = Column(Boolean, nullable=False, default=False)
    telegram_remote_control_banner_message = Column(Text, nullable=True, default=None)
    telegram_remote_control_banner_updated_at = Column(DateTime(timezone=True), nullable=True, default=None)
    remote_snapshot_mode = Column(String(20), nullable=False, default="telegram")
    remote_snapshot_min_pnl_change_usd = Column(Float, nullable=False, default=5.0)
    remote_snapshot_heartbeat_minutes = Column(Integer, nullable=False, default=360)
    remote_snapshot_interval_minutes = Column(Integer, nullable=False, default=360)
    remote_snapshot_send_on_position_change = Column(Boolean, nullable=False, default=True)
    remote_snapshot_include_closed_trades = Column(Boolean, nullable=False, default=False)
    remote_snapshot_max_recommendations = Column(Integer, nullable=False, default=4)

    # ── EDGAR filings config overrides (null = use logic_config.json default) ──
    edgar_filings_enabled = Column(Boolean, nullable=True, default=None)
    edgar_filings_poll_interval_minutes = Column(Integer, nullable=True, default=None)
    edgar_filings_tracked_form_types = Column(JSON, nullable=True, default=None)
    edgar_filings_material_8k_items = Column(JSON, nullable=True, default=None)

    # Trading logic overrides — null means "use logic_config.json default"
    vol_sizing_portfolio_cap_usd = Column(Float, nullable=True, default=None)
    paper_trade_amount = Column(Float, nullable=True, default=None)
    entry_threshold = Column(Float, nullable=True, default=None)
    stop_loss_pct = Column(Float, nullable=True, default=None)
    take_profit_pct = Column(Float, nullable=True, default=None)
    materiality_min_posts_delta = Column(Integer, nullable=True, default=None)
    materiality_min_sentiment_delta = Column(Float, nullable=True, default=None)
    hold_overnight = Column(Boolean, nullable=False, default=False)
    trail_on_window_expiry = Column(Boolean, nullable=False, default=True)
    reentry_cooldown_minutes = Column(Integer, nullable=True, default=None)
    min_same_day_exit_edge_pct = Column(Float, nullable=True, default=None)

    # ── Strategy feature toggles (null = use logic_config.json default) ─────
    continuous_entry_enabled = Column(Boolean, nullable=True, default=None)
    regime_adaptation_enabled = Column(Boolean, nullable=True, default=None)
    hold_decay_enabled = Column(Boolean, nullable=True, default=None)
    accumulate_on_confirmation_enabled = Column(Boolean, nullable=True, default=None)
    accumulate_max_multiplier = Column(Float, nullable=True, default=None)

    # ── Alpaca brokerage execution ────────────────────────────────────────────
    alpaca_execution_mode         = Column(String(10),  nullable=False, default="off")  # off | paper | live
    alpaca_pre_stop_mode          = Column(String(10),  nullable=True,  default=None)   # saved by /stop bot command
    alpaca_live_trading_enabled   = Column(Boolean,     nullable=False, default=False)
    alpaca_allow_short_selling    = Column(Boolean,     nullable=False, default=False)
    alpaca_fixed_order_size       = Column(Boolean,     nullable=False, default=False)
    alpaca_paper_trade_amount_usd = Column(Float,       nullable=True,  default=None)
    alpaca_live_trade_amount_usd  = Column(Float,       nullable=True,  default=None)
    alpaca_max_position_usd       = Column(Float,       nullable=True,  default=None)
    alpaca_max_total_exposure_usd = Column(Float,       nullable=True,  default=None)
    alpaca_order_type             = Column(String(20),  nullable=False, default="market")
    alpaca_limit_slippage_pct     = Column(Float,       nullable=False, default=0.002)
    alpaca_daily_loss_limit_usd   = Column(Float,       nullable=True,  default=None)
    alpaca_max_consecutive_losses = Column(Integer,     nullable=True,  default=3)
    circuit_breaker_fired_at      = Column(DateTime(timezone=True), nullable=True, default=None)
    circuit_breaker_reason        = Column(String(255), nullable=True,  default=None)

    last_analysis_started_at = Column(DateTime(timezone=True), nullable=True)
    last_analysis_completed_at = Column(DateTime(timezone=True), nullable=True)
    last_analysis_request_id = Column(String(36), nullable=True)
    last_remote_snapshot_sent_at = Column(DateTime(timezone=True), nullable=True)
    last_remote_snapshot_request_id = Column(String(36), nullable=True)
    last_remote_snapshot_net_pnl = Column(Float, nullable=True)
    last_remote_snapshot_recommendation_fingerprint = Column(String(255), nullable=True)
    analysis_lock_request_id = Column(String(36), nullable=True)
    analysis_lock_acquired_at = Column(DateTime(timezone=True), nullable=True)
    analysis_lock_expires_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_app_config_last_analysis_started_at", "last_analysis_started_at"),
        Index("ix_app_config_analysis_lock_expires_at", "analysis_lock_expires_at"),
    )


class AlpacaOrder(Base):
    """
    Record of every order attempt sent (or attempted) to the Alpaca brokerage API.
    Written for both successes and failures; paper_trade_id links back to the
    originating paper trade so the two can be compared side-by-side.
    """
    __tablename__ = "alpaca_orders"

    id                = Column(Integer,  primary_key=True)
    paper_trade_id    = Column(Integer,  ForeignKey("paper_trades.id"), nullable=True, index=True)
    alpaca_order_id   = Column(String(64),  nullable=True,  index=True)
    client_order_id   = Column(String(128), nullable=True,  unique=True)
    symbol            = Column(String(20),  nullable=False)
    side              = Column(String(10),  nullable=False)   # buy | sell
    notional          = Column(Float,       nullable=True)
    qty               = Column(Float,       nullable=True)
    order_type        = Column(String(20),  nullable=False, default="market")
    time_in_force     = Column(String(10),  nullable=False, default="day")
    limit_price       = Column(Float,       nullable=True)
    extended_hours    = Column(Boolean,     nullable=False, default=False)
    status            = Column(String(30),  nullable=True)   # filled | cancelled | rejected | error
    filled_qty        = Column(Float,       nullable=True)
    filled_avg_price  = Column(Float,       nullable=True)
    submitted_at      = Column(DateTime(timezone=True), nullable=True)
    filled_at         = Column(DateTime(timezone=True), nullable=True)
    trading_mode      = Column(String(10),  nullable=False, default="paper")   # paper | live
    raw_response      = Column(JSON,        nullable=True)
    error_message     = Column(Text,        nullable=True)
    created_at        = Column(DateTime(timezone=True), nullable=False, default=func.now())
    is_orphan             = Column(Boolean, nullable=False, default=False)
    orphan_acknowledged   = Column(Boolean, nullable=False, default=False)

class AlpacaDispatchError(Base):
    """
    Tracks dispatch-level errors where a close could not be executed
    before an open, potentially leaving opposing positions open.
    These are surfaced on the trading page for user awareness.
    """
    __tablename__ = "alpaca_dispatch_errors"

    id              = Column(Integer, primary_key=True)
    symbol          = Column(String(20),  nullable=False)
    underlying      = Column(String(20),  nullable=True)
    error_type      = Column(String(50),  nullable=False)  # "close_failed", "conflict_detected"
    error_message   = Column(Text,        nullable=True)
    paper_trade_id  = Column(Integer,     nullable=True)
    trading_mode    = Column(String(10),  nullable=False, default="live")
    acknowledged    = Column(Boolean,     nullable=False, default=False)
    created_at      = Column(DateTime(timezone=True), nullable=False, default=func.now())


class SecFiling(Base):
    """
    SEC EDGAR filing data for tracked symbols.
    Stores filing metadata and extracted text for LLM processing.
    """
    __tablename__ = "sec_filings"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(10), nullable=False)
    cik = Column(String(10), nullable=False)
    accession_number = Column(String(24), nullable=False, unique=True)
    form_type = Column(String(20), nullable=False)          # "10-K", "10-Q", "8-K", ...
    filing_date = Column(DateTime(timezone=True), nullable=False)
    report_date = Column(DateTime(timezone=True), nullable=True)
    items = Column(String(64), nullable=True)              # 8-K item codes, comma-separated
    primary_document_url = Column(Text, nullable=False)
    raw_text = Column(Text, nullable=True)                 # extracted filing text (or key sections)
    llm_summary = Column(Text, nullable=True)              # Stage 1 structured extraction / summary
    processed = Column(Boolean, nullable=False, default=False)
    discovered_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_sec_filings_symbol", "symbol"),
        Index("ix_sec_filings_processed", "processed"),
        Index("ix_sec_filings_filing_date", "filing_date"),
    )


class AuditLog(Base):
    """
    Audit trail for state-changing operations.
    Every config change, trade execution, secret save/clear, and data reset is recorded here.
    """
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(50), nullable=False, index=True)         # e.g. config_update, trade_execute
    resource = Column(String(50), nullable=False, index=True)       # e.g. config, trade, alpaca_secret
    resource_id = Column(String(100), nullable=True)                # specific ID if applicable
    detail = Column(Text, nullable=True)                            # human-readable description
    event_metadata = Column("event_metadata", JSON, nullable=True)  # structured before/after context
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

    __table_args__ = (
        Index("ix_audit_log_action_resource", "action", "resource"),
        Index("ix_audit_log_created_at", "created_at"),
    )


# Create all tables
def init_db():
    """Initialize database by creating all tables."""
    Base.metadata.create_all(bind=engine)
    try:
        from .migrate import migrate
        migrate()
    except Exception as exc:
        print(f"Database migration warning: {exc}")


# Drop all tables (for testing/reinitialization)
def drop_db():
    """Drop all tables from the database."""
    Base.metadata.drop_all(bind=engine)


# ═══════════════════════════════════════════════════════════════════════
# Decision Log Models (separate DB — decision_log.db)
# ═══════════════════════════════════════════════════════════════════════

# These models use the decision_log_db engine, not the main engine.
# They're defined here so all models are in one file for import convenience.
# The actual DB binding is done in decision_logger.py using its own Base.

from sqlalchemy.orm import declarative_base as _declarative_base

DecisionLogBase = _declarative_base()


class DecisionLogRun(DecisionLogBase):
    """One row per full analysis run."""
    __tablename__ = "decision_log_run"

    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), unique=True, nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    trigger_source = Column(String(32), nullable=True)
    extraction_model = Column(String(128), nullable=True)
    reasoning_model = Column(String(128), nullable=True)
    config_hash = Column(String(64), nullable=True)
    config_snapshot = Column(JSON, nullable=True)
    total_articles_considered = Column(Integer, nullable=True)
    total_articles_used = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)


class DecisionLogSymbol(DecisionLogBase):
    """One row per symbol per analysis run."""
    __tablename__ = "decision_log_symbol"

    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), ForeignKey("decision_log_run.run_id"), nullable=False, index=True)
    symbol = Column(String(16), nullable=False)
    blue_team_structured_output = Column(JSON, nullable=True)
    red_team_structured_output = Column(JSON, nullable=True)
    raw_bluster_score = Column(Float, nullable=True)
    raw_policy_score = Column(Float, nullable=True)
    raw_confidence_score = Column(Float, nullable=True)
    raw_directional_score = Column(Float, nullable=True)
    decay_half_life_hours = Column(Float, nullable=True)
    hold_decay_half_life_hours = Column(Float, nullable=True)
    decay_factor_applied = Column(Float, nullable=True)
    hold_decay_factor_applied = Column(Float, nullable=True)
    blended_bluster_score = Column(Float, nullable=True)
    blended_policy_score = Column(Float, nullable=True)
    blended_confidence_score = Column(Float, nullable=True)
    blended_directional_score = Column(Float, nullable=True)
    final_signal_type = Column(String(16), nullable=True)
    final_conviction = Column(String(16), nullable=True)
    final_trading_type = Column(String(32), nullable=True)
    final_holding_window_hours = Column(Float, nullable=True)
    final_urgency = Column(String(16), nullable=True)
    final_stop_loss_pct = Column(Float, nullable=True)
    final_take_profit_pct = Column(Float, nullable=True)
    data_gap_hold = Column(Integer, nullable=True)
    atr_14d_pct = Column(Float, nullable=True)
    regime_adaptation_triggered = Column(Integer, nullable=True)
    regime_adaptation_atr_pct = Column(Float, nullable=True)
    regime_adaptation_original_threshold = Column(Float, nullable=True)
    regime_adaptation_adjusted_threshold = Column(Float, nullable=True)
    entry_threshold_used = Column(Float, nullable=True)
    materiality_gate_checked = Column(Integer, nullable=True)
    materiality_gate_blocked = Column(Integer, nullable=True)
    materiality_gate_reason = Column(Text, nullable=True)
    materiality_rolling_baseline = Column(JSON, nullable=True)
    red_team_disagreed = Column(Integer, nullable=True)
    red_team_confidence_delta = Column(Float, nullable=True)
    red_team_override_resolved_to = Column(String(16), nullable=True)
    event_type = Column(String(32), nullable=True)          # promoted from blue_team JSON for fast querying
    keyword_attribution = Column(JSON, nullable=True)       # Stage 1 term hit counts + source breakdown

    __table_args__ = (
        Index("ix_dl_symbol_run_sym", "run_id", "symbol"),
    )


class DecisionLogArticle(DecisionLogBase):
    """Articles considered per symbol during a run."""
    __tablename__ = "decision_log_article"

    id = Column(Integer, primary_key=True)
    symbol_log_id = Column(Integer, ForeignKey("decision_log_symbol.id"), nullable=False, index=True)
    title = Column(Text, nullable=True)
    source = Column(String(128), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    url_hash = Column(String(64), nullable=True)
    was_used = Column(Integer, nullable=True)
    relevance_score = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_dl_article_sym_log", "symbol_log_id"),
    )


class DecisionLogBlend(DecisionLogBase):
    """Prior runs that contributed to blended scores for a symbol."""
    __tablename__ = "decision_log_blend"

    id = Column(Integer, primary_key=True)
    symbol_log_id = Column(Integer, ForeignKey("decision_log_symbol.id"), nullable=False, index=True)
    prior_run_timestamp = Column(DateTime(timezone=True), nullable=True)
    prior_run_request_id = Column(String(64), nullable=True)
    weight = Column(Float, nullable=True)
    prior_directional_score = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_dl_blend_sym_log", "symbol_log_id"),
    )


class DecisionLogTechnical(DecisionLogBase):
    """Technical indicators evaluated for a symbol during a run."""
    __tablename__ = "decision_log_technical"

    id = Column(Integer, primary_key=True)
    symbol_log_id = Column(Integer, ForeignKey("decision_log_symbol.id"), nullable=False, index=True)
    rsi_14 = Column(Float, nullable=True)
    sma_50 = Column(Float, nullable=True)
    sma_200 = Column(Float, nullable=True)
    golden_cross = Column(Integer, nullable=True)
    death_cross = Column(Integer, nullable=True)
    macd_positive = Column(Integer, nullable=True)
    volume_above_average = Column(Integer, nullable=True)
    bb_position = Column(String(16), nullable=True)
    confidence_adjustment_total = Column(Float, nullable=True)
    adjustment_breakdown = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_dl_tech_sym_log", "symbol_log_id"),
    )


class DecisionLogTrade(DecisionLogBase):
    """One row per trade (position open → close)."""
    __tablename__ = "decision_log_trade"

    id = Column(Integer, primary_key=True)
    paper_trade_id = Column(Integer, nullable=True, index=True)
    symbol = Column(String(16), nullable=False)
    direction = Column(String(8), nullable=False)
    entry_timestamp = Column(DateTime(timezone=True), nullable=False)
    entry_price = Column(Float, nullable=True)
    entry_directional_score = Column(Float, nullable=True)
    entry_confidence = Column(Float, nullable=True)
    entry_atr_pct = Column(Float, nullable=True)
    entry_trade_size = Column(Float, nullable=True)
    entry_size_reasoning = Column(Text, nullable=True)
    entry_leverage = Column(Integer, nullable=True)
    entry_leverage_reasoning = Column(Text, nullable=True)
    holding_window_hours = Column(Float, nullable=True)
    close_timestamp = Column(DateTime(timezone=True), nullable=True)
    close_price = Column(Float, nullable=True)
    close_trigger = Column(String(32), nullable=True)
    close_final_score = Column(Float, nullable=True)
    realized_pnl = Column(Float, nullable=True)
    closed = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_dl_trade_symbol", "symbol"),
        Index("ix_dl_trade_paper_id", "paper_trade_id"),
    )


class DecisionLogTradeEvent(DecisionLogBase):
    """Every lifecycle event for a trade (append-only)."""
    __tablename__ = "decision_log_trade_event"

    id = Column(Integer, primary_key=True)
    trade_log_id = Column(Integer, ForeignKey("decision_log_trade.id"), nullable=False, index=True)
    event_type = Column(String(32), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    run_id = Column(String(64), nullable=True)
    directional_score = Column(Float, nullable=True)
    decay_factor = Column(Float, nullable=True)
    keep_vs_close = Column(String(32), nullable=True)
    decision_reason = Column(Text, nullable=True)
    event_details = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_dl_te_trade_log", "trade_log_id"),
        Index("ix_dl_te_event_type", "event_type"),
    )


class DecisionLogDecisionDiff(DecisionLogBase):
    """Mid-position decision changes logged separately."""
    __tablename__ = "decision_log_decision_diff"

    id = Column(Integer, primary_key=True)
    trade_event_id = Column(Integer, ForeignKey("decision_log_trade_event.id"), nullable=True, index=True)
    run_id = Column(String(64), nullable=False)
    before_directional_score = Column(Float, nullable=True)
    after_directional_score = Column(Float, nullable=True)
    before_signal_type = Column(String(16), nullable=True)
    after_signal_type = Column(String(16), nullable=True)
    before_size_pct = Column(Float, nullable=True)
    after_size_pct = Column(Float, nullable=True)
    reason_code = Column(String(64), nullable=True)


class DecisionLogFeedback(DecisionLogBase):
    """Weekly performance feedback analysis results and user acceptance."""
    __tablename__ = "decision_log_feedback"

    id = Column(Integer, primary_key=True)
    feedback_type = Column(String(32), nullable=False, index=True)
    analysis_data = Column(Text, nullable=True)
    timestamp_data = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
