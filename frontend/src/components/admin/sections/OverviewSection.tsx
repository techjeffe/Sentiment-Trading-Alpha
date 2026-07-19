"use client";

import { useMemo } from "react";
import { AppConfig } from "@/lib/utils/config-normalizer";

type DepthOption = {
    key: AppConfig["rss_article_detail_mode"];
    label: string;
    tagline: string;
    pipeline: string;
};

type RiskOption = {
    key: string;
    label: string;
    tagline: string;
    description: string;
    maxLeverage: string;
    color: string;
};

type OverviewSectionProps = {
    config: AppConfig;
    setConfig: React.Dispatch<React.SetStateAction<AppConfig>>;
    isAdvancedMode: boolean;
    riskOptions: RiskOption[];
    depthOptions: DepthOption[];
    onSelectRiskProfile: (profile: string) => void;
};

const activeColorStyles: Record<string, string> = {
    blue: "border-blue-400 bg-blue-500/10 text-blue-100",
    teal: "border-teal-400 bg-teal-500/10 text-teal-100",
    amber: "border-amber-400 bg-amber-500/10 text-amber-100",
    rose: "border-rose-400 bg-rose-500/10 text-rose-100",
};

function buildModelOptions(local: string[], cloud: string[]): { value: string; label: string }[] {
    const seen = new Set<string>();
    const result: { value: string; label: string }[] = [];
    for (const m of local) {
        if (!seen.has(m)) { seen.add(m); result.push({ value: m, label: `(local) ${m}` }); }
    }
    for (const m of cloud) {
        if (!seen.has(m)) { seen.add(m); result.push({ value: m, label: `(cloud) ${m}` }); }
    }
    return result;
}

function modelLabel(model: string, options: { value: string; label: string }[]): string {
    const found = options.find((m) => m.value === model);
    return found ? found.label : model;
}

