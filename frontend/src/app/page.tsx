"use client";

import Link from "next/link";
import { useState, useEffect, useRef, useCallback, useMemo, Fragment } from "react";
import { Activity, WifiOff, ArrowRight, TrendingUp, TrendingDown, Minus, Clock } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

// Types
import {
    FeedItem,
    Prices,
    AnalysisResult,
    AnalysisSnapshotItem,
    AppConfig,
    PnLSummary,
    OllamaStatus,
    Recommendation,
} from "@/lib/types/analysis";

// Constants
import {
    DEFAULT_APP_CONFIG,
    LAST_VIEWED_ANALYSIS_REQUEST_ID_KEY,
    GOLDEN_DATASET_REQUEST_ID_KEY,
    ANALYSIS_STAGES,
    SIGNAL_METRICS,
    SIGNAL_RULES,
} from "@/lib/constants/analysis";

// Utilities
import {
    clamp,
    estimateRunTiming,
    sanitizeTimingSamples,
    appendTimingSample,
    mergeTimingSamples,
} from "@/lib/utils/timing";

// Components
import SentimentTicker from "@/components/Dashboard/SentimentTicker";
import GlassCard from "@/components/Dashboard/GlassCard";
import PriceRow from "@/components/Dashboard/PriceRow";
import ArticleCard from "@/components/Dashboard/ArticleCard";
import SignalHero from "@/components/Dashboard/SignalHero";
import AnalysisStatusCard from "@/components/Dashboard/AnalysisStatusCard";
import PullHistoryCard from "@/components/Dashboard/PullHistoryCard";
import ModelComparePanel from "@/components/Dashboard/ModelComparePanel";
import DebugPanel from "@/components/Dashboard/DebugPanel";
import TradeExecutionModal from "@/components/Dashboard/TradeExecutionModal";
import ActualTradeComparisonCard from "@/components/Dashboard/ActualTradeComparisonCard";
import { useTimezone } from "@/lib/timezone";
import { useAnalysis } from "@/lib/context/AnalysisContext";
import { AppHeader } from "@/components/AppHeader";

const RECENT_ANALYSIS_TIMES_KEY = "recentAnalysisTimes";
const MAX_RECENT_ANALYSIS_TIMES = 12;

function averageTimingSeconds(samples: number[], fallbackSeconds: number) {
    const cleaned = sanitizeTimingSamples(samples, MAX_RECENT_ANALYSIS_TIMES);
    if (cleaned.length === 0) return Math.max(15, Math.round(fallbackSeconds || 82));
    return Math.max(15, Math.round(cleaned.reduce((sum, value) => sum + value, 0) / cleaned.length));
}

function loadRecentTimingSamples(): number[] {
    if (typeof window === "undefined") return [];
    try {
        const raw = localStorage.getItem(RECENT_ANALYSIS_TIMES_KEY);
        if (!raw) return [];
        return sanitizeTimingSamples(JSON.parse(raw), MAX_RECENT_ANALYSIS_TIMES);
    } catch {
        return [];
    }
}

function saveRecentTimingSamples(samples: number[]) {
    if (typeof window === "undefined") return;
    try {
        localStorage.setItem(RECENT_ANALYSIS_TIMES_KEY, JSON.stringify(
            sanitizeTimingSamples(samples, MAX_RECENT_ANALYSIS_TIMES),
        ));
    } catch {
        // best effort only
    }
}

// ─── Main Page ──────────────────────────────

