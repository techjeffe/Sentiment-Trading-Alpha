// ─── Config Normalizer ──────────────────────────────────────────────
// Shared config types, defaults, and normalization logic for admin pages.
// NOTE: AppConfig here is a local extended type — the one in @/lib/types/analysis.ts
// is a stripped-down version used by Dashboard components.

// ─── Rss Feed Option ────────────────────────────────────────────────

export type RssFeedOption = {
    key: string;
    label: string;
    url: string;
};

// ─── Extended AppConfig (admin-only fields) ─────────────────────

export type AppConfig = {
    auto_run_enabled: boolean;
    auto_run_interval_minutes: number;
    tracked_symbols: string[];
    custom_symbols: string[];
    default_symbols: string[];
    max_custom_symbols: number;
    max_posts: number;
    lookback_days: number;
    symbol_prompt_overrides: Record<string, string>;
    symbol_company_aliases: Record<string, string>;
    display_timezone: string;
    data_ingestion_interval_seconds: number;
    snapshot_retention_limit: number;
    extraction_model: string;
    reasoning_model: string;
    ollama_parallel_slots: number;
    inference_backend: string;
    ollama_url: string;
    vllm_url: string;
    openai_base_url: string;
    openai_model: string;
    // ── Hierarchical API selection (UI state) ──
    api_mode: "cloud" | "local";
    cloud_provider: string;
    local_provider: string;
    api_url: string;
    user_edited_url: boolean;
    red_team_enabled: boolean;
    risk_profile: string;
    risk_policy?: {
        crazy_ramp?: {
            threshold_source?: string;
            fetch_timeout_ms?: number;
            eval_timeout_ms?: number;
            stale_ms?: number;
            fallback?: {
                breakout_atr_fraction?: number;
                volume_multiplier?: number;
                retrace_guard?: number;
            };
        };
    };
    allow_extended_hours_trading: boolean;
    hold_overnight: boolean;
    trail_on_window_expiry: boolean;
    reentry_cooldown_minutes: number | null;
    min_same_day_exit_edge_pct: number | null;
    // ── Churn guards (null = use logic_config.json default) ──
    entry_confirmation_runs_required: number | null;
    reentry_no_flip_minutes: number | null;
    same_day_no_rebuy_minutes: number | null;
    trailing_warmup_minutes: number | null;
    trailing_min_favorable_move_pct: number | null;
    remote_snapshot_enabled: boolean;
    telegram_remote_control_enabled: boolean;
    telegram_remote_control_banner_active: boolean;
    telegram_remote_control_banner_message: string;
    telegram_remote_control_banner_updated_at: string | null;
    remote_snapshot_mode: "telegram";
    remote_snapshot_interval_minutes: number;
    remote_snapshot_send_on_position_change: boolean;
    remote_snapshot_include_closed_trades: boolean;
    remote_snapshot_max_recommendations: number;
    vol_sizing_portfolio_cap_usd: number | null;
    paper_trade_amount: number | null;
    entry_threshold: number | null;
    stop_loss_pct: number | null;
    take_profit_pct: number | null;
    materiality_min_posts_delta: number | null;
    materiality_min_sentiment_delta: number | null;
    // ── Strategy feature toggles (null = use logic_config.json default) ──
    continuous_entry_enabled: boolean | null;
    regime_adaptation_enabled: boolean | null;
    hold_decay_enabled: boolean | null;

    logic_defaults: {
        paper_trade_amount: number;
        entry_threshold: number;
        stop_loss_pct: number;
        take_profit_pct: number;
        materiality_min_posts_delta: number;
        materiality_min_sentiment_delta: number;
        reentry_cooldown_minutes: number;
        min_same_day_exit_edge_pct: number;
        entry_confirmation_runs_required: number;
        reentry_no_flip_minutes: number;
        same_day_no_rebuy_minutes: number;
        trailing_warmup_minutes: number;
        trailing_min_favorable_move_pct: number;
        crazy?: {
            entry_threshold: number;
            stop_loss_pct: number;
            take_profit_pct: number;
            materiality_min_posts_delta: number;
            materiality_min_sentiment_delta: number;
            reentry_cooldown_minutes: number;
            min_same_day_exit_edge_pct: number;
        };
    };
    available_models: string[];
    local_models: string[];
    cloud_models: string[];
    last_analysis_started_at: string | null;
    last_analysis_completed_at: string | null;
    last_analysis_request_id: string | null;
    last_remote_snapshot_sent_at: string | null;
    last_remote_snapshot_request_id: string | null;
    seconds_until_next_auto_run: number;
    can_auto_run_now: boolean;
    supported_symbols: string[];
    default_rss_feeds: RssFeedOption[];
    custom_rss_feeds: string[];
    custom_rss_feed_labels: Record<string, string>;
    enabled_rss_feeds: string[];
    supported_rss_feeds: RssFeedOption[];
    max_custom_rss_feeds: number;
    rss_article_detail_mode: "light" | "normal" | "detailed";
    rss_article_limits: {
        light: number;
        normal: number;
        detailed: number;
    };
    rss_articles_per_feed: number;
    notices?: string[];
    alpaca_execution_mode: "off" | "paper" | "live";
    alpaca_live_trading_enabled: boolean;
    alpaca_allow_short_selling: boolean;
    alpaca_fixed_order_size: boolean;
    alpaca_paper_trade_amount_usd: number | null;
    alpaca_live_trade_amount_usd: number | null;
    alpaca_max_position_usd: number | null;
    alpaca_max_total_exposure_usd: number | null;
    alpaca_order_type: string;
    alpaca_limit_slippage_pct: number;
    alpaca_daily_loss_limit_usd: number | null;
    alpaca_max_consecutive_losses: number | null;
};

