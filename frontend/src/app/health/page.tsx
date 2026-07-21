"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AppHeader } from "@/components/AppHeader";

type DataPullEvent = {
  status: string;
  source: string;
  summary: string;
  error?: string | null;
  checked_at?: string | null;
  details?: Record<string, unknown>;
};

type HealthPayload = {
  status: string;
  timestamp?: string;
  version: string;
  database_status: string;
  runtime: {
    started_at?: string | null;
    uptime_seconds: number;
    request_count: number;
    avg_request_latency_ms: number;
  };
  model: {
    reachable?: boolean;
    active_model?: string;
    configured_model?: string;
    available_models?: string[];
    resolution?: string;
    error?: string;
  };
  analysis: {
    avg_runtime_ms?: number | null;
    recent_analysis_seconds?: number[];
    last_request_id?: string | null;
    last_completed_at?: string | null;
    last_status?: string | null;
    last_error?: string | null;
    recent_run_count?: number;
    tracked_symbols?: string[];
    auto_run_enabled?: boolean;
    seconds_until_next_auto_run?: number;
  };
  data_pulls: {
    latest?: DataPullEvent | null;
    recent?: DataPullEvent[];
  };
};

function GlassCard({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-2xl border border-white/10 p-6 ${className}`}
      style={{ background: "rgba(30,41,59,0.75)", backdropFilter: "blur(12px)" }}
    >
      {children}
    </div>
  );
}

function formatRelativeDate(value?: string | null) {
  if (!value) return "Not yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not yet";
  return `${date.toLocaleString()} (${Math.max(0, Math.round((Date.now() - date.getTime()) / 60000))} min ago)`;
}

function formatDurationMs(value?: number | null) {
  if (!value || value <= 0) return "No completed runs yet";
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${Math.round(value)}ms`;
}

function formatUptime(seconds: number) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${minutes}m`;
}

function statusTone(status?: string | null) {
  switch ((status || "").toLowerCase()) {
    case "healthy":
    case "ok":
    case "success":
      return "text-emerald-300 border-emerald-500/20 bg-emerald-500/10";
    case "partial":
    case "degraded":
      return "text-amber-300 border-amber-500/20 bg-amber-500/10";
    case "error":
    case "failed":
    case "offline":
      return "text-red-300 border-red-500/20 bg-red-500/10";
    default:
      return "text-slate-300 border-slate-600/30 bg-slate-800/60";
  }
}

export default function HealthPage() {
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const response = await fetch("/api/health", { cache: "no-store" });
        const payload = await response.json();
        if (!cancelled) {
          setHealth(payload);
          setLoading(false);
        }
      } catch {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void load();
    const id = window.setInterval(load, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const overallStatus = health?.status || "offline";
  const activeModel = health?.model.active_model || health?.model.configured_model || "No model detected";
  const latestPull = health?.data_pulls.latest;
  const autoRunSeconds = health?.analysis.seconds_until_next_auto_run ?? 0;
  const autoRunCountdown = `${Math.floor(autoRunSeconds / 60)}:${String(autoRunSeconds % 60).padStart(2, "0")}`;

  return (
    <div className="min-h-screen" style={{ backgroundColor: "#0f172a", color: "#f8fafc" }}>
      <AppHeader
        title="System Health"
        titleSize="text-2xl"
        titleGradient="from-emerald-400 to-cyan-300"
        subtitle="Live runtime, model, and data-pull status for Sentiment Trading Alpha"
        py="py-4"
      />

      <main className="max-w-6xl mx-auto px-6 py-10 space-y-6">
        <GlassCard>
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="text-[11px] uppercase tracking-[0.22em] text-slate-400">Overall</p>
              <div className={`inline-flex mt-3 rounded-full border px-3 py-1 text-sm font-semibold ${statusTone(overallStatus)}`}>
                {loading ? "Loading..." : overallStatus}
              </div>
              <p className="text-slate-400 mt-4 text-sm">
                Version {health?.version || "1.0.0"} {health?.timestamp ? `• Updated ${new Date(health.timestamp).toLocaleTimeString()}` : ""}
              </p>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 lg:min-w-[34rem]">
              <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                <p className="text-[11px] uppercase tracking-wider text-slate-500">Running Model</p>
                <p className="text-sm text-cyan-300 font-semibold mt-2 break-all">{activeModel}</p>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                <p className="text-[11px] uppercase tracking-wider text-slate-500">Avg Run Time</p>
                <p className="text-sm text-white font-semibold mt-2">{formatDurationMs(health?.analysis.avg_runtime_ms)}</p>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                <p className="text-[11px] uppercase tracking-wider text-slate-500">Last Data Pull</p>
                <p className="text-sm text-white font-semibold mt-2">{latestPull?.status || "unknown"}</p>
                <p className="text-xs text-slate-400 mt-1">{formatRelativeDate(latestPull?.checked_at)}</p>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                <p className="text-[11px] uppercase tracking-wider text-slate-500">Uptime</p>
                <p className="text-sm text-white font-semibold mt-2">{formatUptime(health?.runtime.uptime_seconds || 0)}</p>
              </div>
            </div>
          </div>
        </GlassCard>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <GlassCard className="lg:col-span-2">
            <p className="text-[11px] uppercase tracking-[0.22em] text-emerald-300">What The User Probably Cares About</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-5">
              <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                <p className="text-sm font-semibold text-white">Model runtime</p>
                <p className={`text-sm mt-2 ${health?.model.reachable ? "text-emerald-300" : "text-amber-300"}`}>
                  {health?.model.reachable ? "Ollama is reachable and ready." : "Ollama is not reachable right now."}
                </p>
                <p className="text-xs text-slate-400 mt-2">
                  Resolution: {health?.model.resolution || "unknown"} • {health?.model.available_models?.length || 0} served models detected
                </p>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                <p className="text-sm font-semibold text-white">Last analysis</p>
                <p className="text-sm text-white mt-2">{formatRelativeDate(health?.analysis.last_completed_at)}</p>
                <p className="text-xs text-slate-400 mt-2">
                  Status: {health?.analysis.last_status || "unknown"} • Request ID: {health?.analysis.last_request_id || "n/a"}
                </p>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                <p className="text-sm font-semibold text-white">Data pulls</p>
                <p className="text-sm text-white mt-2">{latestPull?.summary || "No data pull recorded yet."}</p>
                <p className="text-xs text-slate-400 mt-2">{latestPull?.error || "No recent pull errors recorded."}</p>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                <p className="text-sm font-semibold text-white">Automation</p>
                <p className="text-sm text-white mt-2">
                  {health?.analysis.auto_run_enabled ? `Auto-run in ${autoRunCountdown}` : "Auto-run disabled"}
                </p>
                <p className="text-xs text-slate-400 mt-2">
                  Tracking {(health?.analysis.tracked_symbols || []).join(", ") || "no symbols"}
                </p>
              </div>
            </div>
          </GlassCard>

          <GlassCard>
            <p className="text-[11px] uppercase tracking-[0.22em] text-blue-300">System Details</p>
            <div className="space-y-3 mt-5 text-sm">
              <div className="flex justify-between border-b border-slate-700/40 pb-2">
                <span className="text-slate-400">Database</span>
                <span className="text-white text-right max-w-[12rem] break-words">{health?.database_status || "unknown"}</span>
              </div>
              <div className="flex justify-between border-b border-slate-700/40 pb-2">
                <span className="text-slate-400">Requests served</span>
                <span className="text-white">{health?.runtime.request_count || 0}</span>
              </div>
              <div className="flex justify-between border-b border-slate-700/40 pb-2">
                <span className="text-slate-400">Avg request latency</span>
                <span className="text-white">{formatDurationMs(health?.runtime.avg_request_latency_ms || 0)}</span>
              </div>
              <div className="flex justify-between border-b border-slate-700/40 pb-2">
                <span className="text-slate-400">Recent completed runs</span>
                <span className="text-white">{health?.analysis.recent_run_count || 0}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Recent run samples</span>
                <span className="text-white">{(health?.analysis.recent_analysis_seconds || []).join(", ") || "n/a"}</span>
              </div>
            </div>
            {health?.analysis.last_error ? (
              <div className="mt-5 rounded-xl border border-red-500/20 bg-red-500/10 p-4">
                <p className="text-xs uppercase tracking-wider text-red-300">Last analysis error</p>
                <p className="text-sm text-red-100 mt-2 break-words">{health.analysis.last_error}</p>
              </div>
            ) : null}
            {health?.model.error ? (
              <div className="mt-4 rounded-xl border border-amber-500/20 bg-amber-500/10 p-4">
                <p className="text-xs uppercase tracking-wider text-amber-300">Model connection note</p>
                <p className="text-sm text-amber-100 mt-2 break-words">{health.model.error}</p>
              </div>
            ) : null}
          </GlassCard>
        </div>

        <GlassCard>
          <p className="text-[11px] uppercase tracking-[0.22em] text-slate-400">Recent Data Pull Events</p>
          <div className="mt-5 space-y-3">
            {(health?.data_pulls.recent || []).length === 0 ? (
              <p className="text-sm text-slate-400">No data-pull activity has been recorded yet.</p>
            ) : (
              (health?.data_pulls.recent || []).map((event, index) => (
                <div key={`${event.source}-${event.checked_at || index}`} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                  <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                    <div>
                      <p className="text-sm font-semibold text-white">{event.summary}</p>
                      <p className="text-xs text-slate-400 mt-1">
                        {event.source} • {formatRelativeDate(event.checked_at)}
                      </p>
                    </div>
                    <div className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${statusTone(event.status)}`}>
                      {event.status}
                    </div>
                  </div>
                  <p className="text-sm text-slate-300 mt-3">{event.error || "No errors reported for this pull."}</p>
                </div>
              ))
            )}
          </div>
        </GlassCard>
      </main>
    </div>
  );
}
