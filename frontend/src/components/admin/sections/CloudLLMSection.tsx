"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppConfig } from "@/lib/utils/config-normalizer";

type CloudLLMSecretsStatus = {
    available: boolean;
    configured: boolean;
    api_key_masked: string;
    error: string;
};

type CloudLLMSectionProps = {
    config: AppConfig;
    setConfig: React.Dispatch<React.SetStateAction<AppConfig>>;
    isAdvancedMode: boolean;
};

// ── Provider definitions ──────────────────────────────────────────────

type ProviderDef = {
    value: string;
    label: string;
    tagline: string;
};

const CLOUD_PROVIDERS: ProviderDef[] = [
    { value: "openrouter", label: "OpenRouter", tagline: "Multi-model gateway" },
    { value: "anthropic", label: "Anthropic", tagline: "Claude models" },
    { value: "openai", label: "OpenAI", tagline: "GPT models" },
    { value: "google", label: "Google", tagline: "Gemini models" },
    { value: "custom", label: "Custom", tagline: "Bring your own endpoint" },
];

const LOCAL_PROVIDERS: ProviderDef[] = [
    { value: "ollama", label: "Ollama", tagline: "Local LLM server" },
    { value: "vllm", label: "vLLM", tagline: "OpenAI-compatible local" },
    { value: "omlx", label: "OMLX", tagline: "OpenAI-compatible local (MLX)" },
    { value: "llama.cpp", label: "llama.cpp", tagline: "C++ inference server" },
    { value: "custom", label: "Custom", tagline: "Bring your own endpoint" },
];

type ProviderUrls = Record<string, string>;

const CLOUD_URLS: ProviderUrls = {
    openai: "https://api.openai.com/v1",
    anthropic: "https://api.anthropic.com",
    openrouter: "https://openrouter.ai/api/v1",
    google: "https://generativelanguage.googleapis.com",
};

/** Expected hostnames for cloud providers — used for domain validation warnings. */
const CLOUD_DOMAINS: Record<string, string[]> = {
    openai: ["api.openai.com"],
    anthropic: ["api.anthropic.com"],
    openrouter: ["openrouter.ai", "api.openrouter.ai"],
    google: ["generativelanguage.googleapis.com"],
};

/**
 * Preferred default model per cloud provider.
 * Each entry is a prioritized list — the first one found in the available
 * cloud models list is selected automatically.
 */
const PROVIDER_DEFAULT_MODELS: Record<string, string[]> = {
    openrouter: ["deepseek/deepseek-r1", "deepseek/deepseek-chat", "mistralai/mistral-7b-instruct", "gpt-4o-mini"],
    openai: ["gpt-4o-mini", "gpt-4o", "gpt-4.1-nano"],
    anthropic: ["claude-3-5-haiku-20241022", "claude-3-haiku-20240307", "claude-opus-4-20250514", "gpt-4o-mini"],
    google: ["gemini-2.0-flash-lite", "gemini-2.5-flash-preview-04-17", "gemini-1.5-flash", "gpt-4o-mini"],
    custom: ["gpt-4o-mini"],
};

const LOCAL_URLS: ProviderUrls = {
    ollama: "http://localhost:11434/api/generate",
    vllm: "http://localhost:8000",
    omlx: "http://localhost:11434/v1",
    "llama.cpp": "http://localhost:8080",
};

function smartFillUrl(mode: "cloud" | "local", provider: string): string {
    const map = mode === "cloud" ? CLOUD_URLS : LOCAL_URLS;
    return map[provider] ?? "";
}

/** Return a model tagged for display: "(local) model-name" or "(cloud) model-name". */
function modelDisplayName(model: string, localModels: Set<string>, cloudModels: Set<string>): string {
    if (cloudModels.has(model)) return `(cloud) ${model}`;
    if (localModels.has(model)) return `(local) ${model}`;
    return model;
}