function normalizeRiskProfile(value: unknown): string {
    const profile = String(value ?? "").trim().toLowerCase();
    if (profile === "moderate" || profile === "aggressive") return "standard";
    if (profile === "conservative" || profile === "standard" || profile === "crazy" || profile === "custom") {
        return profile;
    }
    return "standard";
}

// ─── Empty / Default Config ─────────────────────────────────────────

export const EMPTY_CONFIG: AppConfig = {
    auto_run_enabled: true,
    auto_run_interval_minutes: 30,
    tracked_symbols: ["USO", "IBIT", "QQQ", "SPY"],
    custom_symbols: [],
    default_symbols: ["USO", "IBIT", "QQQ", "SPY"],
    max_custom_symbols: 50,
    max_posts: 50,
    lookback_days: 14,
    symbol_prompt_overrides: {},
    symbol_company_aliases: {},
    display_timezone: "",
    data_ingestion_interval_seconds: 900,
    snapshot_retention_limit: 12,
    extraction_model: "",
    reasoning_model: "",
    ollama_parallel_slots: 1,
    inference_backend: "ollama",
    ollama_url: "http://localhost:11434/api/generate",
    vllm_url: "http://localhost:8000",
    openai_base_url: "https://api.openai.com/v1",
    openai_model: "gpt-4o-mini",
    api_mode: "local",
    cloud_provider: "openai",
    local_provider: "ollama",
    api_url: "http://localhost:11434",
    user_edited_url: false,
    red_team_enabled: true,
    risk_profile: "standard",
    risk_policy: {
        crazy_ramp: {
            threshold_source: "calibrated_bucket",
            fetch_timeout_ms: 2500,
            eval_timeout_ms: 15000,
            stale_ms: 120000,
            fallback: {
                breakout_atr_fraction: 0.45,
                volume_multiplier: 2.0,
                retrace_guard: 0.2,
            },
        },
    },
    allow_extended_hours_trading: true,
    hold_overnight: false,
    trail_on_window_expiry: true,
    reentry_cooldown_minutes: null,
    min_same_day_exit_edge_pct: null,
    entry_confirmation_runs_required: null,
    reentry_no_flip_minutes: null,
    same_day_no_rebuy_minutes: null,
    trailing_warmup_minutes: null,
    trailing_min_favorable_move_pct: null,
    continuous_entry_enabled: null,
    regime_adaptation_enabled: null,
    hold_decay_enabled: null,
    remote_snapshot_enabled: false,
    telegram_remote_control_enabled: false,
    telegram_remote_control_banner_active: false,
    telegram_remote_control_banner_message: "",
    telegram_remote_control_banner_updated_at: null,
    remote_snapshot_mode: "telegram",
    remote_snapshot_interval_minutes: 360,
    remote_snapshot_send_on_position_change: true,
    remote_snapshot_include_closed_trades: false,
    remote_snapshot_max_recommendations: 4,
    vol_sizing_portfolio_cap_usd: null,
    paper_trade_amount: null,
    entry_threshold: null,
    stop_loss_pct: null,
    take_profit_pct: null,
    materiality_min_posts_delta: null,
    materiality_min_sentiment_delta: null,
    logic_defaults: {
        paper_trade_amount: 100,
        entry_threshold: 0.42,
        stop_loss_pct: 2.0,
        take_profit_pct: 3.0,
        materiality_min_posts_delta: 6,
        materiality_min_sentiment_delta: 0.24,
        reentry_cooldown_minutes: 120,
        min_same_day_exit_edge_pct: 0.5,
        entry_confirmation_runs_required: 3,
        reentry_no_flip_minutes: 60,
        same_day_no_rebuy_minutes: 60,
        trailing_warmup_minutes: 30,
        trailing_min_favorable_move_pct: 0.5,
    },
    available_models: [],
    local_models: [],
    cloud_models: [],
    last_analysis_started_at: null,
    last_analysis_completed_at: null,
    last_analysis_request_id: null,
    last_remote_snapshot_sent_at: null,
    last_remote_snapshot_request_id: null,
    seconds_until_next_auto_run: 0,
    can_auto_run_now: true,
    supported_symbols: ["USO", "IBIT", "QQQ", "SPY"],
    default_rss_feeds: [],
    custom_rss_feeds: [],
    custom_rss_feed_labels: {},
    enabled_rss_feeds: [],
    supported_rss_feeds: [],
    max_custom_rss_feeds: 3,
    rss_article_detail_mode: "normal",
    rss_article_limits: { light: 5, normal: 10, detailed: 20 },
    rss_articles_per_feed: 15,
    alpaca_execution_mode: "off",
    alpaca_live_trading_enabled: false,
    alpaca_allow_short_selling: false,
    alpaca_fixed_order_size: false,
    alpaca_paper_trade_amount_usd: null,
    alpaca_live_trade_amount_usd: null,
    alpaca_max_position_usd: null,
    alpaca_max_total_exposure_usd: null,
    alpaca_order_type: "market",
    alpaca_limit_slippage_pct: 0.002,
    alpaca_daily_loss_limit_usd: null,
    alpaca_max_consecutive_losses: 3,
};

