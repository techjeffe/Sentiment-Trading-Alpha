"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { formatTs, useTimezone, COMMON_TIMEZONES } from "@/lib/timezone";

// Utilities
import { EMPTY_CONFIG, BASIC_MODE_DEFAULTS, normalizeConfigPayload, AppConfig } from "@/lib/utils/config-normalizer";
import { normalizeSymbolInput, normalizeFeedUrl, normalizeArticleLimit } from "@/lib/constants/feed-utils";

// Helper function to safely derive provider from URL
function deriveProviderFromUrl(urlStr: string): string {
    try {
        const url = new URL(urlStr);
        const hostname = url.hostname.toLowerCase();
        
        // Check exact hostname or ends with the domain (to handle subdomains)
        if (hostname === 'openrouter.ai' || hostname.endsWith('.openrouter.ai')) {
            return "openrouter";
        }
        if (hostname === 'anthropic.com' || hostname.endsWith('.anthropic.com')) {
            return "anthropic";
        }
        if (hostname === 'googleapis.com' || hostname.endsWith('.googleapis.com') || 
            hostname === 'generativelanguage.googleapis.com' || hostname.endsWith('.generativelanguage.googleapis.com')) {
            return "google";
        }
        if (hostname === 'openai.com' || hostname.endsWith('.openai.com')) {
            return "openai";
        }
        return "custom";
    } catch {
        // Invalid URL, return custom
        return "custom";
    }
}

// Sections
import { OverviewSection } from "@/components/admin/sections/OverviewSection";
import { TradingLogicSection } from "@/components/admin/sections/TradingLogicSection";
import { TradingBehaviorSection } from "@/components/admin/sections/TradingBehaviorSection";
import { SymbolsSection } from "@/components/admin/sections/SymbolsSection";
import { RssSection } from "@/components/admin/sections/RssSection";
import { PromptOverridesSection } from "@/components/admin/sections/PromptOverridesSection";
import { ExecutionsSection } from "@/components/admin/sections/ExecutionsSection";
import { SystemSection } from "@/components/admin/sections/SystemSection";
import { RemoteSnapshotSection } from "@/components/admin/sections/RemoteSnapshotSection";
import { BrokerageSection } from "@/components/admin/sections/BrokerageSection";
import { PriceHistorySection } from "@/components/admin/sections/PriceHistorySection";
import { CloudLLMSection } from "@/components/admin/sections/CloudLLMSection";

// Modals
import { DangerZoneModal } from "@/components/admin/modals/DangerZoneModal";
import { DirtyModal } from "@/components/admin/modals/DirtyModal";
import { BasicModeModal } from "@/components/admin/modals/BasicModeModal";
import { RemoteSnapshotSetupModal } from "@/components/admin/modals/RemoteSnapshotSetupModal";
import { LiveConfirmModal } from "@/components/admin/modals/LiveConfirmModal";
import { CustomRiskModal } from "@/components/admin/modals/CustomRiskModal";

// Re-export types needed by components
type RssFeedOption = {
    key: string;
    label: string;
    url: string;
};

type AlpacaStatus = {
    secrets: {
        configured: boolean;
        paper: { configured: boolean; api_key_masked: string };
        live: { configured: boolean; api_key_masked: string };
        error: string;
    };
    execution_mode: "off" | "paper" | "live";
    live_trading_enabled: boolean;
    allow_short_selling: boolean;
    paper_trade_amount_usd: number | null;
    live_trade_amount_usd: number | null;
    max_position_usd: number | null;
    max_total_exposure_usd: number | null;
    order_type: string;
    limit_slippage_pct: number;
    daily_loss_limit_usd: number | null;
    max_consecutive_losses: number | null;
    account: Record<string, unknown> | null;
};

type UnexecutedTrade = {
    id: number;
    symbol: string;
    action: string;
    leverage: string;
    entry_price: number;
    recommended_at: string;
    request_id: string;
};

type OrphanOrder = {
    id: number;
    symbol: string;
    side: string;
    status: string | null;
    trading_mode: string;
    alpaca_order_id: string | null;
    created_at: string | null;
};

type RemoteSnapshotSecretsStatus = {
    available: boolean;
    configured: boolean;
    has_bot_token: boolean;
    has_chat_id: boolean;
    has_authorized_user_id: boolean;
    bot_token_masked: string;
    chat_id_masked: string;
    authorized_user_id_masked: string;
    error: string;
};