export function CloudLLMSection({ config, setConfig, isAdvancedMode }: CloudLLMSectionProps) {
    const [secrets, setSecrets] = useState<CloudLLMSecretsStatus>({
        available: false,
        configured: false,
        api_key_masked: "",
        error: "",
    });
    const [apiKeyInput, setApiKeyInput] = useState("");
    const [isSaving, setIsSaving] = useState(false);
    const [status, setStatus] = useState("");
    const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
    const [isTesting, setIsTesting] = useState(false);

    // ── Cloud models fetched on demand ─────────────────────────────────
    const [cloudModels, setCloudModels] = useState<string[]>([]);
    const [isLoadingCloudModels, setIsLoadingCloudModels] = useState(false);
    const [cloudModelsError, setCloudModelsError] = useState("");

    const localModelsSet = useMemo(() => new Set(config.local_models), [config.local_models]);
    const cloudModelsSet = useMemo(() => new Set(cloudModels), [cloudModels]);

    // ── Derived state ───────────────────────────────────────────────────
    const currentProviders = config.api_mode === "cloud" ? CLOUD_PROVIDERS : LOCAL_PROVIDERS;
    const currentProvider = config.api_mode === "cloud" ? config.cloud_provider : config.local_provider;
    // For protocol validation, "custom" means the user is typing their own URL
    const userIsTypingUrl = currentProvider === "custom" || config.user_edited_url;

    // ── Provider domain validation (cloud only) ────────────────────────
    const domainMismatch = useMemo<string | null>(() => {
        if (config.api_mode !== "cloud" || currentProvider === "custom") return null;
        const url = config.api_url?.trim() ?? "";
        if (!url) return null;
        try {
            const hostname = new URL(url).hostname.toLowerCase();
            const expected = CLOUD_DOMAINS[currentProvider];
            if (!expected) return null;
            if (!expected.some((d) => hostname === d || hostname.endsWith("." + d))) {
                return `URL domain "${hostname}" doesn't match the expected domain for ${currentProvider} (${expected.join(", ")}). Verify this is intentional.`;
            }
        } catch {
            // Malformed URL — protocolError will catch it
        }
        return null;
    }, [config.api_url, config.api_mode, currentProvider]);

    // ── Protocol validation ─────────────────────────────────────────────
    const protocolError = useMemo<string | null>(() => {
        const url = config.api_url?.trim() ?? "";
        if (!url) return null; // empty field, no warning yet
        if (config.api_mode === "cloud" && !url.startsWith("https://")) {
            return "Cloud endpoints require https:// for security";
        }
        if (config.api_mode === "local" && !url.startsWith("http://")) {
            return "Local endpoints should use http://";
        }
        return null;
    }, [config.api_url, config.api_mode]);

    const fetchCloudModels = useCallback(async (showLoading = true) => {
        if (showLoading) setIsLoadingCloudModels(true);
        setCloudModelsError("");
        try {
            // Pass the current API URL and provider so the backend can use them
            // instead of stale DB-stored values that may not be saved yet.
            const params = new URLSearchParams();
            if (config.api_url) params.set("base_url", config.api_url);
            // Include provider so the backend reads the right per-provider API key
            if (currentProvider && currentProvider !== "custom") params.set("provider", currentProvider);
            const qs = params.toString();
            const res = await fetch(`/api/admin/models${qs ? `?${qs}` : ""}`, { cache: "no-store" });
            if (!res.ok) {
                const payload = await res.json().catch(() => ({}));
                setCloudModelsError(payload?.error || "Failed to load cloud models");
                return;
            }
            const payload = await res.json();
            const models = Array.isArray(payload.cloud_models) ? payload.cloud_models : [];
            setCloudModels(models);
            if (models.length === 0) {
                setCloudModelsError("No cloud models available — check your API key and base URL.");
            }
        } catch {
            setCloudModelsError("Network error loading cloud models");
        } finally {
            if (showLoading) setIsLoadingCloudModels(false);
        }
    }, []);

    // ── Auto-select best default model when cloud models load or provider changes ──
    const previousCloudModelsLength = useRef(0);
    useEffect(() => {
        // Only auto-select when models first load (empty → populated) or provider changes
        const modelsJustLoaded = previousCloudModelsLength.current === 0 && cloudModels.length > 0;
        previousCloudModelsLength.current = cloudModels.length;

        if (!modelsJustLoaded && !currentProvider) return;

        const preferences = PROVIDER_DEFAULT_MODELS[currentProvider] || PROVIDER_DEFAULT_MODELS.custom;
        const modelSet = new Set(cloudModels);
        const best = preferences.find((m) => modelSet.has(m));
        if (!best) return;

        setConfig((c) => {
            if (c.openai_model && c.openai_model !== "gpt-4o-mini") return c; // user already picked something
            return { ...c, openai_model: best };
        });
    }, [cloudModels, currentProvider, setConfig]);

    // Track the last-used local models so we can restore them when switching back to local
    const lastLocalModelsRef = useRef<{ extraction: string; reasoning: string }>({
        extraction: config.extraction_model,
        reasoning: config.reasoning_model,
    });
    // Track the last-used cloud models so we can restore them when switching back to cloud
    const lastCloudModelsRef = useRef<{ extraction: string; reasoning: string }>({
        extraction: config.extraction_model,
        reasoning: config.reasoning_model,
    });

    // ── When backend changes, auto-select models available on that backend ──
    const prevBackendRef = useRef(config.inference_backend);
    useEffect(() => {
        const prev = prevBackendRef.current;
        const currentBackend = config.inference_backend;
        if (prev === currentBackend) return;
        prevBackendRef.current = currentBackend;

        // Save last-used local models before switching away from local
        if (prev !== "openai") {
            lastLocalModelsRef.current = {
                extraction: config.extraction_model,
                reasoning: config.reasoning_model,
            };
        }

        // Determine which model pool is relevant for the new backend
        const availableModels = currentBackend === "openai"
            ? cloudModels
            : config.local_models;

        if (availableModels.length === 0) return;

        const availableSet = new Set(availableModels);

        setConfig((c) => {
            const next = { ...c };
            let changed = false;

            const pick = (current: string): string => {
                if (current && availableSet.has(current)) return current;
                if (currentBackend !== "openai") {
                    const lastLocal = lastLocalModelsRef.current;
                    if (lastLocal.extraction && availableSet.has(lastLocal.extraction)) return lastLocal.extraction;
                    if (lastLocal.reasoning && availableSet.has(lastLocal.reasoning)) return lastLocal.reasoning;
                }
                return availableModels[0];
            };

            const newExtraction = pick(c.extraction_model);
            if (newExtraction !== c.extraction_model) {
                next.extraction_model = newExtraction;
                changed = true;
            }

            const newReasoning = pick(c.reasoning_model);
            if (newReasoning !== c.reasoning_model) {
                next.reasoning_model = newReasoning;
                changed = true;
            }

            return changed ? next : c;
        });
    }, [config.inference_backend, cloudModels, config.local_models, setConfig]);

    // ── Fetch secret status on mount ──────────────────────────────────
    const fetchSecrets = useCallback(async () => {
        try {
            const providerParam = config.api_mode === "cloud" && currentProvider !== "custom" ? `?provider=${currentProvider}` : "";
            const res = await fetch(`/api/admin/openai-secrets${providerParam}`, { cache: "no-store" });
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) {
                setSecrets((s) => ({ ...s, available: false, configured: false, error: payload?.error || "Failed to load" }));
                return;
            }
            setSecrets({
                available: !!payload.available,
                configured: !!payload.configured,
                api_key_masked: String(payload.api_key_masked || ""),
                error: String(payload.error || ""),
            });
        } catch {
            setSecrets((s) => ({ ...s, available: false, configured: false, error: "Network error" }));
        }
    }, [config.api_mode, currentProvider]);

    // ── Re-fetch secrets when provider changes in cloud mode ──────────
    useEffect(() => { void fetchSecrets(); }, [fetchSecrets]);

    // Reset cloud state when entering/exiting cloud mode so stale data
    // doesn't linger across mode switches.
    const prevApiMode = useRef(config.api_mode);
    useEffect(() => {
        const prev = prevApiMode.current;
        prevApiMode.current = config.api_mode;
        if (prev !== config.api_mode && config.api_mode === "cloud") {
            // Switching TO cloud — clear stale local-state so models re-fetch
            setCloudModels([]);
            setCloudModelsError("");
            setTestResult(null);
        }
    }, [config.api_mode]);

    // Fetch cloud models when entering cloud mode with no cached models.
    // Does NOT gate on secrets.configured — the backend handles auth and
    // the key may be stored in the OS keychain already from a previous
    // session even if the in-memory secrets flag hasn't loaded yet.
    useEffect(() => {
        if (config.api_mode === "cloud" && cloudModels.length === 0 && !isLoadingCloudModels) {
            void fetchCloudModels();
        }
    }, [config.api_mode, cloudModels.length, isLoadingCloudModels, fetchCloudModels]);

    // ── Save API key ──────────────────────────────────────────────────
    const saveApiKey = async () => {
        if (!apiKeyInput.trim()) {
            setStatus("API key is required");
            return;
        }
        setIsSaving(true);
        setStatus("");
        try {
            const providerParam = currentProvider !== "custom" ? `?provider=${currentProvider}` : "";
            const res = await fetch(`/api/admin/openai-secrets${providerParam}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ api_key: apiKeyInput.trim() }),
            });
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) {
                setStatus(payload?.detail || payload?.error || "Failed to save");
                return;
            }
            setSecrets({
                available: !!payload.available,
                configured: !!payload.configured,
                api_key_masked: String(payload.api_key_masked || ""),
                error: String(payload.error || ""),
            });
            setApiKeyInput("");
            setStatus(`API key saved for ${currentProvider}.`);
            void fetchCloudModels();
        } catch {
            setStatus("Failed to save API key");
        } finally {
            setIsSaving(false);
        }
    };

    // ── Clear API key ─────────────────────────────────────────────────
    const clearApiKey = async () => {
        setIsSaving(true);
        setStatus("");
        try {
            const providerParam = currentProvider !== "custom" ? `?provider=${currentProvider}` : "";
            const res = await fetch(`/api/admin/openai-secrets${providerParam}`, { method: "DELETE" });
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) {
                setStatus(payload?.error || "Failed to clear");
                return;
            }
            setSecrets({
                available: !!payload.available,
                configured: !!payload.configured,
                api_key_masked: String(payload.api_key_masked || ""),
                error: String(payload.error || ""),
            });
            setApiKeyInput("");
            setCloudModels([]);
            setStatus("API key cleared from OS keychain.");
        } catch {
            setStatus("Failed to clear API key");
        } finally {
            setIsSaving(false);
        }
    };

    // ── API mode toggle handler (Cloud ↔ Local) ──────────────────────
    const handleApiModeChange = (mode: "cloud" | "local") => {
        if (mode === config.api_mode) return;

        // Save the current models before switching
        if (config.api_mode === "local") {
            lastLocalModelsRef.current = {
                extraction: config.extraction_model,
                reasoning: config.reasoning_model,
            };
        } else {
            lastCloudModelsRef.current = {
                extraction: config.extraction_model,
                reasoning: config.reasoning_model,
            };
        }

        setConfig((c) => {
            const newProvider = mode === "cloud" ? c.cloud_provider : c.local_provider;
            // Only smart-fill if user hasn't manually edited the URL
            // or the URL would be empty/invalid for the new mode
            const currentUrl = c.api_url?.trim() ?? "";
            const fillUrl = smartFillUrl(mode, newProvider);
            const wasEdited = c.user_edited_url;

            // Determine if we should overwrite: if user never edited, or if the old URL
            // makes no sense in the new mode (e.g., http URL when switching to cloud)
            const oldIsHttp = currentUrl.startsWith("http://");
            const oldIsHttps = currentUrl.startsWith("https://");
            const modeMismatch = (mode === "cloud" && oldIsHttp) || (mode === "local" && oldIsHttps);

            const newUrl = (!wasEdited || modeMismatch || !currentUrl) ? fillUrl : currentUrl;

            // Determine which models to use for the new mode
            const switchingToLocal = mode === "local";
            const availableLocalModels = config.local_models || [];
            const availableCloudModels = cloudModels;

            let nextExtraction = c.extraction_model;
            let nextReasoning = c.reasoning_model;

            if (switchingToLocal) {
                // Switching to local — try to restore last-used local models, or pick first available
                if (availableLocalModels.length > 0) {
                    const localSet = new Set(availableLocalModels);
                    const lastLocal = lastLocalModelsRef.current;
                    if (lastLocal.extraction && localSet.has(lastLocal.extraction)) {
                        nextExtraction = lastLocal.extraction;
                    } else if (lastLocal.reasoning && localSet.has(lastLocal.reasoning)) {
                        nextExtraction = lastLocal.reasoning;
                    } else {
                        nextExtraction = availableLocalModels[0];
                    }
                    if (lastLocal.reasoning && localSet.has(lastLocal.reasoning)) {
                        nextReasoning = lastLocal.reasoning;
                    } else if (lastLocal.extraction && localSet.has(lastLocal.extraction)) {
                        nextReasoning = lastLocal.extraction;
                    } else {
                        nextReasoning = availableLocalModels.length > 1 ? availableLocalModels[1] : availableLocalModels[0];
                    }
                }
            } else {
                // Switching to cloud — try to restore last-used cloud models, or use provider default
                const lastCloud = lastCloudModelsRef.current;
                const cloudSet = new Set(availableCloudModels);
                const preferences = PROVIDER_DEFAULT_MODELS[newProvider] || PROVIDER_DEFAULT_MODELS.custom;
                const providerDefault = preferences.find((m) => cloudSet.has(m)) || "gpt-4o-mini";

                // Try last cloud extraction model first, then fall back to provider default
                if (lastCloud.extraction && cloudSet.has(lastCloud.extraction)) {
                    nextExtraction = lastCloud.extraction;
                } else {
                    nextExtraction = providerDefault;
                }
                // Try last cloud reasoning model first, then fall back
                if (lastCloud.reasoning && cloudSet.has(lastCloud.reasoning)) {
                    nextReasoning = lastCloud.reasoning;
                } else {
                    nextReasoning = providerDefault;
                }
            }

            return {
                ...c,
                api_mode: mode,
                api_url: newUrl,
                user_edited_url: wasEdited && !modeMismatch && currentUrl !== "",
                extraction_model: nextExtraction,
                reasoning_model: nextReasoning,
            };
        });
    };

    // ── Provider change handler (smart-fill URL) ─────────────────────
    const handleProviderChange = (provider: string) => {
        setConfig((c) => {
            const fillUrl = smartFillUrl(c.api_mode, provider);
            // Smart-fill always on provider change, regardless of edit history
            return {
                ...c,
                ...(c.api_mode === "cloud" ? { cloud_provider: provider } : { local_provider: provider }),
                api_url: fillUrl,
                // If it's "custom", user will edit, so flag accordingly
                // For known providers, reset edit flag since we just filled
                user_edited_url: provider === "custom" ? c.user_edited_url : false,
            };
        });
    };

    // ── URL manual edit handler ───────────────────────────────────────
    const handleUrlChange = (url: string) => {
        setConfig((c) => ({
            ...c,
            api_url: url,
            user_edited_url: true,
        }));
    };

    // ── All model options for dropdown: local + cloud, deduplicated ───
    const allModelOptions = useMemo(() => {
        const seen = new Set<string>();
        const options: { value: string; label: string }[] = [];

        for (const m of config.local_models) {
            if (!seen.has(m)) {
                seen.add(m);
                options.push({ value: m, label: `(local) ${m}` });
            }
        }
        for (const m of cloudModels) {
            if (!seen.has(m)) {
                seen.add(m);
                options.push({ value: m, label: `(cloud) ${m}` });
            }
        }
        return options;
    }, [config.local_models, cloudModels]);

    // ── Pick the best default model for the current cloud provider ────
    const providerDefaultModel = useMemo<string>(() => {
        if (config.api_mode !== "cloud") return "gpt-4o-mini";
        const preferences = PROVIDER_DEFAULT_MODELS[currentProvider] || PROVIDER_DEFAULT_MODELS.custom;
        const modelSet = new Set(cloudModels);
        return preferences.find((m) => modelSet.has(m)) || "gpt-4o-mini";
    }, [config.api_mode, currentProvider, cloudModels]);

    const showCloudModelDropdown = cloudModels.length > 0 || isLoadingCloudModels || cloudModelsError;

    // ── Mapping for old inference_backend
    const activeBackend = config.inference_backend || "ollama";
    const isCloudMode = config.api_mode === "cloud";

    return (
        <section id="cloud-llm" className="scroll-mt-24 rounded-2xl border border-slate-800 bg-slate-900/70 p-5 space-y-5">
            <div>
                <h2 className="text-sm font-semibold text-slate-200">LLM Configuration</h2>
                <p className="text-xs text-slate-500 mt-1">
                    Choose between cloud and local inference, then pick your provider.
                    The API URL is auto-populated — you can override it if needed.
                </p>
            </div>

            {/* ── Cloud / Local Toggle ───────────────────────────────── */}
            <div>
                <p className="text-xs text-slate-400 mb-3">API mode</p>
                <div className="grid grid-cols-2 gap-3">
                    <button
                        type="button"
                        onClick={() => handleApiModeChange("cloud")}
                        className={`rounded-xl border px-4 py-3 text-left transition-colors ${isCloudMode
                            ? "border-blue-400 bg-blue-500/10 text-blue-100"
                            : "border-slate-800 bg-slate-950/60 text-slate-300 hover:border-slate-700"
                            }`}
                    >
                        <p className="text-sm font-semibold">☁️ Cloud</p>
                        <p className="mt-0.5 text-[11px] text-slate-400 font-medium">
                            OpenAI, Anthropic, OpenRouter, Google
                        </p>
                        <p className="mt-1.5 text-[11px] text-slate-500">
                            Remote API endpoints. Requires an API key. HTTPS enforced.
                        </p>
                    </button>
                    <button
                        type="button"
                        onClick={() => handleApiModeChange("local")}
                        className={`rounded-xl border px-4 py-3 text-left transition-colors ${!isCloudMode
                            ? "border-emerald-400 bg-emerald-500/10 text-emerald-100"
                            : "border-slate-800 bg-slate-950/60 text-slate-300 hover:border-slate-700"
                            }`}
                    >
                        <p className="text-sm font-semibold">🖥️ Local</p>
                        <p className="mt-0.5 text-[11px] text-slate-400 font-medium">
                            Ollama, vLLM, llama.cpp
                        </p>
                        <p className="mt-1.5 text-[11px] text-slate-500">
                            Self-hosted inference. No API key needed. HTTP allowed.
                        </p>
                    </button>
                </div>
            </div>

            {/* ── Settings panel ─────────────────────────────────────── */}
            <div className="space-y-4 rounded-xl border border-slate-800 bg-slate-950/60 p-4">
                {/* ── Provider dropdown ─────────────────────────────── */}
                <div>
                    <label className="block">
                        <span className="text-xs text-slate-400">
                            {isCloudMode ? "Cloud Provider" : "Local Provider"}
                        </span>
                        <select
                            value={currentProvider}
                            onChange={(e) => handleProviderChange(e.target.value)}
                            className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-400"
                        >
                            {currentProviders.map((p) => (
                                <option key={p.value} value={p.value}>
                                    {p.label} — {p.tagline}
                                </option>
                            ))}
                        </select>
                    </label>
                </div>

                {/* ── API URL with smart-fill ───────────────────────── */}
                <label className="block">
                    <span className="text-xs text-slate-400">API URL</span>
                    <p className="text-[11px] text-slate-600 mt-0.5">
                        Auto-populated from provider selection.
                        Edit manually to use a custom endpoint.
                        {isCloudMode
                            ? " HTTPS required for cloud providers."
                            : " HTTP is standard for local servers."}
                    </p>
                    <div className="relative mt-2">
                        <input
                            type="text"
                            value={config.api_url ?? ""}
                            onChange={(e) => handleUrlChange(e.target.value)}
                            placeholder={isCloudMode ? "https://api.openai.com/v1" : "http://localhost:11434"}
                            className={`w-full rounded-lg border bg-slate-800 px-3 py-2 text-sm text-white outline-none font-mono transition-colors ${protocolError
                                ? "border-red-500 focus:border-red-400"
                                : "border-slate-700 focus:border-blue-400"
                                }`}
                        />
                        {config.user_edited_url && (
                            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-amber-500 font-medium">
                                custom
                            </span>
                        )}
                    </div>
                    {protocolError && (
                        <p className="mt-1.5 text-[11px] text-red-400 flex items-center gap-1">
                            <span>⚠</span>
                            <span>{protocolError}</span>
                        </p>
                    )}
                    {domainMismatch && (
                        <p className="mt-1.5 text-[11px] text-amber-400 flex items-center gap-1">
                            <span>⚠</span>
                            <span>{domainMismatch}</span>
                        </p>
                    )}
                    {!protocolError && !domainMismatch && config.user_edited_url && (
                        <p className="mt-1 text-[11px] text-amber-500">
                            Custom URL — provider changes won't overwrite until you select a different provider.
                        </p>
                    )}
                </label>

                {/* ── Cloud-specific: model & API key ────────────────── */}
                {isCloudMode ? (
                    <>
                        {/* Default model — dropdown when cloud models loaded, text input otherwise */}
                        <label className="block">
                            <span className="text-xs text-slate-400">Default model</span>
                            <p className="text-[11px] text-slate-600 mt-0.5">
                                Used when no per-stage model is selected. <code className="font-mono">{providerDefaultModel}</code> is{' '}
                                the default{currentProvider !== "custom" ? ` for ${currentProvider}` : ''}.
                            </p>

                            {showCloudModelDropdown ? (
                                <div className="flex gap-2 items-start mt-2">
                                    <div className="flex-1">
                                        <select
                                            value={config.openai_model || providerDefaultModel}
                                            onChange={(e) => setConfig((c) => ({ ...c, openai_model: e.target.value }))}
                                            className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-400"
                                        >
                                            <option value={providerDefaultModel}>{providerDefaultModel} (default)</option>
                                            {allModelOptions.map((opt) => (
                                                <option key={opt.value} value={opt.value}>
                                                    {opt.label}
                                                </option>
                                            ))}
                                        </select>
                                        {isLoadingCloudModels && (
                                            <p className="text-[11px] text-slate-500 mt-1">Loading cloud models…</p>
                                        )}
                                        {cloudModelsError && cloudModels.length === 0 && (
                                            <p className="text-[11px] text-amber-400 mt-1">{cloudModelsError}</p>
                                        )}
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => fetchCloudModels(true)}
                                        disabled={isLoadingCloudModels}
                                        className="mt-0 shrink-0 rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                                    >
                                        {isLoadingCloudModels ? "⟳" : "↻"}
                                    </button>
                                </div>
                            ) : (
                                <div className="flex gap-2 items-start mt-2">
                                    <input
                                        type="text"
                                        value={config.openai_model || providerDefaultModel}
                                        onChange={(e) => setConfig((c) => ({ ...c, openai_model: e.target.value }))}
                                        placeholder={providerDefaultModel}
                                        className="flex-1 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-400 font-mono"
                                    />
                                    <button
                                        type="button"
                                        onClick={() => fetchCloudModels(true)}
                                        disabled={isLoadingCloudModels}
                                        className="mt-0 shrink-0 rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                                    >
                                        {isLoadingCloudModels ? "⟳" : "↻ Load models"}
                                    </button>
                                </div>
                            )}

                            {cloudModels.length > 0 && (
                                <p className="text-[11px] text-slate-500 mt-2">
                                    {cloudModels.length} cloud model{cloudModels.length !== 1 ? "s" : ""} available
                                    {config.local_models.length > 0 && ` + ${config.local_models.length} local`}
                                </p>
                            )}
                        </label>

                        {/* API key */}
                        <div className="pt-1">
                            <span className="text-xs text-slate-400">API Key</span>
                            <div className="mt-2 flex flex-col gap-3">
                                {secrets.configured ? (
                                    <div className="flex items-center justify-between rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-2.5">
                                        <span className="text-xs text-slate-400">
                                            <span className="inline-block w-2 h-2 rounded-full bg-green-500 mr-2"></span>
                                            Configured: <code className="font-mono text-slate-300">{secrets.api_key_masked}</code>
                                        </span>
                                        <button
                                            type="button"
                                            onClick={clearApiKey}
                                            disabled={isSaving}
                                            className="rounded-lg border border-red-800 px-3 py-1 text-xs font-medium text-red-400 hover:bg-red-900/30 disabled:opacity-50"
                                        >
                                            {isSaving ? "Clearing…" : "Clear"}
                                        </button>
                                    </div>
                                ) : (
                                    <div className="flex flex-col sm:flex-row gap-2">
                                        <input
                                            type="password"
                                            value={apiKeyInput}
                                            onChange={(e) => setApiKeyInput(e.target.value)}
                                            placeholder="sk-..."
                                            className="flex-1 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-400 font-mono"
                                            autoComplete="off"
                                        />
                                        <button
                                            type="button"
                                            onClick={saveApiKey}
                                            disabled={isSaving || !apiKeyInput.trim()}
                                            className="rounded-lg border border-slate-600 bg-slate-800 px-4 py-2 text-xs font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50"
                                        >
                                            {isSaving ? "Saving…" : "Save API Key"}
                                        </button>
                                    </div>
                                )}
                            </div>
                        </div>

                        {/* ── Cloud: Test Connection ── */}
                        <div className="pt-2">
                            <button
                                type="button"
                                onClick={async () => {
                                    setIsTesting(true);
                                    setTestResult(null);
                                    try {
                                        const res = await fetch("/api/admin/openai-test-connection", {
                                            method: "POST",
                                            headers: { "Content-Type": "application/json" },
                                            body: JSON.stringify({
                                                api_key: apiKeyInput || undefined,
                                                base_url: config.api_url || undefined,
                                                provider: currentProvider !== "custom" ? currentProvider : undefined,
                                            }),
                                        });
                                        const data = await res.json();
                                        if (data.reachable) {
                                            const modelCount = data.available_models?.length ?? 0;
                                            setTestResult({
                                                ok: true,
                                                message: `Connected ✅ — ${modelCount} model${modelCount !== 1 ? "s" : ""} available`
                                                    + (data.inference_tested ? " (inference verified)" : ""),
                                            });
                                        } else {
                                            setTestResult({
                                                ok: false,
                                                message: data.error || "Connection failed — check the URL and API key",
                                            });
                                        }
                                    } catch {
                                        setTestResult({ ok: false, message: "Network error — is the backend running?" });
                                    } finally {
                                        setIsTesting(false);
                                    }
                                }}
                                disabled={isTesting}
                                className="rounded-lg border border-slate-600 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                            >
                                {isTesting ? "Testing…" : "🔌 Test Connection"}
                            </button>
                            {testResult && (
                                <p className={`mt-2 text-[11px] ${testResult.ok ? "text-emerald-400" : "text-red-400"}`}>
                                    {testResult.message}
                                </p>
                            )}
                        </div>
                    </>
                ) : (
                    <>
                        {/* ── Local-specific: per-provider docs ──────── */}
                        <div className="rounded-lg border border-slate-700/50 bg-slate-900/60 px-4 py-3 text-[11px] text-slate-500 leading-relaxed">
                            <p className="font-medium text-slate-400 mb-1">
                                {currentProvider === "ollama"
                                    ? "Ollama"
                                    : currentProvider === "vllm"
                                        ? "vLLM"
                                        : currentProvider === "llama.cpp"
                                            ? "llama.cpp"
                                            : "Custom Local"}{" "}
                                endpoint
                            </p>
                            {currentProvider === "ollama" && (
                                <p>
                                    Uses Ollama's /api/generate endpoint. Make sure the Ollama server is running.
                                    Default port: <code className="font-mono text-slate-300">11434</code>.
                                </p>
                            )}
                            {currentProvider === "vllm" && (
                                <p>
                                    Uses vLLM's OpenAI-compatible /v1/completions endpoint.
                                    Default port: <code className="font-mono text-slate-300">8000</code>.
                                </p>
                            )}
                            {currentProvider === "llama.cpp" && (
                                <p>
                                    Uses llama.cpp server with an OpenAI-compatible API.
                                    Default port: <code className="font-mono text-slate-300">8080</code>.
                                </p>
                            )}
                            {currentProvider === "custom" && (
                                <p>
                                    Enter the full URL to your local inference server's API endpoint.
                                    Must start with <code className="font-mono text-slate-300">http://</code>.
                                </p>
                            )}
                        </div>

                        {/* Per-stage model overrides note (same for both modes) */}
                        <div className="rounded-lg border border-slate-700/50 bg-slate-900/60 px-4 py-3 text-[11px] text-slate-500 leading-relaxed">
                            <p className="font-medium text-slate-400 mb-1">Per-stage model overrides</p>
                            <p>
                                Use the <strong className="text-slate-300">Model Orchestration</strong> section to set separate models for Stage 1 (extraction) and Stage 2 (reasoning).
                                When those are set, they override the default model above.
                            </p>
                            <p className="mt-2">
                                Example: use <code className="font-mono text-slate-300">llama3.2:3b</code> for Stage 1 and <code className="font-mono text-slate-300">qwen3:9b</code> for Stage 2.
                            </p>
                        </div>
                    </>
                )}

                {/* ── Local: Test Connection ─────────────────────────── */}
                {!isCloudMode && (
                    <div className="pt-2">
                        <button
                            type="button"
                            onClick={async () => {
                                setIsTesting(true);
                                setTestResult(null);
                                try {
                                    const res = await fetch("/api/ollama/status", { cache: "no-store" });
                                    const data = await res.json();
                                    if (data.reachable) {
                                        const modelCount = data.available_models?.length ?? 0;
                                        const activeModel = data.active_model || "";
                                        setTestResult({
                                            ok: true,
                                            message: `Connected ✅ — ${modelCount} model${modelCount !== 1 ? "s" : ""} available`
                                                + (activeModel ? ` (active: ${activeModel})` : ""),
                                        });
                                    } else {
                                        setTestResult({
                                            ok: false,
                                            message: data.error || "Connection failed — is the local server running?",
                                        });
                                    }
                                } catch {
                                    setTestResult({ ok: false, message: "Network error — is the backend running?" });
                                } finally {
                                    setIsTesting(false);
                                }
                            }}
                            disabled={isTesting}
                            className="rounded-lg border border-slate-600 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                        >
                            {isTesting ? "Testing…" : "🔌 Test Connection"}
                        </button>
                        {testResult && (
                            <p className={`mt-2 text-[11px] ${testResult.ok ? "text-emerald-400" : "text-red-400"}`}>
                                {testResult.message}
                            </p>
                        )}
                    </div>
                )}

                {/* ── Advanced: env var fallback note ────────────────── */}
                {isAdvancedMode && isCloudMode && (
                    <div className="rounded-lg border border-slate-700/50 bg-slate-900/60 px-4 py-3 text-[11px] text-slate-500 leading-relaxed">
                        <p className="font-medium text-slate-400 mb-1">Environment variable fallback</p>
                        <p>
                            The API key is looked up from the OS keychain first, then falls back to
                            the <code className="font-mono text-slate-300">OPENAI_API_KEY</code> environment variable.
                            Base URL and model can also be set via <code className="font-mono text-slate-300">OPENAI_BASE_URL</code> and{' '}
                            <code className="font-mono text-slate-300">OPENAI_MODEL</code>.
                        </p>
                    </div>
                )}

                {isAdvancedMode && !isCloudMode && (
                    <div className="rounded-lg border border-slate-700/50 bg-slate-900/60 px-4 py-3 text-[11px] text-slate-500 leading-relaxed">
                        <p className="font-medium text-slate-400 mb-1">Environment variable fallback</p>
                        <p>
                            URL can also be set via{' '}
                            <code className="font-mono text-slate-300">{currentProvider === "ollama" ? "OLLAMA_URL" : "VLLM_URL"}</code>.
                            When the admin field is empty, the env var is used, then the hardcoded default.
                        </p>
                    </div>
                )}

                {/* Status */}
                {status && (
                    <div className="rounded-lg bg-slate-800 px-3 py-2 text-xs text-slate-300">
                        {status}
                    </div>
                )}
            </div>
        </section>
    );
}