export default function Home() {
    const { setTimeZone } = useTimezone();
    const [result, setResult] = useState<AnalysisResult | null>(null);
    const [config, setConfig] = useState<AppConfig>(DEFAULT_APP_CONFIG);
    const [configLoaded, setConfigLoaded] = useState(false);
    const [pnlSummary, setPnlSummary] = useState<PnLSummary | null>(null);
    const [isAnalyzing, setIsAnalyzing] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [feed, setFeed] = useState<FeedItem[]>([]);
    const [expandedIdxs, setExpandedIdxs] = useState<Set<number>>(new Set());
    const [prices, setPrices] = useState<Prices | null>(null);
    const [analysisStartedAt, setAnalysisStartedAt] = useState<number | null>(null);
    const [streamStartedAt, setStreamStartedAt] = useState<number | null>(null);
    const [elapsedSeconds, setElapsedSeconds] = useState(0);
    const [latestLogMessage, setLatestLogMessage] = useState("");
    const [backendPhaseLabel, setBackendPhaseLabel] = useState("");
    const [selectedRecommendation, setSelectedRecommendation] = useState<Recommendation | null>(null);
    const [advancedMode, setAdvancedMode] = useState(false);
    const [activeTab, setActiveTab] = useState<"signal" | "history" | "compare" | "debug">("signal");
    const [ollamaStatus, setOllamaStatus] = useState<OllamaStatus | null>(null);
    const [analysisSnapshots, setAnalysisSnapshots] = useState<AnalysisSnapshotItem[]>([]);
    const [goldenDatasetRequestId, setGoldenDatasetRequestId] = useState("");
    const [goldenBaselineResult, setGoldenBaselineResult] = useState<AnalysisResult | null>(null);
    const [comparisonResult, setComparisonResult] = useState<AnalysisResult | null>(null);
    const [comparisonBaselineResult, setComparisonBaselineResult] = useState<AnalysisResult | null>(null);
    const [comparisonLoading, setComparisonLoading] = useState(false);
    const [comparisonError, setComparisonError] = useState<string | null>(null);
    const [benchmarkResults, setBenchmarkResults] = useState<AnalysisResult[]>([]);
    const [savedComparisonBaseline, setSavedComparisonBaseline] = useState<AnalysisResult | null>(null);
    const [savedComparisonResult, setSavedComparisonResult] = useState<AnalysisResult | null>(null);
    const [savedComparisonLoading, setSavedComparisonLoading] = useState(false);
    const [savedComparisonError, setSavedComparisonError] = useState<string | null>(null);
    const [showCompletedProgressUntil, setShowCompletedProgressUntil] = useState<number | null>(null);
    const [restoringLastResult, setRestoringLastResult] = useState(true);
    const [signalLogicExpanded, setSignalLogicExpanded] = useState(false);
    const articleCounter = useRef(0);
    const autoRunStartedRef = useRef(false);
    const runAttemptedRef = useRef(false);
    const lastRunFailedRef = useRef(false);
    const analysisStartedAtRef = useRef<number | null>(null);
    const streamStartedAtRef = useRef<number | null>(null);
    const historySectionRef = useRef<HTMLDivElement | null>(null);
    const trackedSymbols = config.tracked_symbols.length > 0 ? config.tracked_symbols : DEFAULT_APP_CONFIG.tracked_symbols;
    const pricePanelSymbols = useMemo(() => {
        const openTradeSymbols = (pnlSummary?.trades ?? [])
            .filter((t) => !t.trade_close)
            .map((t) => t.symbol);
        return Array.from(new Set([...trackedSymbols, ...openTradeSymbols]));
    }, [trackedSymbols, pnlSummary]);

    // Keep stable refs for the auto-run effect
    const isAnalyzingRef = useRef(false);
    useEffect(() => { isAnalyzingRef.current = isAnalyzing; }, [isAnalyzing]);

    const handleAnalyzeRef = useRef<() => void>(() => { });

    const fetchConfig = useCallback(async () => {
        try {
            const response = await fetch("/api/config", { cache: "no-store" });
            if (!response.ok) return;
            const nextConfig = await response.json() as AppConfig;
            const backendTimes = sanitizeTimingSamples(nextConfig.recent_analysis_seconds, MAX_RECENT_ANALYSIS_TIMES);
            const localTimes = loadRecentTimingSamples();
            const mergedTimes = backendTimes.length >= localTimes.length
                ? backendTimes
                : mergeTimingSamples(localTimes, backendTimes, MAX_RECENT_ANALYSIS_TIMES);
            nextConfig.recent_analysis_seconds = mergedTimes;
            nextConfig.estimated_analysis_seconds = averageTimingSeconds(
                mergedTimes,
                nextConfig.estimated_analysis_seconds || DEFAULT_APP_CONFIG.estimated_analysis_seconds,
            );
            if (mergedTimes.length > 0) {
                saveRecentTimingSamples(mergedTimes);
            }

            setConfig(nextConfig);
            setTimeZone(nextConfig.display_timezone || "");
            setConfigLoaded(true);
        } catch { }
    }, [setTimeZone]);

    const handleAnalyze = useCallback(async () => {
        if (isAnalyzingRef.current) return;
        const runStartedAt = Date.now();
        runAttemptedRef.current = true;
        autoRunStartedRef.current = true;
        lastRunFailedRef.current = false;
        analysisStartedAtRef.current = runStartedAt;
        streamStartedAtRef.current = null;
        setIsAnalyzing(true);
        setError(null);
        setFeed([]);
        setExpandedIdxs(new Set());
        setResult(null);
        setAnalysisStartedAt(runStartedAt);
        setStreamStartedAt(null);
        setElapsedSeconds(0);
        setLatestLogMessage("Connecting to backend...");
        setBackendPhaseLabel("");
        setComparisonResult(null);
        setComparisonError(null);
        setSavedComparisonBaseline(null);
        setSavedComparisonResult(null);
        setSavedComparisonError(null);
        setShowCompletedProgressUntil(null);
        articleCounter.current = 0;

        try {
            const response = await fetch("/api/analyze/stream", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    symbols: trackedSymbols,
                    max_posts: config.max_posts,
                    lookback_days: config.lookback_days,
                }),
            });
            if (!response.ok || !response.body) throw new Error(`Server error: ${response.statusText}`);

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            let shouldStop = false;
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop() ?? "";
                for (const line of lines) {
                    if (!line.startsWith("data: ")) continue;
                    try {
                        const event = JSON.parse(line.slice(6));
                        if (!streamStartedAtRef.current) {
                            streamStartedAtRef.current = Date.now();
                            setStreamStartedAt(streamStartedAtRef.current);
                        }
                        if (event.type === "log") {
                            setLatestLogMessage(event.message);
                            setFeed((p) => [...p, { kind: "log", message: event.message }]);
                        } else if (event.type === "phase") {
                            const label = String(event.label || "Running analysis");
                            setBackendPhaseLabel(label);
                            setLatestLogMessage(label);
                        } else if (event.type === "article") {
                            const idx = articleCounter.current++;
                            setFeed((p) => [...p, { kind: "article", idx, source: event.source, title: event.title, description: event.description ?? "", keywords: event.keywords ?? [] }]);
                        } else if (event.type === "result") {
                            setLatestLogMessage("Analysis complete");
                            setResult(event.data);
                            setShowCompletedProgressUntil(Date.now() + 1200);
                            const startedAt = analysisStartedAtRef.current ?? runStartedAt;
                            const completionSeconds = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
                            setConfig((prev) => {
                                const updatedTimes = appendTimingSample(
                                    prev.recent_analysis_seconds,
                                    completionSeconds,
                                    MAX_RECENT_ANALYSIS_TIMES,
                                );
                                saveRecentTimingSamples(updatedTimes);
                                return {
                                    ...prev,
                                    recent_analysis_seconds: updatedTimes,
                                    estimated_analysis_seconds: averageTimingSeconds(
                                        updatedTimes,
                                        prev.estimated_analysis_seconds || DEFAULT_APP_CONFIG.estimated_analysis_seconds,
                                    ),
                                };
                            });
                            void fetchPnl();
                        } else if (event.type === "error") {
                            lastRunFailedRef.current = true;
                            setLatestLogMessage("Analysis failed");
                            setBackendPhaseLabel("");
                            setError(event.message);
                        } else if (event.type === "done") {
                            shouldStop = true;
                            break;
                        }
                    } catch { /* malformed */ }
                }
                if (shouldStop) {
                    try {
                        await reader.cancel();
                    } catch { }
                    break;
                }
            }
        } catch (err: any) {
            lastRunFailedRef.current = true;
            setError(err.message || "Failed to connect to backend");
        } finally {
            setIsAnalyzing(false);
            setAnalysisStartedAt(null);
            setStreamStartedAt(null);
            analysisStartedAtRef.current = null;
            streamStartedAtRef.current = null;
            void fetchConfig();
        }
    }, [config.lookback_days, config.max_posts, config.auto_run_interval_minutes, trackedSymbols, fetchConfig]);

    const fetchPnl = useCallback(async () => {
        try {
            const response = await fetch("/api/pnl", { cache: "no-store" });
            if (response.ok) {
                setPnlSummary(await response.json());
            }
        } catch { }
    }, []);

    const fetchOllamaStatus = useCallback(async () => {
        try {
            const response = await fetch("/api/ollama/status", { cache: "no-store" });
            if (response.ok) {
                setOllamaStatus(await response.json());
            }
        } catch { }
    }, []);

    const fetchAnalysisSnapshots = useCallback(async () => {
        try {
            const response = await fetch("/api/analyze/snapshots?limit=12", { cache: "no-store" });
            if (!response.ok) return;
            const payload = await response.json();
            setAnalysisSnapshots(payload.items || []);
        } catch { }
    }, []);

    useEffect(() => {
        if (typeof window === "undefined") return;
        const saved = localStorage.getItem(GOLDEN_DATASET_REQUEST_ID_KEY) || "";
        if (saved) setGoldenDatasetRequestId(saved);
    }, []);

    const fetchSnapshotDetail = useCallback(async (requestId: string) => {
        const response = await fetch(`/api/analyze/snapshots/${encodeURIComponent(requestId)}`, { cache: "no-store" });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload?.detail?.message || payload?.error || "Failed to load saved run");
        }
        return payload as AnalysisResult;
    }, []);

    const restoreLastViewedResult = useCallback(async () => {
        try {
            const response = await fetch("/api/analyze/snapshots?limit=12", { cache: "no-store" });
            if (!response.ok) return;
            const payload = await response.json();
            const items = (payload.items || []) as AnalysisSnapshotItem[];
            setAnalysisSnapshots(items);

            const availableSnapshots = items.filter((item) => item.snapshot_available);
            if (availableSnapshots.length === 0) return;

            const storedRequestId = typeof window !== "undefined"
                ? localStorage.getItem(LAST_VIEWED_ANALYSIS_REQUEST_ID_KEY)
                : null;
            const preferredSnapshot = (
                storedRequestId
                    ? availableSnapshots.find((item) => item.request_id === storedRequestId)
                    : null
            ) || availableSnapshots[0];

            if (!preferredSnapshot) return;

            const restored = await fetchSnapshotDetail(preferredSnapshot.request_id);
            if (isAnalyzingRef.current) return;
            setResult(restored);
        } catch { }
        finally {
            setRestoringLastResult(false);
        }
    }, [fetchSnapshotDetail]);

    const handleCloseTrade = useCallback(async (tradeId: number, closedPrice: number, notes: string) => {
        await fetch(`/api/trades/${tradeId}/close`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ closed_price: closedPrice, notes }),
        });
        void fetchPnl();
    }, [fetchPnl]);

    const handleRerunSnapshot = useCallback(async (
        requestId: string,
        modelName: string,
        extractionModel?: string,
        reasoningModel?: string,
    ) => {
        if (!requestId || (!modelName && !extractionModel)) return;
        setComparisonLoading(true);
        setComparisonError(null);
        try {
            const baselinePromise = result?.request_id === requestId && result
                ? Promise.resolve(result)
                : fetchSnapshotDetail(requestId);
            const responsePromise = fetch("/api/analyze/rerun", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    request_id: requestId,
                    ...(modelName ? { model_name: modelName } : {}),
                    ...(extractionModel ? { extraction_model: extractionModel } : {}),
                    ...(reasoningModel ? { reasoning_model: reasoningModel } : {}),
                }),
            });
            const [baselinePayload, response] = await Promise.all([baselinePromise, responsePromise]);
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload?.detail?.message || payload?.error || "Failed to rerun snapshot");
            }
            setComparisonBaselineResult(baselinePayload);
            setComparisonResult(payload);
            if (requestId === goldenDatasetRequestId) {
                setGoldenBaselineResult(baselinePayload);
                setBenchmarkResults((current) => {
                    const next = [payload as AnalysisResult, ...current.filter((item) => item.request_id !== payload.request_id)];
                    return next.slice(0, 8);
                });
            }
            void fetchPnl();
            void fetchAnalysisSnapshots();
        } catch (err: any) {
            setComparisonError(err.message || "Failed to rerun snapshot");
        } finally {
            setComparisonLoading(false);
        }
    }, [fetchAnalysisSnapshots, fetchPnl, fetchSnapshotDetail, goldenDatasetRequestId, result]);

    const handleCompareSavedRuns = useCallback(async (baselineRequestId: string, comparisonRequestId: string) => {
        if (!baselineRequestId || !comparisonRequestId || baselineRequestId === comparisonRequestId) return;
        setSavedComparisonLoading(true);
        setSavedComparisonError(null);
        try {
            const [baselinePayload, comparisonPayload] = await Promise.all([
                fetchSnapshotDetail(baselineRequestId),
                fetchSnapshotDetail(comparisonRequestId),
            ]);
            setSavedComparisonBaseline(baselinePayload);
            setSavedComparisonResult(comparisonPayload);
        } catch (err: any) {
            setSavedComparisonError(err.message || "Failed to compare saved runs");
        } finally {
            setSavedComparisonLoading(false);
        }
    }, [fetchSnapshotDetail]);

    const handleSelectGoldenDataset = useCallback(async (requestId: string) => {
        setGoldenDatasetRequestId(requestId);
        if (typeof window !== "undefined") {
            localStorage.setItem(GOLDEN_DATASET_REQUEST_ID_KEY, requestId);
        }
        setBenchmarkResults([]);
        if (result?.request_id === requestId && result) {
            setGoldenBaselineResult(result);
            return;
        }
        try {
            const payload = await fetchSnapshotDetail(requestId);
            setGoldenBaselineResult(payload);
        } catch {
            setGoldenBaselineResult(null);
        }
    }, [fetchSnapshotDetail, result]);

    const handleClearBenchmarks = useCallback(() => {
        setBenchmarkResults([]);
        setGoldenBaselineResult(null);
    }, []);

    useEffect(() => {
        const timerStart = streamStartedAt ?? analysisStartedAt;
        if (!isAnalyzing || !timerStart) return;
        const id = setInterval(() => {
            setElapsedSeconds(Math.floor((Date.now() - timerStart) / 1000));
        }, 1000);
        return () => clearInterval(id);
    }, [isAnalyzing, analysisStartedAt, streamStartedAt]);

    useEffect(() => { handleAnalyzeRef.current = handleAnalyze; }, [handleAnalyze]);

    useEffect(() => {
        void fetchConfig();
    }, [fetchConfig]);

    useEffect(() => {
        if (!configLoaded || restoringLastResult || isAnalyzing || autoRunStartedRef.current || runAttemptedRef.current) return;
        if (config.auto_run_enabled && config.can_auto_run_now && !result && feed.length === 0) {
            autoRunStartedRef.current = true;
            void handleAnalyze();
        }
    }, [config, configLoaded, feed.length, handleAnalyze, isAnalyzing, restoringLastResult, result]);

    // Price polling
    const fetchPrices = useCallback(async () => {
        try {
            const query = pricePanelSymbols.length > 0 ? `?symbols=${encodeURIComponent(pricePanelSymbols.join(","))}` : "";
            const r = await fetch(`/api/prices${query}`);
            if (r.ok) setPrices(await r.json());
        } catch { }
    }, [pricePanelSymbols]);

    useEffect(() => {
        fetchPrices();
        const id = setInterval(fetchPrices, 300_000);
        return () => clearInterval(id);
    }, [fetchPrices]);

    useEffect(() => {
        fetchPnl();
    }, [fetchPnl]);

    useEffect(() => {
        void fetchOllamaStatus();
        const id = setInterval(fetchOllamaStatus, 15_000);
        return () => clearInterval(id);
    }, [fetchOllamaStatus]);

    useEffect(() => {
        void fetchAnalysisSnapshots();
    }, [fetchAnalysisSnapshots, result?.request_id]);

    useEffect(() => {
        void restoreLastViewedResult();
    }, [restoreLastViewedResult]);

    // When the auto-run in the provider completes, promote its result to the hero.
    const { countdown: ctxCountdown, latestResult: ctxLatestResult } = useAnalysis();
    useEffect(() => {
        if (ctxLatestResult && !isAnalyzing) {
            setResult(ctxLatestResult);
            setFeed([]);
            setExpandedIdxs(new Set());
            if (ctxLatestResult.request_id && typeof window !== "undefined") {
                localStorage.setItem(LAST_VIEWED_ANALYSIS_REQUEST_ID_KEY, ctxLatestResult.request_id);
            }
        }
    }, [ctxLatestResult, isAnalyzing]);

    useEffect(() => {
        if (!result?.request_id || typeof window === "undefined") return;
        localStorage.setItem(LAST_VIEWED_ANALYSIS_REQUEST_ID_KEY, result.request_id);
    }, [result?.request_id]);

    useEffect(() => {
        if (!showCompletedProgressUntil) return;
        const delay = Math.max(0, showCompletedProgressUntil - Date.now());
        const id = window.setTimeout(() => setShowCompletedProgressUntil(null), delay);
        return () => window.clearTimeout(id);
    }, [showCompletedProgressUntil]);

    useEffect(() => {
        if (!advancedMode && activeTab === "debug") setActiveTab("signal");
    }, [advancedMode, activeTab]);

    useEffect(() => {
        if (activeTab !== "history") return;
        const id = window.setTimeout(() => {
            historySectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 0);
        return () => window.clearTimeout(id);
    }, [activeTab]);

    const toggleArticle = (idx: number) => {
        setExpandedIdxs((prev) => {
            const next = new Set(prev);
            next.has(idx) ? next.delete(idx) : next.add(idx);
            return next;
        });
    };

    // ─── Derived rendering state ──────────────────────────────────────────────

    const errorText = String(error || "");
    const errorLower = errorText.toLowerCase();
    const isCloudBackend = config.inference_backend === "openai";
    const isModelNotFoundError = errorLower.includes("model not found");
    const isOllamaError = isModelNotFoundError
        || errorLower.includes("ollama api")
        || errorLower.includes("cannot connect to ollama")
        || errorLower.includes("is ollama running")
        || errorLower.includes("ollama endpoint not found");
    const isCloudError = isCloudBackend && (errorLower.includes("openai") || errorLower.includes("api key") || errorLower.includes("401") || errorLower.includes("authentication"));
    const activeModelLabel = ollamaStatus?.active_model || ollamaStatus?.configured_model || "No model detected";
    const configuredPipelineModel = config.reasoning_model?.trim() || config.extraction_model?.trim() || "";
    const missingModelMatch = errorText.match(/model not found:\s*`?([^`\s]+)`?/i);
    const missingModelName = missingModelMatch?.[1]?.trim() || "";
    const ollamaCommandModel = missingModelName || configuredPipelineModel || ollamaStatus?.active_model || ollamaStatus?.configured_model || "the-first-model-you-served";
    const ollamaHintCommand = isModelNotFoundError ? `ollama pull ${ollamaCommandModel}` : `ollama run ${ollamaCommandModel}`;
    const cloudHintCommand = isModelNotFoundError
        ? `Verify the model name "${missingModelName}" is available on your provider and the base URL is correct.`
        : "Check your API key and base URL in the admin LLM Configuration section.";
    const isActuallyOllamaError = isOllamaError && !isCloudBackend;
    const errorBannerTitle = isCloudBackend && isModelNotFoundError
        ? "Model not found on provider"
        : isCloudBackend && isCloudError
            ? "Cloud LLM authentication error"
            : isModelNotFoundError
                ? "Model not available in Ollama"
                : isOllamaError
                    ? "Ollama runtime issue"
                    : "Error";
    const errorBannerBg = isCloudBackend ? "bg-violet-950/60 border-violet-700/50 text-violet-300" : "bg-orange-950/60 border-orange-700/50 text-orange-300";
    const errorHeaderIcon = isCloudBackend ? "☁️" : "⚠️";
    const showHint = isOllamaError || isModelNotFoundError || isCloudError;
    const feedCountLabel = `${config.enabled_rss_feeds.length || DEFAULT_APP_CONFIG.enabled_rss_feeds.length} RSS sources`;
    const currentRequestTrades = (pnlSummary?.trades ?? []).filter((trade) => trade.request_id === result?.request_id);
    const selectedTrade = selectedRecommendation
        ? currentRequestTrades.find((trade) => trade.symbol === selectedRecommendation.symbol && trade.action === selectedRecommendation.action)
        : null;
    const articleItems = feed.filter((f): f is FeedItem & { kind: "article" } => f.kind === "article");
    const logItems = feed.filter((f): f is FeedItem & { kind: "log" } => f.kind === "log");
    const mm = Math.floor(ctxCountdown / 60);
    const ss = ctxCountdown % 60;
    const stageIndex = (() => {
        const message = latestLogMessage.toLowerCase();
        let best = 0;
        ANALYSIS_STAGES.forEach((stage, index) => {
            if (stage.matches.some((token) => message.includes(token.toLowerCase()))) {
                best = Math.max(best, index);
            }
        });
        return best;
    })();
    const stageLabel = backendPhaseLabel || ANALYSIS_STAGES[stageIndex]?.label || "Running analysis";
    const recentAnalysisTimes = config.recent_analysis_seconds || [];
    const timing = estimateRunTiming(recentAnalysisTimes, config.estimated_analysis_seconds || 82);
    const hasReliableHistory = timing.reliable;
    const estimatedAnalysisSeconds = timing.expectedSeconds;
    const pacingSeconds = timing.pacingSeconds;
    const elapsedRatio = pacingSeconds > 0 ? elapsedSeconds / pacingSeconds : 0;
    const rawProgressPct = clamp(elapsedRatio * 100, 0, 99);
    const justCompleted = !!(showCompletedProgressUntil && Date.now() < showCompletedProgressUntil);
    const progressPct = justCompleted ? 100 : rawProgressPct;
    const etaSeconds = justCompleted
        ? 0
        : isAnalyzing
            ? Math.max(0, Math.round(pacingSeconds - elapsedSeconds))
            : 0;
    const isWaitingForStream = isAnalyzing && !streamStartedAt;
    const showAnalysisStatusCard = isAnalyzing || justCompleted;
    const hasCompareResults = !!(comparisonResult || savedComparisonResult);

    const saveTradeExecution = useCallback(async (payload: { executedAction: "BUY" | "SELL"; executedPrice: number; }) => {
        if (!selectedTrade) return;
        const response = await fetch(`/api/trades/${selectedTrade.id}/execute`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                executed_action: payload.executedAction,
                executed_price: payload.executedPrice,
            }),
        });
        if (!response.ok) {
            throw new Error("Failed to save trade");
        }
        await fetchPnl();
        setSelectedRecommendation(null);
    }, [fetchPnl, selectedTrade]);

    return (
        <div className="min-h-screen" style={{ backgroundColor: "#0f172a", color: "#f8fafc" }}>

            {/* ── Header ── */}
            <AppHeader
                titleSlot={
                    <button type="button" onClick={() => setActiveTab("signal")} className="text-left shrink-0">
                        <h1 className="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-emerald-400">
                            Sentiment Trading Alpha
                        </h1>
                        <p className="text-slate-500 text-xs mt-0.5">{trackedSymbols.join(" · ")} | Geopolitical Sentiment Pipeline</p>
                    </button>
                }
            >
                <div className="flex items-center gap-3 shrink-0">
                    {error && (
                        <span className="flex items-center gap-1.5 text-xs bg-red-500/10 text-red-400 px-2.5 py-1 rounded-full border border-red-500/20">
                            <WifiOff size={11} /> {isOllamaError ? "Ollama" : "Error"}
                        </span>
                    )}
                    <div className="text-right hidden sm:block">
                        <p className="text-[11px] text-slate-500">Status</p>
                        <p className={`text-xs font-semibold ${isAnalyzing ? "text-yellow-400" : result ? "text-emerald-400" : "text-slate-400"}`}>
                            {isAnalyzing ? "Analyzing…" : result ? "Ready" : "Idle"}
                        </p>
                    </div>
                    <Link href="/trading" className="text-xs text-emerald-400 hover:text-emerald-200 border border-emerald-500/20 rounded-lg px-2.5 py-1.5">
                        Trading
                    </Link>
                    <Link href="/about" className="text-xs text-slate-400 hover:text-white border border-slate-700/60 rounded-lg px-2.5 py-1.5">
                        About
                    </Link>
                    <Link href="/health" className="text-xs text-emerald-400 hover:text-emerald-200 border border-emerald-500/20 rounded-lg px-2.5 py-1.5">
                        Health
                    </Link>
                    <Link href="/admin" className="text-xs text-blue-400 hover:text-blue-200 border border-blue-500/20 rounded-lg px-2.5 py-1.5">
                        Admin
                    </Link>
                </div>
            </AppHeader>

            <main className="max-w-6xl mx-auto px-6 py-8">
                {/* ── Tab navigation ── */}
                <nav className="flex items-center gap-1 rounded-xl p-1 mb-6 w-fit" style={{ background: "rgba(15,23,42,0.8)", border: "1px solid rgba(255,255,255,0.06)" }}>
                    {(["signal", "history", "compare", ...(advancedMode ? ["debug"] : [])] as ("signal" | "history" | "compare" | "debug")[]).map((tab) => {
                        const labels: Record<string, string> = { signal: "Signal", history: "History", compare: "Compare", debug: "Debug" };
                        const isActive = activeTab === tab;
                        const hasDot = tab === "compare" && hasCompareResults;
                        return (
                            <button
                                key={tab}
                                type="button"
                                onClick={() => setActiveTab(tab)}
                                className={`flex items-center gap-1.5 rounded-lg py-1.5 px-3 text-xs font-semibold transition-colors ${isActive ? "bg-slate-700 text-white shadow-sm" : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
                                    }`}
                            >
                                {labels[tab]}
                                {hasDot && <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-400 shrink-0" />}
                            </button>
                        );
                    })}
                    <div className="w-px h-5 bg-slate-700/60 mx-1" />
                    <button
                        type="button"
                        onClick={() => setAdvancedMode((current) => !current)}
                        className={`rounded-lg py-1.5 px-2.5 text-[10px] font-semibold uppercase tracking-wider transition-colors ${advancedMode
                            ? "bg-blue-500/20 text-blue-300 border border-blue-400/30"
                            : "text-slate-500 hover:text-slate-300 hover:bg-slate-800/60"
                            }`}
                        title={advancedMode ? "Advanced mode enabled — Debug tab visible" : "Enable advanced mode to show Debug tab"}
                    >
                        {advancedMode ? "Adv" : "Std"}
                    </button>
                    {advancedMode && (
                        <Link
                            href="/alpha"
                            className="rounded-lg py-1.5 px-2.5 text-[10px] font-semibold uppercase tracking-wider transition-colors text-violet-400 hover:text-violet-200 hover:bg-violet-900/30 border border-violet-500/30"
                            title="Alpha Analytics — IC, attribution, perturbation testing"
                        >
                            α Alpha
                        </Link>
                    )}
                </nav>
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

                    {/* ── Left Sidebar ── */}
                    <div className="space-y-4">

                        {/* Engine Config */}
                        <GlassCard>
                            <h2 className="text-sm font-semibold text-slate-300 mb-4">Engine Config</h2>
                            <div className="space-y-2.5 text-sm mb-5">
                                {(() => {
                                    const em = config.extraction_model?.trim();
                                    const rm = config.reasoning_model?.trim();
                                    const twoStage = !!(em && rm);
                                    const lightSingle = !!(em && !rm && config.rss_article_detail_mode === "light");
                                    const modelRows = twoStage
                                        ? [
                                            { label: "Stage 1 (extract)", val: em!, cls: "text-blue-300 font-mono text-xs" },
                                            { label: "Stage 2 (reason)", val: rm!, cls: "text-violet-300 font-mono text-xs" },
                                        ]
                                        : [{ label: lightSingle ? "Model (Light)" : "Model", val: em || activeModelLabel, cls: "text-blue-300 font-mono text-xs" }];
                                    return [
                                        ...modelRows,
                                        { label: "Feeds", val: feedCountLabel, cls: "font-mono text-xs" },
                                        { label: "Symbols", val: trackedSymbols.join(", "), cls: "font-mono text-xs" },
                                        (() => {
                                            const profile = config.risk_profile || "standard";
                                            const leverageMap: Record<string, string> = {
                                                conservative: "1x+inv",
                                                standard: "≤2x cond.",
                                                crazy: "3x always",
                                                custom: "custom",
                                            };
                                            const profileLabel = profile.charAt(0).toUpperCase() + profile.slice(1);
                                            const leverageLabel = leverageMap[profile] ?? "3x";
                                            const clsMap: Record<string, string> = {
                                                conservative: "text-blue-400",
                                                standard: "text-teal-400",
                                                crazy: "text-rose-400",
                                                custom: "text-amber-400",
                                            };
                                            return { label: "Risk", val: `${profileLabel} (${leverageLabel})`, cls: `font-mono text-xs font-bold ${clsMap[profile] ?? "text-teal-400"}` };
                                        })(),
                                    ].map(({ label, val, cls }) => (
                                        <div key={label} className="flex justify-between border-b border-slate-700/40 pb-2 last:border-0">
                                            <span className="text-slate-400">{label}</span>
                                            <span className={cls}>{val}</span>
                                        </div>
                                    ));
                                })()}
                            </div>
                            <div className="mb-4 rounded-xl border border-slate-700/50 bg-slate-900/50 px-3 py-2 text-xs">
                                <p className="text-slate-500 uppercase tracking-wider mb-1">Runtime</p>
                                {(() => {
                                    const isCloud = config.inference_backend === "openai";
                                    const hasLocal = config.local_models && config.local_models.length > 0;
                                    const hasCloud = config.cloud_models && config.cloud_models.length > 0;
                                    const localReachable = ollamaStatus?.reachable;
                                    const cloudReachable = hasCloud;
                                    const isHybrid = isCloud && hasLocal;
                                    if (isHybrid) {
                                        return (
                                            <>
                                                <p className="text-emerald-300">☁️ Cloud LLM connected · {hasCloud} models</p>
                                                <p className={localReachable ? "text-emerald-300" : "text-orange-300"}>
                                                    {localReachable ? "🤖 Ollama connected" : "⚠️ Ollama unavailable"}
                                                </p>
                                                <p className="text-slate-400 mt-1">
                                                    {config.extraction_model?.trim() && config.reasoning_model?.trim()
                                                        ? `${config.extraction_model} → ${config.reasoning_model}`
                                                        : `Pipeline model: ${configuredPipelineModel || "not set"}`}
                                                </p>
                                            </>
                                        );
                                    }
                                    if (isCloud) {
                                        return (
                                            <>
                                                <p className={cloudReachable ? "text-emerald-300" : "text-orange-300"}>
                                                    {cloudReachable ? "☁️ Connected to cloud" : "☁️ Cloud LLM configured — connect in admin"}
                                                </p>
                                                <p className="text-slate-400 mt-1">
                                                    {cloudReachable
                                                        ? (config.extraction_model?.trim() && config.reasoning_model?.trim()
                                                            ? `${config.extraction_model} → ${config.reasoning_model}`
                                                            : `${config.openai_model || "gpt-4o-mini"} (default cloud model)`)
                                                        : "Save an API key and click ↻ to load available models."}
                                                </p>
                                            </>
                                        );
                                    }
                                    // Ollama-only (default)
                                    return (
                                        <>
                                            <p className={localReachable ? "text-emerald-300" : "text-orange-300"}>
                                                {localReachable ? "🤖 Ollama reachable" : "Waiting for Ollama"}
                                            </p>
                                            <p className="text-slate-400 mt-1">
                                                {localReachable
                                                    ? (config.extraction_model?.trim() && config.reasoning_model?.trim()
                                                        ? `${config.extraction_model} → ${config.reasoning_model}`
                                                        : `Pipeline model: ${configuredPipelineModel || activeModelLabel}`)
                                                    : "The dashboard will use whichever local model Ollama is currently serving."}
                                            </p>
                                        </>
                                    );
                                })()}
                            </div>
                            <button onClick={handleAnalyze} disabled={isAnalyzing}
                                className={`w-full py-3 rounded-xl font-bold text-sm flex items-center justify-center gap-2 transition-colors ${isAnalyzing ? "bg-slate-700 cursor-not-allowed text-slate-400" : "bg-blue-600 hover:bg-blue-500 text-white"
                                    }`}>
                                {isAnalyzing ? <><Activity size={14} className="animate-spin" /> Analyzing…</> :
                                    result ? <>Run Again <ArrowRight size={14} /></> :
                                        <>Analyze Market <ArrowRight size={14} /></>}
                            </button>
                            <div className="flex items-center justify-center gap-1.5 mt-2 text-xs text-slate-600">
                                <Clock size={11} />
                                <span>{config.auto_run_enabled ? `Auto-run in ${mm}:${ss.toString().padStart(2, "0")}` : "Auto-run disabled"}</span>
                            </div>
                        </GlassCard>

                        {/* Live Prices */}
                        <GlassCard>
                            <h2 className="text-sm font-semibold text-slate-300 mb-3">Market Prices</h2>
                            {prices ? (
                                <div>
                                    {trackedSymbols
                                        .filter((symbol) => prices[symbol])
                                        .map((symbol) => (
                                            <PriceRow key={symbol} symbol={symbol} q={prices[symbol]} />
                                        ))}
                                </div>
                            ) : (
                                <p className="text-xs text-slate-600 italic">Loading…</p>
                            )}
                        </GlassCard>

                        {/* Signal Logic (collapsible) */}
                        <GlassCard>
                            <button
                                type="button"
                                onClick={() => setSignalLogicExpanded((prev) => !prev)}
                                className="w-full flex items-center justify-between text-sm font-semibold text-slate-300 mb-0"
                            >
                                <span>Signal Logic</span>
                                <span className="text-slate-500 text-xs">{signalLogicExpanded ? "▲" : "▼"} {signalLogicExpanded ? "Hide" : "Show"}</span>
                            </button>
                            {signalLogicExpanded && (
                                <div className="space-y-3 mt-3">
                                    {/* Score explanations */}
                                    <div className="space-y-2 pb-3 border-b border-slate-700/30">
                                        <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Score Components</p>
                                        {SIGNAL_METRICS.map(({ key, label, range, desc, color }) => (
                                            <div key={key} className="bg-slate-800/40 rounded-lg p-2.5">
                                                <div className="flex items-center justify-between mb-0.5">
                                                    <p className={`text-xs font-bold ${color}`}>{label}</p>
                                                    <span className="text-[10px] font-mono text-slate-500">{range}</span>
                                                </div>
                                                <p className="text-[11px] text-slate-400 leading-relaxed">{desc}</p>
                                            </div>
                                        ))}
                                    </div>
                                    {/* Signal rules */}
                                    <div className="space-y-2">
                                        <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Signal Triggers</p>
                                        {SIGNAL_RULES.map(({ border, bg, label, labelColor, desc }) => (
                                            <div key={label} className={`border-l-4 ${border} ${bg} p-2.5 rounded-r-lg`}>
                                                <p className={`text-xs font-bold uppercase ${labelColor}`}>{label}</p>
                                                <p className="text-[11px] font-mono text-slate-400 mt-0.5">{desc}</p>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </GlassCard>

                        {/* Run Stats */}
                        {result && (
                            <GlassCard>
                                <h2 className="text-sm font-semibold text-slate-300 mb-3">Run Stats</h2>
                                <div className="space-y-1.5 text-sm">
                                    {[
                                        { label: "Articles", val: result.posts_scraped },
                                        { label: "Symbols", val: result.symbols_analyzed?.join(", ") },
                                        { label: "Duration", val: `${(result.processing_time_ms / 1000).toFixed(1)}s` },
                                    ].map(({ label, val }) => (
                                        <div key={label} className="flex justify-between">
                                            <span className="text-slate-400 text-xs">{label}</span>
                                            <span className="font-mono text-xs">{val}</span>
                                        </div>
                                    ))}
                                </div>
                            </GlassCard>
                        )}
                    </div>

                    {/* ── Main Content ── */}
                    <div className="lg:col-span-2 space-y-5">

                        {/* Error Banner */}
                        <AnimatePresence>
                            {error && (
                                <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                                    className={`p-4 rounded-xl border flex items-start gap-3 ${isCloudBackend || isCloudError
                                        ? "bg-violet-950/60 border-violet-700/50 text-violet-300"
                                        : isOllamaError
                                            ? "bg-orange-950/60 border-orange-700/50 text-orange-300"
                                            : "bg-red-950/60 border-red-700/50 text-red-300"}`}>
                                    <span className="text-lg mt-0.5 shrink-0">{isCloudBackend ? "☁️" : <WifiOff size={16} />}</span>
                                    <div>
                                        <p className="font-semibold text-sm">{errorBannerTitle}</p>
                                        <p className="text-sm mt-0.5 opacity-80">{error}</p>
                                        {isActuallyOllamaError && (
                                            <code className="text-xs mt-2 block bg-black/40 px-3 py-1.5 rounded font-mono">
                                                {ollamaHintCommand}
                                            </code>
                                        )}
                                        {isCloudBackend && (isModelNotFoundError || isCloudError) && (
                                            <code className="text-xs mt-2 block bg-black/40 px-3 py-1.5 rounded font-mono">
                                                {cloudHintCommand}
                                            </code>
                                        )}
                                    </div>
                                </motion.div>
                            )}
                        </AnimatePresence>

                        {/* Signal Hero (results) */}
                        <AnimatePresence>
                            {showAnalysisStatusCard && (
                                <motion.div key="analysis-status" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
                                    <AnalysisStatusCard
                                        stageLabel={stageLabel}
                                        progressPct={progressPct}
                                        elapsedSeconds={elapsedSeconds}
                                        etaSeconds={etaSeconds}
                                        latestMessage={latestLogMessage || "Waiting for the next pipeline update..."}
                                        isWaitingForStream={isWaitingForStream}
                                        hasReliableHistory={hasReliableHistory}
                                    />
                                </motion.div>
                            )}
                            {result && (
                                <motion.div key={`analysis-result-${result.request_id || "latest"}`} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
                                    <SignalHero
                                        signal={result.trading_signal}
                                        redTeamReview={result.red_team_review}
                                        sentimentScores={result.sentiment_scores}
                                        trackedSymbols={trackedSymbols}
                                        trackedTrades={currentRequestTrades}
                                        onRecommendationClick={setSelectedRecommendation}
                                    />

                                    {activeTab === "signal" && (
                                        <div className="space-y-4">
                                            <SentimentTicker data={result.sentiment_scores} />
                                            <ActualTradeComparisonCard pnlSummary={pnlSummary} currentRequestId={result.request_id} prices={prices} onCloseTrade={handleCloseTrade} />
                                        </div>
                                    )}
                                    {activeTab === "debug" && advancedMode && (
                                        <DebugPanel result={result} />
                                    )}
                                </motion.div>
                            )}

                            {activeTab === "history" && (
                                <motion.div ref={historySectionRef} key="tab-history" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
                                    <PullHistoryCard snapshots={analysisSnapshots} currentRequestId={result?.request_id} />
                                </motion.div>
                            )}
                            {activeTab === "compare" && (
                                <motion.div key="tab-compare" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
                                    <ModelComparePanel
                                        result={result ?? null}
                                        snapshots={analysisSnapshots}
                                        availableModels={ollamaStatus?.available_models ?? []}
                                        compareBaselineResult={comparisonBaselineResult}
                                        compareResult={comparisonResult}
                                        goldenDatasetRequestId={goldenDatasetRequestId}
                                        goldenBaselineResult={goldenBaselineResult}
                                        benchmarkResults={benchmarkResults}
                                        savedBaselineResult={savedComparisonBaseline}
                                        savedComparisonResult={savedComparisonResult}
                                        onRerunSnapshot={handleRerunSnapshot}
                                        onCompareSavedRuns={handleCompareSavedRuns}
                                        onSelectGoldenDataset={handleSelectGoldenDataset}
                                        onClearBenchmarks={handleClearBenchmarks}
                                        rerunLoading={comparisonLoading}
                                        rerunError={comparisonError}
                                        savedCompareLoading={savedComparisonLoading}
                                        savedCompareError={savedComparisonError}
                                    />
                                </motion.div>
                            )}
                        </AnimatePresence>

                        {/* Idle state */}
                        {!result && !isAnalyzing && feed.length === 0 && !error && (
                            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                                <GlassCard className="text-center py-8">
                                    <p className="text-slate-500 text-xs uppercase tracking-widest mb-2">Ready</p>
                                    <h2 className="text-2xl font-black mb-2 bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-emerald-400">
                                        Geopolitical Sentiment → Trade Signal
                                    </h2>
                                    <p className="text-slate-500 text-sm max-w-md mx-auto">
                                        Fetches live headlines, runs local Ollama sentiment analysis with {activeModelLabel},
                                        generates BUY/SELL signals for {trackedSymbols.join(", ")} and tracks live paper P&L over time.
                                    </p>
                                    <p className="text-slate-600 text-xs mt-4">
                                        {config.auto_run_enabled ? `Auto-runs in ${mm}:${ss.toString().padStart(2, "0")}` : "Auto-run disabled in admin settings"}
                                    </p>
                                </GlassCard>
                            </motion.div>
                        )}

                        {/* Article Feed */}
                        {feed.length > 0 && (
                            <GlassCard className="!p-0 overflow-hidden">
                                <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700/40">
                                    <div className="flex items-center gap-2">
                                        <span className="text-sm font-semibold text-slate-300">Live Feed</span>
                                        {articleItems.length > 0 && (
                                            <span className="text-xs bg-blue-500/20 text-blue-300 px-2 py-0.5 rounded-full border border-blue-500/20">
                                                {articleItems.length} articles
                                            </span>
                                        )}
                                    </div>
                                    {isAnalyzing && (
                                        <span className="flex items-center gap-1.5 text-xs text-emerald-400">
                                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                                            Running
                                        </span>
                                    )}
                                </div>
                                <div className="max-h-[520px] overflow-y-auto p-4">
                                    {isAnalyzing && (
                                        <div className="flex items-center gap-2 text-slate-700 text-xs font-mono py-1">
                                            <span>›</span><span className="animate-pulse">▋</span>
                                        </div>
                                    )}
                                    {/* Pipeline Log — always visible, newest first */}
                                    {logItems.length > 0 && (
                                        <div className="mb-3 space-y-0">
                                            {[...logItems].reverse().map((item, i) => (
                                                <div key={i} className="flex items-start gap-2 py-0.5 text-xs text-slate-500 font-mono">
                                                    <span className="text-slate-700 shrink-0">›</span>
                                                    <span>{item.message}</span>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                    {/* Articles */}
                                    {articleItems.length > 0 && [...articleItems].reverse().map((item, i) => (
                                        <ArticleCard
                                            key={i}
                                            item={item}
                                            expanded={expandedIdxs.has(item.idx)}
                                            onToggle={() => toggleArticle(item.idx)}
                                            result={result}
                                        />
                                    ))}
                                </div>
                            </GlassCard>
                        )}
                    </div>
                </div>
            </main>

            <footer className="mt-16 pb-8 text-center text-slate-700 text-xs">
                Educational use only · Not financial advice · Trading leveraged ETFs carries significant risk
            </footer>

            {selectedRecommendation && (
                <TradeExecutionModal
                    recommendation={selectedRecommendation}
                    trade={selectedTrade ?? null}
                    onClose={() => setSelectedRecommendation(null)}
                    onSave={saveTradeExecution}
                />
            )}
        </div>
    );
}