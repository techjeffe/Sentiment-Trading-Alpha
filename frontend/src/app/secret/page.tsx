"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

type SecretPayload = {
    request_id: string;
    timestamp: string | null;
    model_name: string;
    risk_profile: string;
    processing_time_ms: number;
    signal: Record<string, any>;
    sentiment_data: Record<string, any>;
    dataset_snapshot: Record<string, any>;
    secret_trace: Record<string, any>;
};

function StepPanel({
    step,
    title,
    children,
}: {
    step: string;
    title: string;
    children: React.ReactNode;
}) {
    return (
        <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
            <div className="flex items-center gap-3">
                <span className="rounded-full border border-blue-500/30 bg-blue-500/10 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-blue-300">
                    {step}
                </span>
                <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
            </div>
            <div className="mt-4">{children}</div>
        </section>
    );
}

function JsonBlock({ value }: { value: unknown }) {
    return (
        <pre className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/80 p-4 text-xs leading-6 text-slate-300">
            {JSON.stringify(value, null, 2)}
        </pre>
    );
}

export default function SecretPage() {
    const [data, setData] = useState<SecretPayload | null>(null);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const load = async () => {
            try {
                const response = await fetch("/api/secret/latest", { cache: "no-store" });
                const payload = await response.json();
                if (!response.ok) {
                    throw new Error(payload?.error || "Failed to load");
                }
                setData(payload);
            } catch (err: any) {
                setError(err?.message || "Failed to load latest debug run");
            } finally {
                setLoading(false);
            }
        };
        void load();
    }, []);

    const stage1Articles = useMemo(
        () => (data?.secret_trace?.sentiment?.stage_trace?.stage1?.articles || []) as Array<Record<string, any>>,
        [data]
    );
    const stage1KeywordTrace = useMemo(
        () => (data?.secret_trace?.sentiment?.stage_trace?.stage1?.keyword_generation_trace_by_symbol || {}) as Record<string, any>,
        [data]
    );
    const stage2Runs = useMemo(
        () => (data?.secret_trace?.sentiment?.stage_trace?.stage2_runs_by_symbol || {}) as Record<string, any>,
        [data]
    );

    if (loading) {
        return <main className="min-h-screen bg-slate-950 px-6 py-10 text-slate-200">Loading last-run diagnostics...</main>;
    }

    if (error || !data) {
        return <main className="min-h-screen bg-slate-950 px-6 py-10 text-red-300">{error || "No diagnostics found"}</main>;
    }

    const secretTrace = data.secret_trace || {};
    const ingestion = secretTrace.ingestion || {};
    const rssFeeds = ingestion?.rss?.feeds || [];
    const webResearch = secretTrace.web_research || {};
    const sentiment = secretTrace.sentiment || {};
    const symbolResults = sentiment.symbol_results || {};
    const blueTeamSignal = secretTrace.blue_team_signal || {};
    const redTeamReview = secretTrace.red_team_review || {};
    const redTeamDebug = secretTrace.red_team_debug || {};

    return (
        <main className="min-h-screen bg-slate-950 px-6 py-10 text-slate-100">
            <div className="mx-auto max-w-5xl space-y-6">
                <div className="flex items-start gap-4">
                    <div>
                        <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Secret Diagnostics</p>
                        <h1 className="mt-2 text-3xl font-black text-white">Last Run Forensics</h1>
                        <p className="mt-2 text-sm text-slate-400">
                            Request `{data.request_id}` · {data.model_name || "Unknown model"} · {(data.processing_time_ms / 1000).toFixed(2)}s
                        </p>
                        <p className="mt-1 text-xs text-slate-500">
                            Run occurred: {data.timestamp ? new Date(data.timestamp).toLocaleString("en-US", { timeZoneName: "short" }) : "Unknown"}
                        </p>
                    </div>
                </div>

                <StepPanel step="Step 0" title="Pipeline Events">
                    <div className="space-y-2">
                        {(secretTrace.pipeline_events || []).map((event: string, index: number) => (
                            <div key={`${index}-${event}`} className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-sm text-slate-300">
                                {event}
                            </div>
                        ))}
                    </div>
                </StepPanel>

                <StepPanel step="Step 1" title="Request Inputs">
                    <JsonBlock value={{ request: secretTrace.request, models: secretTrace.models }} />
                </StepPanel>

                <StepPanel step="Step 2" title="Ingestion Trace">
                    <div className="space-y-4">
                        <JsonBlock value={{ truth_social: ingestion.truth_social, rss_summary: ingestion.rss, total_items: ingestion.total_items }} />
                        <div className="space-y-4">
                            {rssFeeds.map((feed: Record<string, any>, index: number) => (
                                <div key={`${feed.feed_key || index}-${index}`} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                                    <div className="flex items-center justify-between gap-3">
                                        <div>
                                            <p className="text-sm font-semibold text-slate-200">{feed.feed_key || `Feed ${index + 1}`}</p>
                                            <p className="text-xs text-slate-500 break-all">{feed.feed_url || "No URL recorded"}</p>
                                        </div>
                                        <span className="text-xs text-slate-400">{feed.count || 0} pulled</span>
                                    </div>
                                    <div className="mt-3 space-y-2">
                                        {(feed.articles || []).map((article: Record<string, any>, articleIndex: number) => (
                                            <div key={`${article.title || articleIndex}-${articleIndex}`} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
                                                <p className="text-sm text-slate-100">{article.title || "Untitled"}</p>
                                                <p className="mt-1 text-xs text-slate-500">{article.source || "Unknown source"}</p>
                                                <p className="mt-2 text-xs text-slate-400">{article.summary || "No summary saved"}</p>
                                                {!!article.keywords?.length && (
                                                    <div className="mt-2 flex flex-wrap gap-1.5">
                                                        {article.keywords.map((keyword: string, keywordIndex: number) => (
                                                            <span key={`${keyword}-${keywordIndex}`} className="rounded border border-blue-500/20 bg-blue-500/10 px-2 py-0.5 text-[10px] text-blue-200">
                                                                {keyword}
                                                            </span>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </StepPanel>

                <StepPanel step="Step 3" title="Web Research Inputs">
                    <JsonBlock value={webResearch} />
                </StepPanel>

                <StepPanel step="Step 4" title="Stage 1 Keyword Matching">
                    <div className="space-y-4">
                        <JsonBlock value={sentiment?.stage_trace?.stage1?.proxy_terms_by_symbol || {}} />
                        <div className="space-y-4">
                            {Object.entries(stage1KeywordTrace).map(([symbol, trace]) => (
                                <div key={symbol} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                                    <div className="flex items-center justify-between gap-3">
                                        <p className="text-sm font-semibold text-slate-100">{symbol} · Stage 1 prompt/output</p>
                                        <span className="text-[10px] uppercase text-slate-500">
                                            {String((trace as any)?.mode || "unknown")}
                                            {(trace as any)?.cache_hit ? " · cache" : ""}
                                        </span>
                                    </div>
                                    <div className="mt-3 space-y-4">
                                        <div>
                                            <p className="mb-2 text-xs uppercase tracking-[0.2em] text-slate-500">Prompt</p>
                                            <JsonBlock value={(trace as any)?.prompt || "No Stage 1 prompt recorded for this symbol."} />
                                        </div>
                                        <div>
                                            <p className="mb-2 text-xs uppercase tracking-[0.2em] text-slate-500">Output</p>
                                            <JsonBlock value={{ terms: (trace as any)?.terms || [], raw_response: (trace as any)?.raw_response || "", error: (trace as any)?.error || null }} />
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                        <div className="space-y-3">
                            {stage1Articles.map((article, index) => (
                                <div key={`${article.title || index}-${index}`} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                                    <div className="flex items-start justify-between gap-3">
                                        <div>
                                            <p className="text-sm font-semibold text-slate-100">{article.title || "Untitled"}</p>
                                            <p className="mt-1 text-xs text-slate-500">{article.source || "Unknown source"}</p>
                                        </div>
                                        <span className={`rounded px-2 py-1 text-[10px] uppercase ${article.selected_for_reasoning ? "bg-emerald-500/15 text-emerald-300" : "bg-slate-800 text-slate-400"}`}>
                                            {article.selected_for_reasoning ? "Used" : "Skipped"}
                                        </span>
                                    </div>
                                    <div className="mt-3 space-y-3 text-xs">
                                        <JsonBlock value={{ matched_symbols: article.matched_symbols, matched_terms_by_symbol: article.matched_terms_by_symbol }} />
                                        <JsonBlock value={{ rss_keywords: article.keywords, summary: article.summary }} />
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </StepPanel>

                <StepPanel step="Step 5" title="Stage 2 Specialist Prompt Flow">
                    <div className="space-y-4">
                        {Object.entries(stage2Runs).map(([symbol, run]) => (
                            <div key={symbol} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <p className="text-sm font-semibold text-slate-100">{symbol} · Stage 2</p>
                                        <p className="mt-1 text-xs text-slate-500">
                                            Model {(run as any)?.model || "unknown"} · Signal {(run as any)?.signal_type || "-"} · Confidence {typeof (run as any)?.confidence === "number" ? `${Math.round((run as any).confidence * 100)}%` : "-"}
                                        </p>
                                    </div>
                                </div>
                                <div className="mt-4 space-y-4">
                                    <div>
                                        <p className="mb-2 text-xs uppercase tracking-[0.2em] text-slate-500">Prompt</p>
                                        <JsonBlock value={(run as any)?.prompt || ""} />
                                    </div>
                                    <div>
                                        <p className="mb-2 text-xs uppercase tracking-[0.2em] text-slate-500">Raw Model Response</p>
                                        <JsonBlock value={(run as any)?.raw_response || ""} />
                                    </div>
                                    <div>
                                        <p className="mb-2 text-xs uppercase tracking-[0.2em] text-slate-500">Parsed Payload</p>
                                        <JsonBlock value={(run as any)?.parsed_payload || {}} />
                                    </div>
                                    <div>
                                        <p className="mb-2 text-xs uppercase tracking-[0.2em] text-slate-500">Final Reasoning</p>
                                        <JsonBlock value={(run as any)?.final_reasoning || ""} />
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </StepPanel>

                <StepPanel step="Step 6" title="Sentiment Results">
                    <JsonBlock value={symbolResults} />
                </StepPanel>

                <StepPanel step="Step 7" title="Blue Team Signal Output">
                    <JsonBlock value={blueTeamSignal} />
                </StepPanel>

                <StepPanel step="Step 8" title="Final Consensus Signal Output">
                    <JsonBlock value={data.signal} />
                </StepPanel>

                <StepPanel step="Step 9" title="Red Team Review">
                    <div className="space-y-4">
                        <JsonBlock value={redTeamReview} />
                        {!!redTeamReview?.summary && (
                            <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4">
                                <p className="text-xs uppercase tracking-[0.2em] text-amber-300">Summary</p>
                                <p className="mt-2 text-sm text-slate-200">{String(redTeamReview.summary)}</p>
                                {Array.isArray(redTeamReview.portfolio_risks) && redTeamReview.portfolio_risks.length > 0 && (
                                    <div className="mt-3 space-y-2">
                                        {redTeamReview.portfolio_risks.map((risk: string, index: number) => (
                                            <div key={`${index}-${risk}`} className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-sm text-slate-300">
                                                {risk}
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}
                        {Array.isArray(redTeamReview?.symbol_reviews) && redTeamReview.symbol_reviews.length > 0 && (
                            <div className="space-y-4">
                                {redTeamReview.symbol_reviews.map((review: Record<string, any>, index: number) => (
                                    <div key={`${review.symbol || "symbol"}-${index}`} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                                        <div className="flex items-start justify-between gap-4">
                                            <div>
                                                <p className="text-sm font-semibold text-slate-100">{review.symbol || "Unknown symbol"}</p>
                                                <p className="mt-1 text-xs text-slate-500">{review.current_recommendation || "No current recommendation recorded"}</p>
                                            </div>
                                            <div className="text-right">
                                                <p className="text-xs uppercase tracking-[0.2em] text-amber-300">{review.adjusted_signal || "HOLD"}</p>
                                                <p className="mt-1 text-xs text-slate-400">
                                                    {typeof review.adjusted_confidence === "number" ? `${Math.round(review.adjusted_confidence * 100)}%` : "-"} · {review.adjusted_urgency || "LOW"}
                                                </p>
                                            </div>
                                        </div>
                                        <div className="mt-4 grid gap-4 md:grid-cols-2">
                                            <div>
                                                <p className="mb-2 text-xs uppercase tracking-[0.2em] text-emerald-300">Thesis</p>
                                                <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-sm text-slate-300">
                                                    {review.thesis || "No thesis captured"}
                                                </div>
                                            </div>
                                            <div>
                                                <p className="mb-2 text-xs uppercase tracking-[0.2em] text-red-300">Antithesis</p>
                                                <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-sm text-slate-300">
                                                    {review.antithesis || "No antithesis captured"}
                                                </div>
                                            </div>
                                        </div>
                                        <div className="mt-4 grid gap-4 md:grid-cols-2">
                                            <div>
                                                <p className="mb-2 text-xs uppercase tracking-[0.2em] text-blue-300">Evidence</p>
                                                <JsonBlock value={review.evidence || []} />
                                            </div>
                                            <div>
                                                <p className="mb-2 text-xs uppercase tracking-[0.2em] text-slate-500">Key Risks</p>
                                                <JsonBlock value={review.key_risks || []} />
                                            </div>
                                            <div>
                                                <p className="mb-2 text-xs uppercase tracking-[0.2em] text-slate-500">ATR / Stop Basis</p>
                                                <JsonBlock value={{ stop_loss_pct: review.stop_loss_pct, atr_basis: review.atr_basis, rationale: review.rationale }} />
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                        {!!redTeamDebug?.signal_changes?.length && (
                            <div className="rounded-xl border border-blue-500/20 bg-blue-500/5 p-4">
                                <p className="text-xs uppercase tracking-[0.2em] text-blue-300">Blue vs Consensus Changes</p>
                                <div className="mt-3 space-y-3">
                                    {redTeamDebug.signal_changes.map((change: Record<string, any>, index: number) => (
                                        <div key={`${change.symbol || index}-${index}`} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                                            <div className="flex items-start justify-between gap-3">
                                                <div>
                                                    <p className="text-sm font-semibold text-slate-100">{change.symbol || "Unknown symbol"}</p>
                                                    <p className="mt-1 text-xs text-slate-400">
                                                        {change.blue_team_recommendation || "No recommendation"} {"->"} {change.consensus_recommendation || "No recommendation"}
                                                    </p>
                                                </div>
                                                <span className="text-[10px] uppercase tracking-[0.2em] text-blue-300">{change.change_type || "unchanged"}</span>
                                            </div>
                                            {!!change.rationale && (
                                                <p className="mt-2 text-sm text-slate-300">{change.rationale}</p>
                                            )}
                                            {!!change.evidence?.length && (
                                                <div className="mt-2">
                                                    <JsonBlock value={change.evidence} />
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                        {!!redTeamDebug?.prompt && (
                            <div className="space-y-4">
                                <div>
                                    <p className="mb-2 text-xs uppercase tracking-[0.2em] text-slate-500">Prompt</p>
                                    <JsonBlock value={redTeamDebug.prompt} />
                                </div>
                                <div>
                                    <p className="mb-2 text-xs uppercase tracking-[0.2em] text-slate-500">Raw Model Response</p>
                                    <JsonBlock value={redTeamDebug.raw_response || ""} />
                                </div>
                            </div>
                        )}
                    </div>
                </StepPanel>

                <StepPanel step="Step 10" title="Raw Sentiment Payload">
                    <JsonBlock value={data.sentiment_data} />
                </StepPanel>

                <StepPanel step="Step 11" title="Dataset Snapshot">
                    <JsonBlock value={data.dataset_snapshot} />
                </StepPanel>
            </div>
        </main>
    );
}