export default function AdminPage() {
    const router = useRouter();
    const [config, setConfig] = useState<AppConfig>(EMPTY_CONFIG);
    const [savedConfig, setSavedConfig] = useState<AppConfig>(EMPTY_CONFIG);
    const [isSaving, setIsSaving] = useState(false);
    const [status, setStatus] = useState<string>("");
    const [unexecutedTrades, setUnexecutedTrades] = useState<UnexecutedTrade[]>([]);
    const [deletingId, setDeletingId] = useState<number | null>(null);
    const [deleteError, setDeleteError] = useState<string>("");
    const [showDirtyModal, setShowDirtyModal] = useState(false);
    const [pendingNav, setPendingNav] = useState<string | null>(null);
    const [showResetModal, setShowResetModal] = useState(false);
    const [showRemoteSnapshotSetupModal, setShowRemoteSnapshotSetupModal] = useState(false);
    const [selectedSection, setSelectedSection] = useState<string>("overview");
    const [remoteSecrets, setRemoteSecrets] = useState<RemoteSnapshotSecretsStatus>({
        available: false,
        configured: false,
        has_bot_token: false,
        has_chat_id: false,
        has_authorized_user_id: false,
        bot_token_masked: "",
        chat_id_masked: "",
        authorized_user_id_masked: "",
        error: "",
    });
    const [telegramBotToken, setTelegramBotToken] = useState("");
    const [telegramChatId, setTelegramChatId] = useState("");
    const [telegramAuthorizedUserId, setTelegramAuthorizedUserId] = useState("");
    const [isSavingSecrets, setIsSavingSecrets] = useState(false);
    const [isVerifyingSecrets, setIsVerifyingSecrets] = useState(false);
    const [secretStatus, setSecretStatus] = useState<string>("");
    const [isSendingSnapshotNow, setIsSendingSnapshotNow] = useState(false);
    const [sendSnapshotStatus, setSendSnapshotStatus] = useState<string>("");
    const [resetConfirmText, setResetConfirmText] = useState("");
    const [isResetting, setIsResetting] = useState(false);
    const [resetStatus, setResetStatus] = useState<{ ok: boolean; message: string } | null>(null);
    const [isPulling, setIsPulling] = useState(false);
    const [pullStatus, setPullStatus] = useState<{ ok: boolean; message: string } | null>(null);
    const [isAdvancedMode, setIsAdvancedMode] = useState(false);
    const [showBasicModeModal, setShowBasicModeModal] = useState(false);
    const [priceHistoryStatus, setPriceHistoryStatus] = useState<{
        symbols: Record<string, { rows: number; earliest_date: string | null; latest_date: string | null; ready: boolean }>;
        total_rows: number;
        all_ready: boolean;
    } | null>(null);
    const { timeZone, storedRaw, setTimeZone } = useTimezone();

    const [alpacaStatus, setAlpacaStatus] = useState<AlpacaStatus | null>(null);
    const [alpacaSecretForm, setAlpacaSecretForm] = useState<{ api_key: string; secret_key: string; trading_mode: "paper" | "live" }>({ api_key: "", secret_key: "", trading_mode: "paper" });
    const [alpacaSecretStatus, setAlpacaSecretStatus] = useState<string>("");
    const [isSavingAlpacaSecrets, setIsSavingAlpacaSecrets] = useState(false);
    const [isTestingAlpacaConnection, setIsTestingAlpacaConnection] = useState(false);
    const [alpacaTestResult, setAlpacaTestResult] = useState<{ ok: boolean; message: string } | null>(null);
    const [showLiveConfirmModal, setShowLiveConfirmModal] = useState(false);
    const [showCustomRiskModal, setShowCustomRiskModal] = useState(false);
    const [liveConfirmText, setLiveConfirmText] = useState("");
    const [isEnablingLive, setIsEnablingLive] = useState(false);
    const [alpacaAccountConfigurations, setAlpacaAccountConfigurations] = useState<Record<string, unknown> | null>(null);
    const [orphanOrders, setOrphanOrders] = useState<OrphanOrder[]>([]);

    const isDirty = useMemo(
        () => JSON.stringify(config) !== JSON.stringify(savedConfig),
        [config, savedConfig]
    );

    const trackedSet = useMemo(() => new Set(config.tracked_symbols), [config.tracked_symbols]);
    const enabledFeeds = useMemo(() => new Set(config.enabled_rss_feeds), [config.enabled_rss_feeds]);
    // Always show all saved custom symbols plus one empty slot for the next entry.
    const customSymbolSlots = [...config.custom_symbols, ""];
    const customFeedSlots = Array.from({ length: config.max_custom_rss_feeds }, (_, index) => {
        const url = config.custom_rss_feeds[index] ?? "";
        return {
            url,
            label: url ? (config.custom_rss_feed_labels[url] ?? "") : "",
        };
    });
    const depthOptions: Array<{
        key: AppConfig["rss_article_detail_mode"];
        label: string;
        tagline: string;
        pipeline: string;
    }> = [
            {
                key: "light",
                label: "Light",
                tagline: "Fast single-model run",
                pipeline: "One model handles both entity mapping and reasoning — fastest turnaround.",
            },
            {
                key: "normal",
                label: "Normal",
                tagline: "Balanced, configurable",
                pipeline: "Optionally split entity mapping and reasoning across two models. Falls back to single-model if only one is configured.",
            },
            {
                key: "detailed",
                label: "Detailed",
                tagline: "Full two-model pipeline",
                pipeline: "Always runs Stage 1 entity mapping then Stage 2 reasoning. Requires both models to be set.",
            },
        ];
    const sectionOptions = [
        { value: "overview", label: "Overview", description: "Risk profile, depth, models, and pipeline posture." },
        { value: "trading", label: "Trading + Execution", description: "Guardrails and Alpaca routing." },
        { value: "symbols", label: "Symbols + RSS", description: "Tracked symbols, custom names, and feed sources." },
        { value: "system", label: "System / Telegram", description: "Auto-run, snapshots, remote control, and price-history status." },
    ];
    const riskOptions: Array<{
        key: string;
        label: string;
        tagline: string;
        description: string;
        maxLeverage: string;
        color: string;
    }> = [
            {
                key: "conservative",
                label: "Conservative",
                tagline: "Inverse ETFs, tighter thresholds",
                description: "Bullish signals buy at 1x. Bearish signals use inverse ETFs. Higher entry threshold (0.42), narrower stop-loss (2%), tight take-profit (3%). Fewer trades, smaller moves.",
                maxLeverage: "1x, inverse ETFs",
                color: "blue",
            },
            {
                key: "standard",
                label: "Standard",
                tagline: "Controlled 2x at high confidence",
                description: "Entry threshold 0.42, stop-loss 2%, take-profit 3%. Signals decay after 3h. 2x leverage when confidence > 75%, otherwise 1x.",
                maxLeverage: "2x conditional",
                color: "teal",
            },
            {
                key: "crazy",
                label: "Crazy",
                tagline: "3x always, lower gates",
                description: "Entry threshold dropped to 0.35, stop-loss widened to 2.5%, take-profit raised to 5%. Signals last longer (4h decay). Low-conviction entries allowed, materiality gate relaxed, re-entry cooldown halved to 60 min. Vol-sizer bumps conviction scalars by 20%.",
                maxLeverage: "3x always, wider stops",
                color: "rose",
            },
            {
                key: "custom",
                label: "Custom",
                tagline: "Per-parameter overrides",
                description: "Unlocks all strategy guardrails in the Custom Risk modal — entry threshold, stop/take-profit, materiality gate, re-entry cooldown, same-day exit edge. Starts from standard defaults.",
                maxLeverage: "User-defined overrides",
                color: "amber",
            },
        ];
    const handleSelectRiskProfile = useCallback((profile: string) => {
        setConfig((current) => ({ ...current, risk_profile: profile }));
        if (profile === "custom") {
            setShowCustomRiskModal(true);
        }
    }, []);

    const hasAdvancedCustomizations = useMemo(() => {
        const d = BASIC_MODE_DEFAULTS;
        return (
            config.max_posts !== d.max_posts ||
            config.lookback_days !== d.lookback_days ||
            config.data_ingestion_interval_seconds !== d.data_ingestion_interval_seconds ||
            config.snapshot_retention_limit !== d.snapshot_retention_limit ||
            config.ollama_parallel_slots !== d.ollama_parallel_slots ||
            config.inference_backend !== d.inference_backend ||
            config.red_team_enabled !== d.red_team_enabled ||
            config.allow_extended_hours_trading !== d.allow_extended_hours_trading ||
            config.hold_overnight !== d.hold_overnight ||
            config.trail_on_window_expiry !== d.trail_on_window_expiry ||
            config.reentry_cooldown_minutes !== d.reentry_cooldown_minutes ||
            config.min_same_day_exit_edge_pct !== d.min_same_day_exit_edge_pct ||
            config.vol_sizing_portfolio_cap_usd !== d.vol_sizing_portfolio_cap_usd ||
            config.paper_trade_amount !== d.paper_trade_amount ||
            config.entry_threshold !== d.entry_threshold ||
            config.stop_loss_pct !== d.stop_loss_pct ||
            config.take_profit_pct !== d.take_profit_pct ||
            config.materiality_min_posts_delta !== d.materiality_min_posts_delta ||
            config.materiality_min_sentiment_delta !== d.materiality_min_sentiment_delta ||
            config.remote_snapshot_mode !== d.remote_snapshot_mode ||
            config.remote_snapshot_interval_minutes !== d.remote_snapshot_interval_minutes ||
            config.remote_snapshot_send_on_position_change !== d.remote_snapshot_send_on_position_change ||
            config.remote_snapshot_include_closed_trades !== d.remote_snapshot_include_closed_trades ||
            config.remote_snapshot_max_recommendations !== d.remote_snapshot_max_recommendations ||
            (config.custom_rss_feeds?.length ?? 0) > 0 ||
            Object.keys(config.symbol_prompt_overrides ?? {}).length > 0
        );
    }, [config]);

    const advancedOnlySections = new Set(["trading-logic", "rss", "prompts", "executions", "price-history"]);
    const visibleSectionOptions = sectionOptions;

    const handleSwitchToBasic = () => {
        if (hasAdvancedCustomizations) {
            setShowBasicModeModal(true);
        } else {
            setIsAdvancedMode(false);
            localStorage.setItem("adminMode", "basic");
        }
    };

    const handleSwitchToAdvanced = () => {
        setIsAdvancedMode(true);
        localStorage.setItem("adminMode", "advanced");
    };

    const confirmSwitchToBasic = () => {
        setConfig((current) => ({ ...current, ...BASIC_MODE_DEFAULTS }));
        setIsAdvancedMode(false);
        localStorage.setItem("adminMode", "basic");
        setShowBasicModeModal(false);
    };

    const fetchUnexecuted = useCallback(async () => {
        const res = await fetch("/api/pnl", { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        const trades: UnexecutedTrade[] = (data.trades ?? [])
            .filter((t: any) => !!t.actual_execution)
            .map((t: any) => ({
                id: t.id,
                symbol: t.symbol,
                action: t.action,
                leverage: t.leverage,
                entry_price: t.entry_price,
                recommended_at: t.recommended_at,
                request_id: t.request_id,
            }));
        setUnexecutedTrades(trades);
    }, []);

    const fetchPriceHistoryStatus = useCallback(async () => {
        try {
            const res = await fetch("/api/admin/price-history/status", { cache: "no-store" });
            if (res.ok) setPriceHistoryStatus(await res.json());
        } catch { /* silent */ }
    }, []);

    const fetchRemoteSnapshotSecrets = useCallback(async () => {
        try {
            const res = await fetch("/api/admin/remote-snapshot-secrets", { cache: "no-store" });
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) {
                setRemoteSecrets((current) => ({
                    ...current,
                    available: false,
                    configured: false,
                    error: payload?.error || "Failed to load secret status",
                }));
                return;
            }
            setRemoteSecrets({
                available: !!payload.available,
                configured: !!payload.configured,
                has_bot_token: !!payload.has_bot_token,
                has_chat_id: !!payload.has_chat_id,
                has_authorized_user_id: !!payload.has_authorized_user_id,
                bot_token_masked: String(payload.bot_token_masked || ""),
                chat_id_masked: String(payload.chat_id_masked || ""),
                authorized_user_id_masked: String(payload.authorized_user_id_masked || ""),
                error: String(payload.error || ""),
            });
        } catch {
            setRemoteSecrets((current) => ({
                ...current,
                available: false,
                configured: false,
                error: "Failed to load secret status",
            }));
        }
    }, []);

    const fetchAlpacaStatus = useCallback(async () => {
        try {
            const res = await fetch("/api/alpaca/status", { cache: "no-store" });
            if (res.ok) {
                const statusData = await res.json();
                setAlpacaStatus(statusData);
                if (statusData?.secrets?.configured) {
                    const cfgRes = await fetch("/api/alpaca/account/configurations", { cache: "no-store" });
                    if (cfgRes.ok) setAlpacaAccountConfigurations(await cfgRes.json());
                }
            }
        } catch { /* silent */ }
    }, []);

    const fetchOrphanOrders = useCallback(async () => {
        try {
            const res = await fetch("/api/alpaca/orphans", { cache: "no-store" });
            if (res.ok) setOrphanOrders(await res.json());
        } catch { /* silent */ }
    }, []);

    const acknowledgeOrphan = useCallback(async (id: number) => {
        await fetch(`/api/alpaca/orphans/${id}/acknowledge`, { method: "POST" });
        setOrphanOrders((prev) => prev.filter((o) => o.id !== id));
    }, []);

    useEffect(() => {
        if (localStorage.getItem("adminMode") === "advanced") setIsAdvancedMode(true);
    }, []);

    useEffect(() => {
        const load = async () => {
            const response = await fetch("/api/config", { cache: "no-store" });
            if (!response.ok) return;
            const raw = await response.json();
            const nextConfig = normalizeConfigPayload(raw);
            // ── Derive new API selection fields from legacy backend fields ──
            const legacyBackend = String(raw?.inference_backend || "ollama").trim().toLowerCase();
            const isCloudLegacy = legacyBackend === "openai";
            const legacyOllamaUrl = String(raw?.ollama_url || "").trim();
            const legacyVllmUrl = String(raw?.vllm_url || "").trim();
            const legacyOpenaiUrl = String(raw?.openai_base_url || "").trim();

            const derivedApiMode: "cloud" | "local" = isCloudLegacy ? "cloud" : "local";

            // Determine provider and URL from legacy fields
            let derivedProvider = "";
            let derivedUrl = "";

            if (isCloudLegacy) {
                derivedUrl = legacyOpenaiUrl || "https://api.openai.com/v1";
                // Guess provider from URL - properly parse URL to avoid substring matching issues
                derivedProvider = deriveProviderFromUrl(derivedUrl);
            } else {
                if (legacyBackend === "ollama") {
                    derivedProvider = "ollama";
                    derivedUrl = legacyOllamaUrl || "http://localhost:11434";
                } else {
                    // vllm or any local backend
                    derivedProvider = "vllm";
                    derivedUrl = legacyVllmUrl || "http://localhost:8000";
                }
            }

            const finalConfig: AppConfig = {
                ...nextConfig,
                api_mode: derivedApiMode,
                cloud_provider: isCloudLegacy ? derivedProvider : nextConfig.cloud_provider,
                local_provider: isCloudLegacy ? nextConfig.local_provider : derivedProvider,
                api_url: derivedUrl,
                user_edited_url: derivedProvider === "custom",
            };
            setConfig(finalConfig);
            setSavedConfig(finalConfig);
            setTimeZone(finalConfig.display_timezone || "");
        };
        void load();
        void fetchUnexecuted();
        void fetchPriceHistoryStatus();
        void fetchRemoteSnapshotSecrets();
        void fetchAlpacaStatus();
        void fetchOrphanOrders();
    }, [fetchUnexecuted, fetchPriceHistoryStatus, fetchRemoteSnapshotSecrets, fetchAlpacaStatus, fetchOrphanOrders]);

    useEffect(() => {
        if (!isDirty) return;
        const handler = (e: BeforeUnloadEvent) => {
            e.preventDefault();
            e.returnValue = "";
        };
        window.addEventListener("beforeunload", handler);
        return () => window.removeEventListener("beforeunload", handler);
    }, [isDirty]);

    const toggleRemoteSnapshotEnabled = (enabled: boolean) => {
        if (enabled && !remoteSecrets.configured) {
            setSecretStatus("");
            setShowRemoteSnapshotSetupModal(true);
            return;
        }
        setConfig((current) => ({ ...current, remote_snapshot_enabled: enabled }));
    };

    const toggleTelegramRemoteControlEnabled = (enabled: boolean) => {
        if (enabled && !remoteSecrets.configured) {
            setSecretStatus("");
            setShowRemoteSnapshotSetupModal(true);
            return;
        }
        setConfig((current) => ({ ...current, telegram_remote_control_enabled: enabled }));
    };

    const verifyRemoteSnapshotSecrets = useCallback(async (savedThisRun = false) => {
        setIsVerifyingSecrets(true);
        if (!savedThisRun) {
            setSecretStatus("");
        }
        try {
            const response = await fetch("/api/admin/remote-snapshot-secrets/verify", {
                method: "POST",
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                setSecretStatus(payload?.detail || payload?.error || "Telegram verification failed");
                return;
            }
            setSecretStatus(String(payload?.message || "Telegram remote control verified."));
        } catch {
            setSecretStatus("Telegram verification failed");
        } finally {
            setIsVerifyingSecrets(false);
        }
    }, []);

    const saveRemoteSnapshotSecrets = async () => {
        setIsSavingSecrets(true);
        setSecretStatus("");
        try {
            const response = await fetch("/api/admin/remote-snapshot-secrets", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    bot_token: telegramBotToken,
                    chat_id: telegramChatId,
                    authorized_user_id: telegramAuthorizedUserId,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                setSecretStatus(payload?.detail || payload?.error || "Failed to save secrets");
                return;
            }
            setRemoteSecrets({
                available: !!payload.available,
                configured: !!payload.configured,
                has_bot_token: !!payload.has_bot_token,
                has_chat_id: !!payload.has_chat_id,
                has_authorized_user_id: !!payload.has_authorized_user_id,
                bot_token_masked: String(payload.bot_token_masked || ""),
                chat_id_masked: String(payload.chat_id_masked || ""),
                authorized_user_id_masked: String(payload.authorized_user_id_masked || ""),
                error: String(payload.error || ""),
            });
            setTelegramBotToken("");
            setTelegramChatId("");
            setTelegramAuthorizedUserId("");
            if (payload?.test_delivery_started) {
                setSecretStatus("Credentials saved. A test snapshot is being sent now.");
            } else if (payload?.test_delivery_note) {
                setSecretStatus(`Credentials saved. ${payload.test_delivery_note}`);
            } else {
                setSecretStatus("Credentials saved to OS keychain.");
            }
            await verifyRemoteSnapshotSecrets(true);
        } catch {
            setSecretStatus("Failed to save secrets");
        } finally {
            setIsSavingSecrets(false);
        }
    };

    const clearRemoteSnapshotSecrets = async () => {
        setIsSavingSecrets(true);
        setSecretStatus("");
        try {
            const response = await fetch("/api/admin/remote-snapshot-secrets", {
                method: "DELETE",
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                setSecretStatus(payload?.error || "Failed to clear secrets");
                return;
            }
            setRemoteSecrets({
                available: !!payload.available,
                configured: !!payload.configured,
                has_bot_token: !!payload.has_bot_token,
                has_chat_id: !!payload.has_chat_id,
                has_authorized_user_id: !!payload.has_authorized_user_id,
                bot_token_masked: String(payload.bot_token_masked || ""),
                chat_id_masked: String(payload.chat_id_masked || ""),
                authorized_user_id_masked: String(payload.authorized_user_id_masked || ""),
                error: String(payload.error || ""),
            });
            setTelegramBotToken("");
            setTelegramChatId("");
            setTelegramAuthorizedUserId("");
            setSecretStatus("Secrets cleared from OS keychain");
        } catch {
            setSecretStatus("Failed to clear secrets");
        } finally {
            setIsSavingSecrets(false);
        }
    };

    const sendRemoteSnapshotNow = async () => {
        setIsSendingSnapshotNow(true);
        setSendSnapshotStatus("");
        try {
            const response = await fetch("/api/admin/remote-snapshot-send", {
                method: "POST",
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                setSendSnapshotStatus(payload?.error || "Failed to queue snapshot send");
                return;
            }
            setSendSnapshotStatus(payload?.message || "Remote snapshot send has been queued.");
        } catch {
            setSendSnapshotStatus("Failed to queue snapshot send");
        } finally {
            setIsSendingSnapshotNow(false);
        }
    };

    const toggleTrackedSymbol = (symbol: string) => {
        setConfig((current) => {
            const next = new Set(current.tracked_symbols);
            if (next.has(symbol)) {
                next.delete(symbol);
            } else {
                next.add(symbol);
            }
            return {
                ...current,
                tracked_symbols: Array.from(next),
            };
        });
    };

    const updateCustomSymbol = (index: number, value: string) => {
        const nextValue = normalizeSymbolInput(value);
        setConfig((current) => {
            const nextCustomSymbols = [...current.custom_symbols];
            const previousValue = nextCustomSymbols[index] ?? "";
            const nextAliases = { ...current.symbol_company_aliases };
            const previousAlias = previousValue ? (nextAliases[previousValue] ?? "") : "";
            if (nextValue) {
                nextCustomSymbols[index] = nextValue;
            } else {
                nextCustomSymbols.splice(index, 1);
            }
            const filteredCustomSymbols = nextCustomSymbols.filter(Boolean);
            if (previousValue && previousValue !== nextValue) {
                delete nextAliases[previousValue];
            }
            if (nextValue && previousAlias) {
                nextAliases[nextValue] = previousAlias;
            }
            const filteredAliases = Object.fromEntries(
                Object.entries(nextAliases).filter(([symbol, alias]) => filteredCustomSymbols.includes(symbol) && !!alias.trim())
            );
            const nextTracked = current.tracked_symbols
                .filter((symbol) => symbol !== previousValue)
                .filter((symbol) => current.default_symbols.includes(symbol) || filteredCustomSymbols.includes(symbol));
            if (nextValue && !nextTracked.includes(nextValue)) {
                nextTracked.push(nextValue);
            }
            return {
                ...current,
                custom_symbols: filteredCustomSymbols,
                symbol_company_aliases: filteredAliases,
                tracked_symbols: nextTracked,
            };
        });
    };

    const updateCustomSymbolAlias = (symbol: string, value: string) => {
        const trimmed = value.trimStart().slice(0, 120);
        setConfig((current) => {
            if (!symbol) return current;
            const nextAliases = { ...current.symbol_company_aliases };
            if (trimmed) {
                nextAliases[symbol] = trimmed;
            } else {
                delete nextAliases[symbol];
            }
            return {
                ...current,
                symbol_company_aliases: nextAliases,
            };
        });
    };

    const toggleCustomSymbolTracked = (symbol: string) => {
        if (!symbol) return;
        toggleTrackedSymbol(symbol);
    };

    const toggleFeed = (url: string) => {
        setConfig((current) => {
            const next = new Set(current.enabled_rss_feeds);
            if (next.has(url)) {
                next.delete(url);
            } else {
                next.add(url);
            }
            return {
                ...current,
                enabled_rss_feeds: Array.from(next),
            };
        });
    };

    const updateCustomFeed = (index: number, value: string) => {
        const nextValue = normalizeFeedUrl(value);
        setConfig((current) => {
            const nextCustomFeeds = [...current.custom_rss_feeds];
            const previousValue = nextCustomFeeds[index] ?? "";
            const nextFeedLabels = { ...current.custom_rss_feed_labels };
            const previousLabel = previousValue ? (nextFeedLabels[previousValue] ?? "") : "";
            if (nextValue) {
                nextCustomFeeds[index] = nextValue;
            } else {
                nextCustomFeeds.splice(index, 1);
            }
            const filteredCustomFeeds = nextCustomFeeds.filter(Boolean).slice(0, current.max_custom_rss_feeds);
            if (previousValue && previousValue !== nextValue) {
                delete nextFeedLabels[previousValue];
            }
            if (nextValue && previousLabel) {
                nextFeedLabels[nextValue] = previousLabel;
            }
            const filteredCustomFeedLabels = Object.fromEntries(
                Object.entries(nextFeedLabels).filter(([url, label]) => filteredCustomFeeds.includes(url) && !!label.trim())
            );
            const nextEnabled = current.enabled_rss_feeds
                .filter((url) => url !== previousValue)
                .filter((url) => current.default_rss_feeds.some((feed) => feed.url === url) || filteredCustomFeeds.includes(url));
            return {
                ...current,
                custom_rss_feeds: filteredCustomFeeds,
                custom_rss_feed_labels: filteredCustomFeedLabels,
                enabled_rss_feeds: nextEnabled,
            };
        });
    };

    const updateCustomFeedLabel = (url: string, value: string) => {
        const trimmed = value.trimStart().slice(0, 60);
        setConfig((current) => {
            if (!url) return current;
            const nextLabels = { ...current.custom_rss_feed_labels };
            if (trimmed) {
                nextLabels[url] = trimmed;
            } else {
                delete nextLabels[url];
            }
            return {
                ...current,
                custom_rss_feed_labels: nextLabels,
            };
        });
    };

    const toggleCustomFeedTracked = (url: string) => {
        if (!url) return;
        toggleFeed(url);
    };

    const updateArticleLimit = (key: "light" | "normal" | "detailed", value: string) => {
        setConfig((current) => ({
            ...current,
            rss_article_limits: {
                ...current.rss_article_limits,
                [key]: normalizeArticleLimit(value, current.rss_article_limits[key]),
            },
        }));
    };

    const updatePromptOverride = (symbol: string, value: string) => {
        setConfig((current) => ({
            ...current,
            symbol_prompt_overrides: {
                ...current.symbol_prompt_overrides,
                [symbol]: value,
            },
        }));
    };

    const deleteTrade = async (id: number) => {
        setDeletingId(id);
        setDeleteError("");
        try {
            const res = await fetch(`/api/trades/${id}/execution`, { method: "DELETE" });
            if (!res.ok) {
                const payload = await res.json().catch(() => ({}));
                setDeleteError(payload?.error || "Remove failed");
                return;
            }
            await fetchUnexecuted();
        } finally {
            setDeletingId(null);
        }
    };

    const save = async () => {
        if (config.tracked_symbols.length === 0) {
            setStatus("Select at least one symbol");
            return;
        }
        if (config.enabled_rss_feeds.length === 0) {
            setStatus("Select at least one RSS feed");
            return;
        }

        setIsSaving(true);
        setStatus("");

        // ── Map new API selection fields to legacy backend fields ──
        const apiUrl = (config.api_url ?? "").trim();
        const payload = {
            ...config,
            inference_backend: config.api_mode === "cloud"
                ? "openai"
                : config.local_provider === "ollama" ? "ollama" : "vllm",
            openai_base_url: config.api_mode === "cloud" ? apiUrl : config.openai_base_url,
            ollama_url: config.api_mode === "local" && config.local_provider === "ollama" ? apiUrl : config.ollama_url,
            vllm_url: config.api_mode === "local" && config.local_provider !== "ollama" ? apiUrl : config.vllm_url,
        };

        try {
            const response = await fetch("/api/config", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!response.ok) {
                throw new Error("Failed to save config");
            }
            const committed = normalizeConfigPayload(await response.json());
            setConfig(committed);
            setSavedConfig(committed);
            const notices = Array.isArray(committed.notices) ? committed.notices.filter(Boolean) : [];
            setStatus(notices.length > 0 ? `Saved. ${notices.join(" ")}` : "Saved");
        } catch {
            setStatus("Save failed");
        } finally {
            setIsSaving(false);
        }
    };

    const selectSection = (sectionId: string) => {
        if (!sectionId) return;
        setSelectedSection(sectionId);
        window.scrollTo({ top: 0, behavior: "smooth" });
    };

    const handleNavigate = (target: string) => {
        if (isDirty) {
            setPendingNav(target);
            setShowDirtyModal(true);
        } else {
            router.push(target);
        }
    };

    const handlePullPriceHistory = async () => {
        setIsPulling(true);
        setPullStatus(null);
        try {
            const res = await fetch("/api/admin/price-history/pull", { method: "POST" });
            const data = await res.json();
            if (!res.ok) {
                setPullStatus({ ok: false, message: data?.error || "Pull failed" });
                return;
            }
            const added = data.total_rows_added ?? 0;
            const limited = data.rate_limited ? " Rate limit hit — run again later to resume." : "";
            setPullStatus({ ok: !data.rate_limited, message: `${added} rows added.${limited}` });
            await fetchPriceHistoryStatus();
        } catch {
            setPullStatus({ ok: false, message: "Network error — pull may not have completed" });
        } finally {
            setIsPulling(false);
        }
    };

    const handleResetDatabase = async () => {
        setIsResetting(true);
        setResetStatus(null);
        try {
            const res = await fetch("/api/admin/reset", { method: "POST" });
            const data = await res.json();
            if (!res.ok) {
                setResetStatus({ ok: false, message: data?.error || "Reset failed" });
                return;
            }
            setResetStatus({ ok: true, message: `Cleared ${data.total_rows_deleted} rows across ${Object.keys(data.deleted).length} tables.` });
            setResetConfirmText("");
            const cfgRes = await fetch("/api/config", { cache: "no-store" });
            if (cfgRes.ok) {
                const nextConfig = normalizeConfigPayload(await cfgRes.json());
                setConfig(nextConfig);
                setSavedConfig(nextConfig);
            }
        } catch {
            setResetStatus({ ok: false, message: "Network error — reset may not have completed" });
        } finally {
            setIsResetting(false);
        }
    };

    const saveAlpacaSecrets = async () => {
        setIsSavingAlpacaSecrets(true);
        setAlpacaSecretStatus("");
        try {
            const response = await fetch("/api/alpaca/secrets", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(alpacaSecretForm),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                setAlpacaSecretStatus(payload?.error || "Failed to save secrets");
                return;
            }
            setAlpacaSecretForm({ api_key: "", secret_key: "", trading_mode: "paper" });
            setAlpacaSecretStatus("Keys saved to OS keychain");
            await fetchAlpacaStatus();
        } catch {
            setAlpacaSecretStatus("Failed to save secrets");
        } finally {
            setIsSavingAlpacaSecrets(false);
        }
    };

    const clearAlpacaSecrets = async (mode?: "paper" | "live") => {
        setIsSavingAlpacaSecrets(true);
        setAlpacaSecretStatus("");
        try {
            const qs = mode ? `?mode=${mode}` : "";
            const response = await fetch(`/api/alpaca/secrets${qs}`, { method: "DELETE" });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                setAlpacaSecretStatus(payload?.error || "Failed to clear secrets");
                return;
            }
            setAlpacaSecretStatus(mode ? `${mode} keys cleared` : "All keys cleared from OS keychain");
            await fetchAlpacaStatus();
        } catch {
            setAlpacaSecretStatus("Failed to clear secrets");
        } finally {
            setIsSavingAlpacaSecrets(false);
        }
    };

    const testAlpacaConnection = async (mode?: "paper" | "live") => {
        setIsTestingAlpacaConnection(true);
        setAlpacaTestResult(null);
        try {
            const qs = mode ? `?mode=${mode}` : "";
            const response = await fetch(`/api/alpaca/test-connection${qs}`, { method: "POST" });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                setAlpacaTestResult({ ok: false, message: payload?.error || "Connection failed" });
                return;
            }
            const testedMode = payload?.mode === "live" ? "live" : "paper";
            const equity = payload?.account?.equity ? ` — equity $${Number(payload.account.equity).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "";
            setAlpacaTestResult({ ok: true, message: `Connected (${testedMode}${equity})` });
            await fetchAlpacaStatus();
        } catch {
            setAlpacaTestResult({ ok: false, message: "Network error" });
        } finally {
            setIsTestingAlpacaConnection(false);
        }
    };

    const setAlpacaExecutionMode = async (mode: "off" | "paper" | "live") => {
        setIsEnablingLive(true);
        try {
            const response = await fetch("/api/alpaca/settings", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ alpaca_execution_mode: mode }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                setAlpacaSecretStatus(payload?.error || "Failed to update Alpaca routing");
                return;
            }
            setConfig((current) => ({ ...current, alpaca_execution_mode: mode, alpaca_live_trading_enabled: mode === "live" }));
            setSavedConfig((current) => ({ ...current, alpaca_execution_mode: mode, alpaca_live_trading_enabled: mode === "live" }));
            setShowLiveConfirmModal(false);
            setLiveConfirmText("");
            await fetchAlpacaStatus();
        } catch {
            setAlpacaSecretStatus("Failed to update Alpaca routing");
        } finally {
            setIsEnablingLive(false);
        }
    };

    const handleDiscardAndLeave = () => {
        setShowDirtyModal(false);
        router.push(pendingNav!);
    };

    const handleSaveAndLeave = async () => {
        await save();
        setShowDirtyModal(false);
        router.push(pendingNav!);
    };

    const handleSaveAndExit = async () => {
        await save();
        router.push("/");
    };

    return (
        <main className="min-h-screen bg-slate-950 text-slate-100 px-6 py-10">
            <div className="max-w-7xl mx-auto flex gap-8">
                {/* Sidebar nav */}
                <nav className="sticky top-10 self-start w-56 pt-8 hidden md:block">
                    <div className="flex items-center gap-2 mb-6">
                        <span className="text-xl font-bold text-white">⚙️</span>
                        <span className="text-lg font-semibold text-slate-100">Admin</span>
                    </div>
                    <div className="space-y-1">
                        {visibleSectionOptions.map((opt) => (
                            <button
                                key={opt.value}
                                type="button"
                                onClick={() => selectSection(opt.value)}
                                className={`block w-full text-left py-3 px-3 rounded-xl text-sm transition-colors ${selectedSection === opt.value
                                    ? "bg-slate-800 text-white shadow-inner"
                                    : "text-slate-300 hover:bg-slate-800/80 hover:text-white"
                                    }`}
                                aria-current={selectedSection === opt.value ? "page" : undefined}
                            >
                                <div className="font-medium">{opt.label}</div>
                                <div className="text-[11px] text-slate-500 mt-0.5">{opt.description}</div>
                            </button>
                        ))}
                    </div>
                    <div className="mt-6 space-y-2">
                        {!isAdvancedMode ? (
                            <button
                                type="button"
                                onClick={handleSwitchToAdvanced}
                                className="text-xs text-slate-500 hover:text-slate-300"
                            >
                                Switch to Advanced
                            </button>
                        ) : (
                            <>
                                <p className="text-xs text-slate-400">✓ Advanced mode</p>
                                <button
                                    type="button"
                                    onClick={handleSwitchToBasic}
                                    className="text-xs text-slate-500 hover:text-slate-300"
                                >
                                    {hasAdvancedCustomizations ? "Reset to Basic" : "Switch to Basic"}
                                </button>
                            </>
                        )}
                    </div>
                </nav>

                {/* Main content */}
                <div className="flex-1 min-w-0">
                    {/* Mobile nav */}
                    <div className="md:hidden flex gap-2 mb-6 overflow-x-auto pb-2">
                        {visibleSectionOptions.map((opt) => (
                            <button
                                key={opt.value}
                                type="button"
                                onClick={() => selectSection(opt.value)}
                                className={`flex-shrink-0 rounded-lg border px-3 py-1.5 text-xs transition-colors ${selectedSection === opt.value
                                    ? "border-blue-500 bg-blue-500/10 text-blue-200"
                                    : "border-slate-800 bg-slate-900 text-slate-300 hover:bg-slate-800"
                                    }`}
                            >
                                {opt.label}
                            </button>
                        ))}
                    </div>

                    <div className="mb-6 flex items-center justify-between">
                        <div>
                            <h1 className="text-3xl font-bold text-white">Configuration</h1>
                            <p className="text-sm text-slate-400 mt-1">
                                Tweak the knobs below to control how the model thinks.
                                Changes are saved instantly via the floating bar.
                            </p>
                        </div>
                        <div className="hidden md:flex items-center gap-3">
                            {isAdvancedMode && (
                                <button
                                    type="button"
                                    onClick={() => {
                                        if (hasAdvancedCustomizations) {
                                            setShowBasicModeModal(true);
                                        } else {
                                            setIsAdvancedMode(false);
                                            localStorage.setItem("adminMode", "basic");
                                        }
                                    }}
                                    className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors ${hasAdvancedCustomizations
                                        ? "border-amber-700 text-amber-300 hover:bg-amber-950/30"
                                        : "border-slate-700 text-slate-400 hover:text-white"
                                        }`}
                                >
                                    {hasAdvancedCustomizations ? "⚠ Has advanced settings" : "Switch to Basic"}
                                </button>
                            )}
                            {!isAdvancedMode && (
                                <button
                                    type="button"
                                    onClick={handleSwitchToAdvanced}
                                    className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:bg-slate-800 transition-colors"
                                >
                                    Switch to Advanced
                                </button>
                            )}
                        </div>
                    </div>

                    {/* Extracted sections */}
                    <div className="space-y-6">
                        {selectedSection === "overview" && (
                            <OverviewSection
                                config={config} setConfig={setConfig}
                                isAdvancedMode={isAdvancedMode}
                                riskOptions={riskOptions} depthOptions={depthOptions}
                                onSelectRiskProfile={handleSelectRiskProfile}
                            />
                        )}
                        {selectedSection === "trading" && (
                            <>
                                <TradingBehaviorSection
                                    config={config}
                                    setConfig={setConfig}
                                />
                                {isAdvancedMode && config.risk_profile === "custom" && (
                                    <TradingLogicSection
                                        openCustomRiskModal={() => setShowCustomRiskModal(true)}
                                    />
                                )}
                                <BrokerageSection
                                    config={config} setConfig={setConfig}
                                    isAdvancedMode={isAdvancedMode}
                                    alpacaStatus={alpacaStatus}
                                    alpacaAccountConfigurations={alpacaAccountConfigurations}
                                    alpacaSecretForm={alpacaSecretForm}
                                    setAlpacaSecretForm={setAlpacaSecretForm}
                                    alpacaSecretStatus={alpacaSecretStatus}
                                    alpacaTestResult={alpacaTestResult}
                                    isSavingAlpacaSecrets={isSavingAlpacaSecrets}
                                    isTestingAlpacaConnection={isTestingAlpacaConnection}
                                    saveAlpacaSecrets={saveAlpacaSecrets}
                                    clearAlpacaSecrets={clearAlpacaSecrets}
                                    testAlpacaConnection={testAlpacaConnection}
                                    openLiveConfirmModal={() => { setShowLiveConfirmModal(true); setLiveConfirmText(""); }}
                                    setAlpacaExecutionMode={setAlpacaExecutionMode}
                                    orphanOrders={orphanOrders}
                                    onAcknowledgeOrphan={acknowledgeOrphan}
                                />
                            </>
                        )}
                        {selectedSection === "symbols" && (
                            <>
                                <SymbolsSection
                                    config={config} setConfig={setConfig}
                                    trackedSet={trackedSet}
                                    customSymbolSlots={customSymbolSlots}
                                    updateCustomSymbol={updateCustomSymbol}
                                    updateCustomSymbolAlias={updateCustomSymbolAlias}
                                    toggleCustomSymbolTracked={toggleCustomSymbolTracked}
                                    toggleTrackedSymbol={toggleTrackedSymbol}
                                />
                                <RssSection
                                    config={config} setConfig={setConfig}
                                    depthOptions={depthOptions}
                                    enabledFeeds={enabledFeeds}
                                    toggleFeed={toggleFeed}
                                    customFeedSlots={customFeedSlots}
                                    updateCustomFeed={updateCustomFeed}
                                    updateCustomFeedLabel={updateCustomFeedLabel}
                                    toggleCustomFeedTracked={toggleCustomFeedTracked}
                                    updateArticleLimit={updateArticleLimit}
                                />
                                {isAdvancedMode && (
                                    <PromptOverridesSection
                                        config={config}
                                        setConfig={setConfig}
                                        updatePromptOverride={updatePromptOverride}
                                    />
                                )}
                            </>
                        )}
                        {selectedSection === "system" && (
                            <>
                                <CloudLLMSection
                                    config={config}
                                    setConfig={setConfig}
                                    isAdvancedMode={isAdvancedMode}
                                />
                                <SystemSection
                                    config={config} setConfig={setConfig}
                                    timeZone={timeZone} setTimeZone={setTimeZone}
                                    isAdvancedMode={isAdvancedMode}
                                />
                                <RemoteSnapshotSection
                                    config={config} setConfig={setConfig}
                                    isAdvancedMode={isAdvancedMode}
                                    timeZone={timeZone}
                                    sendSnapshotStatus={sendSnapshotStatus}
                                    isSendingSnapshotNow={isSendingSnapshotNow}
                                    sendSnapshotNow={sendRemoteSnapshotNow}
                                    toggleRemoteSnapshotEnabled={toggleRemoteSnapshotEnabled}
                                    toggleTelegramRemoteControlEnabled={toggleTelegramRemoteControlEnabled}
                                    remoteSecrets={remoteSecrets}
                                    setShowRemoteSnapshotSetupModal={setShowRemoteSnapshotSetupModal}
                                />
                                <PriceHistorySection
                                    isAdvancedMode={isAdvancedMode}
                                    isPulling={isPulling}
                                    pullStatus={pullStatus}
                                    priceHistoryStatus={priceHistoryStatus}
                                    handlePullPriceHistory={handlePullPriceHistory}
                                />
                                {isAdvancedMode && (
                                    <ExecutionsSection
                                        unexecutedTrades={unexecutedTrades}
                                        deletingId={deletingId}
                                        deleteError={deleteError}
                                        timeZone={timeZone}
                                        deleteTrade={deleteTrade}
                                    />
                                )}
                                <section id="danger-zone" className="scroll-mt-24 rounded-2xl border border-red-900/50 bg-red-950/20 p-5 space-y-4">
                                    <div>
                                        <h2 className="text-sm font-semibold text-red-400">Danger Zone</h2>
                                        <p className="text-xs text-slate-500 mt-1">
                                            Irreversible actions. Your config settings are not affected.
                                        </p>
                                    </div>
                                    <div className="flex items-center justify-between gap-4 rounded-xl border border-red-900/40 bg-slate-950/60 px-4 py-3">
                                        <div>
                                            <p className="text-sm font-medium text-slate-200">Reset all data</p>
                                            <p className="text-xs text-slate-500 mt-0.5">
                                                Deletes all analysis results, trade recommendations, P&L snapshots, and execution records. Config is preserved.
                                            </p>
                                        </div>
                                        <button
                                            type="button"
                                            onClick={() => { setResetStatus(null); setResetConfirmText(""); setShowResetModal(true); }}
                                            className="flex-shrink-0 rounded-lg border border-red-800 px-4 py-2 text-sm font-medium text-red-400 hover:bg-red-900/30"
                                        >
                                            Reset Database
                                        </button>
                                    </div>
                                </section>
                            </>
                        )}
                    </div>
                </div>
            </div>

            {/* Floating save / exit panel */}
            <div className="fixed bottom-6 right-6 z-40 flex flex-col items-end gap-2">
                {status && (
                    <span className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs text-slate-300 shadow">{status}</span>
                )}
                <div className="flex items-center gap-2 rounded-xl border border-slate-700 bg-slate-900/95 px-3 py-2 shadow-xl backdrop-blur-sm">
                    <button
                        type="button"
                        onClick={() => handleNavigate("/")}
                        className="rounded-lg px-3 py-1.5 text-sm text-slate-400 hover:text-white transition-colors"
                    >
                        Back
                    </button>
                    {isDirty && (
                        <button
                            type="button"
                            onClick={handleSaveAndExit}
                            disabled={isSaving}
                            className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-60 transition-colors"
                        >
                            {isSaving ? "Saving…" : "Save & Exit"}
                        </button>
                    )}
                </div>
            </div>

            {/* Modals */}
            <DirtyModal
                showDirtyModal={showDirtyModal}
                isSaving={isSaving}
                handleDiscardAndLeave={handleDiscardAndLeave}
                handleSaveAndLeave={handleSaveAndLeave}
                setShowDirtyModal={setShowDirtyModal}
            />
            <DangerZoneModal
                showResetModal={showResetModal}
                resetConfirmText={resetConfirmText}
                resetStatus={resetStatus}
                isResetting={isResetting}
                setResetConfirmText={setResetConfirmText}
                setShowResetModal={setShowResetModal}
                setResetStatus={setResetStatus}
                handleResetDatabase={handleResetDatabase}
            />
            <BasicModeModal
                showBasicModeModal={showBasicModeModal}
                confirmSwitchToBasic={confirmSwitchToBasic}
                setShowBasicModeModal={setShowBasicModeModal}
            />
            <RemoteSnapshotSetupModal
                showRemoteSnapshotSetupModal={showRemoteSnapshotSetupModal}
                telegramBotToken={telegramBotToken}
                telegramChatId={telegramChatId}
                telegramAuthorizedUserId={telegramAuthorizedUserId}
                setTelegramBotToken={setTelegramBotToken}
                setTelegramChatId={setTelegramChatId}
                setTelegramAuthorizedUserId={setTelegramAuthorizedUserId}
                saveRemoteSecrets={saveRemoteSnapshotSecrets}
                verifyRemoteSecrets={() => void verifyRemoteSnapshotSecrets(false)}
                clearRemoteSecrets={clearRemoteSnapshotSecrets}
                isSavingSecrets={isSavingSecrets}
                isVerifyingSecrets={isVerifyingSecrets}
                secretStatus={secretStatus}
                remoteSecrets={remoteSecrets}
                setShowRemoteSnapshotSetupModal={setShowRemoteSnapshotSetupModal}
            />
            <LiveConfirmModal
                showLiveConfirmModal={showLiveConfirmModal}
                liveConfirmText={liveConfirmText}
                isEnablingLive={isEnablingLive}
                setLiveConfirmText={setLiveConfirmText}
                setShowLiveConfirmModal={setShowLiveConfirmModal}
                handleSaveAndActivateLive={() => setAlpacaExecutionMode("live")}
            />
            <CustomRiskModal
                show={showCustomRiskModal}
                config={config}
                setConfig={setConfig}
                onClose={() => setShowCustomRiskModal(false)}
            />
        </main>
    );
}
