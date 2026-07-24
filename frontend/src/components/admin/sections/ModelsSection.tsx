"use client";

import { useMemo } from "react";
import { AppConfig } from "@/lib/utils/config-normalizer";

type ModelsSectionProps = {
    config: AppConfig;
    setConfig: React.Dispatch<React.SetStateAction<AppConfig>>;
    hasAdvancedCustomizations: boolean;
    depthOptions: Array<{
        key: AppConfig["rss_article_detail_mode"];
        label: string;
        tagline: string;
        pipeline: string;
    }>;
};

/** Build a display label for a model, tagging with (local) or (cloud) origin. */
function modelLabel(model: string, allModels: { value: string; label: string }[]): string {
    const found = allModels.find((m) => m.value === model);
    return found ? found.label : model;
}

export function ModelsSection({ config, setConfig, hasAdvancedCustomizations, depthOptions }: ModelsSectionProps) {
    const localSet = useMemo(() => new Set(config.local_models), [config.local_models]);
    const cloudSet = useMemo(() => new Set(config.cloud_models), [config.cloud_models]);

    // Build labeled options: local first, then cloud, deduplicated by model ID
    const modelOptions = useMemo(() => {
        const seen = new Set<string>();
        const result: { value: string; label: string }[] = [];
        for (const m of config.local_models) {
            if (!seen.has(m)) {
                seen.add(m);
                result.push({ value: m, label: `(local) ${m}` });
            }
        }
        for (const m of config.cloud_models) {
            if (!seen.has(m)) {
                seen.add(m);
                result.push({ value: m, label: `(cloud) ${m}` });
            }
        }
        return result;
    }, [config.local_models, config.cloud_models]);

    const hasModels = modelOptions.length > 0 || config.available_models.length > 0;
    const hasLocal = config.local_models.length > 0;
    const hasCloud = config.cloud_models.length > 0;
    const isVllm = config.inference_backend === "vllm";
    const isOllama = config.inference_backend === "ollama";
    const isOpenai = config.inference_backend === "openai";
    const isOmlx = config.inference_backend === "omlx";

    // For Ollama/vLLM/omlx: show flag if no models found, for Cloud: show note to configure in LLM section
    const noModelsMessage = isOpenai
        ? "Configure a Cloud LLM API key in the LLM Configuration section above to see cloud models."
        : isVllm
            ? "No vLLM models detected — make sure vLLM is running and VLLM_URL is set correctly."
            : isOmlx
                ? "No OMLX models detected — make sure omlx is running on the configured URL."
                : "No Ollama models detected — make sure Ollama is running.";

    return (
        <section id="models" className="scroll-mt-24 rounded-2xl border border-slate-800 bg-slate-900/70 p-5 space-y-5">
            <div>
                <h2 className="text-sm font-semibold text-slate-200">Model Orchestration</h2>
                <p className="text-xs text-slate-500 mt-1">
                    Model selection follows the depth setting chosen above.
                </p>
            </div>

            {/* Model source indicator */}
            <div className="flex flex-wrap gap-3 text-[11px]">
                {hasLocal && (
                    <span className="inline-flex items-center gap-1 px-2 py-1 rounded bg-blue-900/40 text-blue-300 border border-blue-800/50">
                        <span className="w-1.5 h-1.5 rounded-full bg-blue-400" />
                        Local models: {config.local_models.length}
                    </span>
                )}
                {hasCloud && (
                    <span className="inline-flex items-center gap-1 px-2 py-1 rounded bg-emerald-900/40 text-emerald-300 border border-emerald-800/50">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                        Cloud models: {config.cloud_models.length}
                    </span>
                )}
                {!hasLocal && !hasCloud && (
                    <span className="text-amber-400 italic text-xs">{noModelsMessage}</span>
                )}
            </div>

            {hasModels ? (
                config.rss_article_detail_mode === "light" ? (
                    /* Light — one model for both stages */
                    <div className="space-y-3">
                        <p className="text-xs text-slate-400">
                            Light mode uses a single model for both entity mapping (Stage 1) and financial reasoning (Stage 2).
                            Pick a fast, small model for best throughput.
                        </p>
                        <label className="block">
                            <span className="text-xs text-slate-400">Analysis Model</span>
                            <select
                                value={config.extraction_model}
                                onChange={(e) => setConfig((c) => ({ ...c, extraction_model: e.target.value, reasoning_model: "" }))}
                                className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-blue-400"
                            >
                                <option value="">— use active Ollama model —</option>
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
                    /* Detailed — two models required */
                    <div className="space-y-4">
                        <p className="text-xs text-slate-400">
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
                        <p className="text-xs text-slate-400">
                            Normal mode runs two-stage when both models are set, single-stage otherwise.
                            Leave blank to use whichever model is currently active.
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
                <p className="text-xs text-amber-400 italic">
                    {isVllm
                        ? "No vLLM models detected — make sure vLLM is running and VLLM_URL is set correctly."
                        : isOpenai
                            ? "Configure a Cloud LLM API key in the LLM Configuration section above to see cloud models."
                            : "No Ollama models detected — make sure Ollama is running."}
                </p>
            )}
        </section>
    );
}