export function OverviewSection({ config, setConfig, isAdvancedMode, riskOptions, depthOptions, onSelectRiskProfile }: OverviewSectionProps) {
    const modelOptions = useMemo(() => buildModelOptions(config.local_models, config.cloud_models), [config.local_models, config.cloud_models]);
    const hasModels = modelOptions.length > 0;

    return (
        <section id="overview" className="scroll-mt-24 rounded-2xl border border-slate-800 bg-slate-900/70 p-5 space-y-5">

            {/* 1. Risk profile — TOP */}
            <div>
                <p className="text-xs text-slate-400 mb-3">Risk profile & leverage</p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    {riskOptions.map((option) => {
                        const isActive = (config.risk_profile || "standard") === option.key;
                        return (
                            <button
                                key={option.key}
                                type="button"
                                onClick={() => onSelectRiskProfile(option.key)}
                                className={`rounded-xl border px-4 py-3 text-left transition-colors ${isActive
                                    ? activeColorStyles[option.color]
                                    : "border-slate-800 bg-slate-950/60 text-slate-300"
                                    }`}
                            >
                                <p className="text-sm font-semibold">{option.label}</p>
                                <p className="mt-0.5 text-[11px] text-slate-400 font-medium">{option.tagline}</p>
                                <p className="mt-1.5 text-[11px] text-slate-500">{option.description}</p>
                                <p className="mt-2 text-[10px] font-mono text-slate-600">{option.maxLeverage}</p>
                            </button>
                        );
                    })}
                </div>
            </div>

            {/* 2. Analysis depth */}
            <div>
                <p className="text-xs text-slate-400 mb-3">Analysis depth & pipeline mode</p>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    {depthOptions.map((option) => (
                        <button
                            key={option.key}
                            type="button"
                            onClick={() => setConfig((c) => ({ ...c, rss_article_detail_mode: option.key }))}
                            className={`rounded-xl border px-4 py-3 text-left transition-colors ${config.rss_article_detail_mode === option.key
                                ? "border-blue-400 bg-blue-500/10 text-blue-100"
                                : "border-slate-800 bg-slate-950/60 text-slate-300"
                                }`}
                        >
                            <p className="text-sm font-semibold">{option.label}</p>
                            <p className="mt-0.5 text-[11px] text-slate-400 font-medium">{option.tagline}</p>
                            <p className="mt-1.5 text-[11px] text-slate-500">{option.pipeline}</p>
                        </button>
                    ))}
                </div>
            </div>

            {/* 3. Model selection */}
            <div>
                <p className="text-xs text-slate-400 mb-1">Model selection</p>
                <p className="text-[11px] text-slate-600 mb-3">Follows the depth setting above.</p>
                {hasModels ? (
                    config.rss_article_detail_mode === "light" ? (
                        <div className="space-y-3">
                            <p className="text-xs text-slate-500">
                                Light mode uses a single model for both entity mapping (Stage 1) and financial reasoning (Stage 2).
                            </p>
                            <label className="block">
                                <span className="text-xs text-slate-400">Analysis Model</span>
                                <select
                                    value={config.extraction_model}
                                    onChange={(e) => setConfig((c) => ({ ...c, extraction_model: e.target.value, reasoning_model: "" }))}
                                    className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-400"
                                >
                                    <option value="">— use active model —</option>
                                    {modelOptions.map((opt) => (
                                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                                    ))}
                                </select>
                            </label>
                            {config.extraction_model && (
                                <div className="rounded-xl border border-slate-700/50 bg-slate-950/60 px-4 py-3 text-xs text-slate-400 space-y-0.5">
                                    <p><span className="text-slate-500">Stage 1 (entity mapping) — </span>{modelLabel(config.extraction_model, modelOptions)}</p>
                                    <p><span className="text-slate-500">Stage 2 (reasoning) — </span>{modelLabel(config.extraction_model, modelOptions)}</p>
                                </div>
                            )}
                        </div>
                    ) : config.rss_article_detail_mode === "detailed" ? (
                        <div className="space-y-4">
                            <p className="text-xs text-slate-500">
                                Detailed mode always runs the full two-stage pipeline. Both models are required.
                                Use a fast small model for Stage 1 and your best reasoning model for Stage 2.
                            </p>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                                <label className="block">
                                    <span className="text-xs text-slate-400">
                                        Stage 1 — Extraction Model
                                        {!config.extraction_model && <span className="ml-2 text-amber-400">required</span>}
                                    </span>
                                    <p className="text-[11px] text-slate-600 mt-0.5">Entity mapping & article filtering (e.g. llama3.2:3b)</p>
                                    <select
                                        value={config.extraction_model}
                                        onChange={(e) => setConfig((c) => ({ ...c, extraction_model: e.target.value }))}
                                        className={`mt-2 w-full rounded-lg border px-3 py-2 text-sm text-white outline-none focus:border-blue-400 bg-slate-800 ${!config.extraction_model ? "border-amber-700/60" : "border-slate-700"}`}
                                    >
                                        <option value="">— choose a model —</option>
                                        {modelOptions.map((opt) => (
                                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                                        ))}
                                    </select>
                                </label>
                                <label className="block">
                                    <span className="text-xs text-slate-400">
                                        Stage 2 — Reasoning Model
                                        {!config.reasoning_model && <span className="ml-2 text-amber-400">required</span>}
                                    </span>
                                    <p className="text-[11px] text-slate-600 mt-0.5">Financial signal generation (e.g. qwen3:9b)</p>
                                    <select
                                        value={config.reasoning_model}
                                        onChange={(e) => setConfig((c) => ({ ...c, reasoning_model: e.target.value }))}
                                        className={`mt-2 w-full rounded-lg border px-3 py-2 text-sm text-white outline-none focus:border-blue-400 bg-slate-800 ${!config.reasoning_model ? "border-amber-700/60" : "border-slate-700"}`}
                                    >
                                        <option value="">— choose a model —</option>
                                        {modelOptions.map((opt) => (
                                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                                        ))}
                                    </select>
                                </label>
                            </div>
                            {config.extraction_model && config.reasoning_model && (
                                <div className="rounded-xl border border-blue-800/40 bg-blue-500/5 px-4 py-3 text-xs text-slate-300 space-y-0.5">
                                    <p className="font-semibold text-blue-300 mb-1">Two-stage pipeline ready</p>
                                    <p><span className="text-slate-500">Stage 1 — </span>{modelLabel(config.extraction_model, modelOptions)}</p>
                                    <p><span className="text-slate-500">Stage 2 — </span>{modelLabel(config.reasoning_model, modelOptions)}</p>
                                </div>
                            )}
                        </div>
                    ) : (
                        /* Normal — two models optional */
                        <div className="space-y-4">
                            <p className="text-xs text-slate-500">
                                Normal mode runs two-stage when both models are set, single-stage otherwise.
                                Leave blank to use whichever Ollama model is currently active.
                            </p>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                                <label className="block">
                                    <span className="text-xs text-slate-400">Stage 1 — Extraction Model <span className="text-slate-600">(optional)</span></span>
                                    <p className="text-[11px] text-slate-600 mt-0.5">Entity mapping & article filtering (e.g. llama3.2:3b)</p>
                                    <select
                                        value={config.extraction_model}
                                        onChange={(e) => setConfig((c) => ({ ...c, extraction_model: e.target.value }))}
                                        className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-400"
                                    >
                                        <option value="">— use active model —</option>
                                        {modelOptions.map((opt) => (
                                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                                        ))}
                                    </select>
                                </label>
                                <label className="block">
                                    <span className="text-xs text-slate-400">Stage 2 — Reasoning Model <span className="text-slate-600">(optional)</span></span>
                                    <p className="text-[11px] text-slate-600 mt-0.5">Financial signal generation (e.g. qwen3:9b)</p>
                                    <select
                                        value={config.reasoning_model}
                                        onChange={(e) => setConfig((c) => ({ ...c, reasoning_model: e.target.value }))}
                                        className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-400"
                                    >
                                        <option value="">— use active model —</option>
                                        {modelOptions.map((opt) => (
                                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                                        ))}
                                    </select>
                                </label>
                            </div>
                            {(config.extraction_model || config.reasoning_model) && (
                                <div className="rounded-xl border border-slate-700/50 bg-slate-950/60 px-4 py-3 text-xs text-slate-400 space-y-0.5">
                                    {config.extraction_model && config.reasoning_model ? (
                                        <>
                                            <p className="font-semibold text-blue-300 mb-1">Two-stage pipeline active</p>
                                            <p><span className="text-slate-500">Stage 1 — </span>{modelLabel(config.extraction_model, modelOptions)}</p>
                                            <p><span className="text-slate-500">Stage 2 — </span>{modelLabel(config.reasoning_model, modelOptions)}</p>
                                        </>
                                    ) : (
                                        <p className="text-amber-400">Single-stage mode — set both models to enable two-stage pipeline.</p>
                                    )}
                                </div>
                            )}
                        </div>
                    )
                ) : (
                    <p className="text-xs text-amber-400 italic">No Ollama models detected — make sure Ollama is running.</p>
                )}
            </div>

            {/* 4. Red-team — always visible */}
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
                <label className="flex items-start gap-3 cursor-pointer">
                    <input
                        type="checkbox"
                        checked={config.red_team_enabled}
                        onChange={(e) => setConfig((c) => ({ ...c, red_team_enabled: e.target.checked }))}
                        className="mt-1 h-4 w-4 rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-blue-500"
                    />
                    <span className="block">
                        <span className="text-sm font-semibold text-slate-200">Red-team risk review</span>
                        <span className="block mt-1 text-xs text-slate-400 leading-relaxed">
                            Adversarial pass that re-reads the blue-team signal looking for bias, source skew, and overlooked risks.
                            Disabling saves one Ollama call per analysis (~30–60s on a slow box) at the cost of the bias countercheck.
                        </span>
                    </span>
                </label>
            </div>

            {/* 6. Advanced: article volume */}
            {isAdvancedMode && (
                <div>
                    <p className="text-xs text-slate-400 mb-1">Article volume</p>
                    <p className="text-[11px] text-slate-500 mb-3">
                        Max posts ingested per analysis. Lower = faster Stage 2. Higher = broader signal coverage.
                    </p>
                    <div className="grid grid-cols-4 gap-2">
                        {[5, 15, 25, 50].map((n) => (
                            <button
                                key={n}
                                type="button"
                                onClick={() => setConfig((c) => ({ ...c, max_posts: n }))}
                                className={`rounded-lg border px-3 py-2 text-sm transition-colors ${config.max_posts === n
                                    ? "border-blue-400 bg-blue-500/10 text-blue-100 font-semibold"
                                    : "border-slate-800 bg-slate-950/60 text-slate-300 hover:border-slate-700"
                                    }`}
                            >
                                {n}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* 7. Advanced: parallel Stage 2 slots */}
            {isAdvancedMode && (
                <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
                    <label className="block">
                        <span className="text-sm font-semibold text-slate-200">Parallel Stage 2 slots</span>
                        <p className="mt-1 text-xs text-slate-400 leading-relaxed">
                            Number of Stage 2 specialist calls that may run concurrently. <span className="font-semibold text-slate-200">1</span> = serialized (safe default).
                        </p>
                        <input
                            type="number"
                            min={1}
                            max={8}
                            value={config.ollama_parallel_slots}
                            onChange={(e) => setConfig((c) => ({ ...c, ollama_parallel_slots: Math.max(1, Math.min(8, parseInt(e.target.value) || 1)) }))}
                            className="mt-3 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-400"
                        />
                        {config.ollama_parallel_slots > 1 && (
                            <p className="mt-2 text-[11px] text-amber-400 leading-relaxed">
                                ⚠ For local Ollama: requires GPU VRAM headroom AND <code className="font-mono text-amber-300">OLLAMA_NUM_PARALLEL={config.ollama_parallel_slots}</code> set on the Ollama side. For cloud APIs (OpenRouter, OpenAI): safe to increase to 4-8x for faster analysis.
                            </p>
                        )}
                    </label>
                </div>
            )}
        </section>
    );
}
