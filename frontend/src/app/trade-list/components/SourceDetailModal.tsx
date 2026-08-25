"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { X, ExternalLink, Newspaper, FileText, Info } from "lucide-react";

interface SourceItem {
  title: string;
  url: string | null;
  published_at: string | null;
  source_label: string;
  summary: string;
  form_type?: string;
}

interface SourceDetail {
  name: string;
  label: string;
  description: string;
  weight: number;
  items_found: number;
  items: SourceItem[];
}

interface SourcesResponse {
  symbol: string;
  score: number;
  source_count: number;
  signal_count: number;
  sources: SourceDetail[];
}

interface SourceDetailModalProps {
  opportunityId: number;
  symbol: string;
  onClose: () => void;
}

export function SourceDetailModal({ opportunityId, symbol, onClose }: SourceDetailModalProps) {
  const [data, setData] = useState<SourcesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`/api/v1/trade-list/${opportunityId}/sources`);
        if (!res.ok) throw new Error(`API error: ${res.status}`);
        const json = await res.json();
        if (!cancelled) setData(json);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load sources");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [opportunityId]);

  // Close on Escape key
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 z-10 bg-slate-900 border-b border-slate-800 px-6 py-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <FileText size={20} className="text-blue-400" />
              Sources &amp; Scoring — {symbol}
            </h2>
            {data && (
              <p className="text-sm text-slate-400 mt-0.5">
                Score {data.score}/100 · {data.source_count} source{data.source_count !== 1 ? "s" : ""} · {data.signal_count} signals
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition-colors"
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-4 space-y-4">
          {loading && (
            <div className="text-center py-12">
              <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
              <p className="mt-2 text-slate-400">Loading source details...</p>
            </div>
          )}

          {error && (
            <div className="bg-red-500/10 border border-red-500/50 text-red-400 px-4 py-3 rounded-lg">
              Error: {error}
            </div>
          )}

          {data && !loading && (
            <>
              {/* Scoring explainer */}
              <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-3 flex items-start gap-2">
                <Info size={16} className="text-blue-400 mt-0.5 shrink-0" />
                <p className="text-sm text-blue-300">
                  Each source type carries a <strong>weight</strong> that influences the overall score — a higher weight means the source is considered more reliable. The score combines signals from all of the sources below.
                </p>
              </div>

              {data.sources.length === 0 && (
                <p className="text-slate-400 text-sm">No source details recorded for this opportunity.</p>
              )}

              {data.sources.map((src) => (
                <div key={src.name} className="border border-slate-800 rounded-xl overflow-hidden">
                  <div className="px-4 py-3 bg-slate-800/50 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Newspaper size={16} className="text-blue-400" />
                      <span className="font-semibold text-white">{src.label}</span>
                      <span className="text-xs text-slate-500 font-mono">{src.name}</span>
                    </div>
                    <span className="text-xs px-2 py-1 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/40">
                      weight {src.weight}
                    </span>
                  </div>
                  <div className="px-4 py-3">
                    <p className="text-sm text-slate-400 mb-2">{src.description}</p>
                    {src.items_found > 0 ? (
                      <div className="space-y-2">
                        <p className="text-xs text-slate-500 font-medium">
                          {src.items_found} item{src.items_found !== 1 ? "s" : ""} found
                        </p>
                        {src.items.map((item, i) => (
                          <div key={i} className="text-sm border-l-2 border-slate-700 pl-3">
                            {item.url ? (
                              <a
                                href={item.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-blue-400 hover:underline flex items-center gap-1"
                              >
                                <span className="truncate">{item.title}</span>
                                <ExternalLink size={12} className="shrink-0" />
                              </a>
                            ) : (
                              <span className="text-slate-300">{item.title}</span>
                            )}
                            <div className="text-xs text-slate-500 mt-0.5">
                              {item.source_label}
                              {item.published_at ? ` · ${new Date(item.published_at).toLocaleDateString()}` : ""}
                            </div>
                            {item.summary && (
                              <p className="text-xs text-slate-400 mt-1 line-clamp-2">{item.summary}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 italic">
                        No stored items to display for this source. Details are captured at discovery time.
                      </p>
                    )}
                  </div>
                </div>
              ))}

              {/* Footer action */}
              <div className="pt-2 flex justify-end">
                <Link
                  href={`/news?symbol=${symbol}`}
                  className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-700 text-white transition-colors"
                >
                  View all news for {symbol} →
                </Link>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