// ─── Basic Mode Defaults ────────────────────────────────────────────

export const BASIC_MODE_DEFAULTS: Partial<AppConfig> = {
    max_posts: 50,
    lookback_days: 14,
    data_ingestion_interval_seconds: 900,
    snapshot_retention_limit: 12,
    ollama_parallel_slots: 1,
    red_team_enabled: true,
    allow_extended_hours_trading: true,
    hold_overnight: false,
    trail_on_window_expiry: true,
    reentry_cooldown_minutes: null,
    min_same_day_exit_edge_pct: null,
    vol_sizing_portfolio_cap_usd: null,
    paper_trade_amount: null,
    entry_threshold: null,
    stop_loss_pct: null,
    take_profit_pct: null,
    materiality_min_posts_delta: null,
    materiality_min_sentiment_delta: null,
    remote_snapshot_mode: "telegram",
    remote_snapshot_interval_minutes: 360,
    remote_snapshot_send_on_position_change: true,
    remote_snapshot_include_closed_trades: false,
    remote_snapshot_max_recommendations: 4,
    rss_article_limits: { light: 5, normal: 10, detailed: 20 },
};

// ─── Config Normalizer ──────────────────────────────────────────────

export function normalizeConfigPayload(payload: Partial<AppConfig> | null | undefined): AppConfig {
    const next = {
        ...EMPTY_CONFIG,
        ...(payload ?? {}),
    } as AppConfig;
    const executionMode = next.alpaca_execution_mode === "paper" || next.alpaca_execution_mode === "live"
        ? next.alpaca_execution_mode
        : "off";

    return {
        ...next,
        risk_profile: normalizeRiskProfile(next.risk_profile),
        alpaca_execution_mode: executionMode,
        alpaca_live_trading_enabled: executionMode === "live",
        tracked_symbols: Array.isArray(next.tracked_symbols) ? next.tracked_symbols : EMPTY_CONFIG.tracked_symbols,
        custom_symbols: Array.isArray(next.custom_symbols) ? next.custom_symbols : EMPTY_CONFIG.custom_symbols,
        default_symbols: Array.isArray(next.default_symbols) ? next.default_symbols : EMPTY_CONFIG.default_symbols,
        symbol_prompt_overrides: next.symbol_prompt_overrides ?? {},
        symbol_company_aliases: next.symbol_company_aliases ?? {},
        logic_defaults: {
            ...EMPTY_CONFIG.logic_defaults,
            ...(next.logic_defaults ?? {}),
        },
        risk_policy: next.risk_policy ?? EMPTY_CONFIG.risk_policy,
        telegram_remote_control_banner_active: !!next.telegram_remote_control_banner_active,
        telegram_remote_control_banner_message: String(next.telegram_remote_control_banner_message || ""),
        telegram_remote_control_banner_updated_at: next.telegram_remote_control_banner_updated_at || null,
        available_models: Array.isArray(next.available_models) ? next.available_models : [],
        local_models: Array.isArray(next.local_models) ? next.local_models : [],
        cloud_models: Array.isArray(next.cloud_models) ? next.cloud_models : [],
        supported_symbols: Array.isArray(next.supported_symbols) ? next.supported_symbols : EMPTY_CONFIG.supported_symbols,
        default_rss_feeds: Array.isArray(next.default_rss_feeds) ? next.default_rss_feeds : [],
        custom_rss_feeds: Array.isArray(next.custom_rss_feeds) ? next.custom_rss_feeds : [],
        custom_rss_feed_labels: next.custom_rss_feed_labels ?? {},
        enabled_rss_feeds: Array.isArray(next.enabled_rss_feeds) ? next.enabled_rss_feeds : [],
        supported_rss_feeds: Array.isArray(next.supported_rss_feeds) ? next.supported_rss_feeds : [],
        rss_article_limits: {
            ...EMPTY_CONFIG.rss_article_limits,
            ...(next.rss_article_limits ?? {}),
        },
        notices: Array.isArray(next.notices) ? next.notices : [],
    };
}
