"use client";

import Link from "next/link";
import { useState, useEffect, useCallback } from "react";
import { AppHeader } from "@/components/AppHeader";
import {
  TrendingUp, TrendingDown, RefreshCw, Activity, BarChart2, Zap,
} from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, ScatterChart, Scatter,
  BarChart, Bar, Cell, Legend,
} from "recharts";

// ── Types ─────────────────────────────────────────────────────────────────────

type RollingPoint = { trade_index: number; timestamp: string; symbol: string; ic: number | null };
type HorizonIC = { overall: number | null; rolling: RollingPoint[]; pairs: number };
type ICResponse = {
  ic_by_horizon: Record<string, HorizonIC>;
  scatter: { horizon: string; symbol: string; event_type: string; confidence: number; return_pct: number }[];
  pairs_count: number;
  window: number;
  message?: string;
};

type EventRow = {
  event_type: string; count: number; avg_directional_score: number | null;
  long_count: number; short_count: number; hold_count: number;
};
type AttributionResponse = {
  rows_analyzed: number;
  by_event_type: EventRow[];
  top_sources: [string, number][];
  top_terms: { term: string; count: number }[];
};

type PerturbScenario = {
  signal_count: number; blocked_count: number;
  threshold_multiplier: number;
  avg_return_by_horizon: Record<string, number | null>;
};
type PerturbResponse = {
  rows_analyzed: number; nudge_pct: number;
  baseline: PerturbScenario; nudge_up: PerturbScenario; nudge_down: PerturbScenario;
  error?: string;
};

// ── Constants ─────────────────────────────────────────────────────────────────

const HORIZONS = ["4h", "1d", "3d", "1w"] as const;
const HORIZON_COLORS: Record<string, string> = {
  "4h": "#818cf8", "1d": "#34d399", "3d": "#fbbf24", "1w": "#f472b6",
};
const EVENT_COLORS: Record<string, string> = {
  earnings: "#f59e0b", macro_data: "#3b82f6", geopolitical: "#ef4444",
  monetary_policy: "#8b5cf6", trade_policy: "#ec4899", regulatory: "#14b8a6",
  sector_news: "#6366f1", fiscal: "#f97316", noise: "#64748b",
};

function fmt(v: number | null | undefined, decimals = 4): string {
  if (v == null) return "—";
  return v.toFixed(decimals);
}

// ── Section wrapper ───────────────────────────────────────────────────────────

function Section({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="bg-slate-900 border border-slate-700 rounded-xl p-5">
      <h2 className="text-slate-200 font-semibold text-sm mb-4 flex items-center gap-2">
        {icon}{title}
      </h2>
      {children}
    </section>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AlphaPage() {
  const [icData, setIcData] = useState<ICResponse | null>(null);
  const [attrData, setAttrData] = useState<AttributionResponse | null>(null);
  const [perturbData, setPerturbData] = useState<PerturbResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [activeHorizon, setActiveHorizon] = useState<string>("1d");
  const [scatterHorizon, setScatterHorizon] = useState<string>("1d");
  const [nudgePct, setNudgePct] = useState(10);
  const [symbolFilter, setSymbolFilter] = useState("");
  const [window_, setWindow] = useState(30);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const sym = symbolFilter.trim().toUpperCase() || undefined;
      const symQ = sym ? `&symbol=${sym}` : "";
      const [icRes, attrRes] = await Promise.all([
        fetch(`/api/alpha/ic?horizons=4h,1d,3d,1w&window=${window_}${symQ}`),
        fetch(`/api/alpha/attribution?limit=200${symQ}`),
      ]);
      const [ic, attr] = await Promise.all([icRes.json(), attrRes.json()]);
      setIcData(ic);
      setAttrData(attr);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [symbolFilter, window_]);

  const runPerturbation = useCallback(async () => {
    setLoading(true);
    try {
      const sym = symbolFilter.trim().toUpperCase() || null;
      const res = await fetch("/api/alpha/perturbation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nudge_pct: nudgePct / 100, symbol: sym, horizons: ["4h", "1d", "3d", "1w"] }),
      });
      setPerturbData(await res.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [symbolFilter, nudgePct]);

  useEffect(() => { load(); }, [load]);

  const rollingPoints = icData?.ic_by_horizon?.[activeHorizon]?.rolling ?? [];
  const scatterPoints = (icData?.scatter ?? []).filter(p => p.horizon === scatterHorizon);
  const overallIC = icData?.ic_by_horizon?.[activeHorizon]?.overall;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <AppHeader
        title="Alpha Analytics"
        titleGradient="from-violet-400 to-emerald-400"
        subtitle="Signal attribution · Information coefficient · Sensitivity testing"
      >
        <div className="flex items-center gap-3">
          <input
            className="bg-slate-800 border border-slate-600 text-slate-200 text-xs rounded-lg px-3 py-1.5 w-24 placeholder-slate-500"
            placeholder="Symbol…"
            value={symbolFilter}
            onChange={e => setSymbolFilter(e.target.value.toUpperCase())}
          />
          <Link href="/" className="text-xs text-slate-400 hover:text-slate-200 transition-colors">← Dashboard</Link>
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 text-xs bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg transition-colors"
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </AppHeader>

      <main className="max-w-6xl mx-auto px-6 py-6 space-y-6">
        {error && (
          <div className="bg-red-900/30 border border-red-700 text-red-300 rounded-lg px-4 py-3 text-sm">{error}</div>
        )}

        {icData?.message && !icData.pairs_count && (
          <div className="bg-slate-800 border border-slate-700 rounded-xl p-8 text-center text-slate-400 text-sm">
            {icData.message}
          </div>
        )}

        {/* ── IC Summary chips ── */}
        {icData && icData.pairs_count > 0 && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {HORIZONS.map(h => {
              const hd = icData.ic_by_horizon?.[h];
              const ic = hd?.overall;
              const pos = ic != null && ic > 0;
              return (
                <button
                  key={h}
                  onClick={() => { setActiveHorizon(h); setScatterHorizon(h); }}
                  className={`rounded-xl border p-4 text-left transition-all ${activeHorizon === h ? "border-violet-500 bg-violet-950/40" : "border-slate-700 bg-slate-900 hover:border-slate-600"}`}
                >
                  <div className="text-xs text-slate-500 mb-1">{h} IC</div>
                  <div className={`text-2xl font-bold ${ic == null ? "text-slate-500" : pos ? "text-emerald-400" : "text-red-400"}`}>
                    {fmt(ic, 3)}
                  </div>
                  <div className="text-xs text-slate-500 mt-1">{hd?.pairs ?? 0} pairs</div>
                </button>
              );
            })}
          </div>
        )}

        {/* ── Rolling IC chart ── */}
        {rollingPoints.length > 0 && (
          <Section title={`Rolling IC — ${activeHorizon} (window = ${window_} trades)`} icon={<Activity size={14} className="text-violet-400" />}>
            <div className="flex items-center gap-3 mb-3">
              <label className="text-xs text-slate-400">Window:</label>
              {[15, 30, 50].map(w => (
                <button key={w} onClick={() => setWindow(w)}
                  className={`text-xs px-2 py-0.5 rounded ${window_ === w ? "bg-violet-600 text-white" : "bg-slate-800 text-slate-400 hover:bg-slate-700"}`}>
                  {w}
                </button>
              ))}
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={rollingPoints} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="trade_index" tick={{ fill: "#64748b", fontSize: 10 }} label={{ value: "Trade #", position: "insideBottom", fill: "#475569", fontSize: 10, offset: -2 }} />
                <YAxis domain={[-1, 1]} tick={{ fill: "#64748b", fontSize: 10 }} width={36} />
                <Tooltip
                  contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8, fontSize: 11 }}
                  formatter={(v: number) => [fmt(v, 3), "IC"]}
                  labelFormatter={(i) => `Trade #${i} · ${rollingPoints[i as number]?.symbol ?? ""}`}
                />
                <ReferenceLine y={0} stroke="#475569" strokeDasharray="4 2" />
                <Line
                  type="monotone" dataKey="ic"
                  stroke={HORIZON_COLORS[activeHorizon]}
                  dot={false} strokeWidth={2}
                  connectNulls
                />
              </LineChart>
            </ResponsiveContainer>
            {overallIC != null && (
              <p className="text-xs text-slate-500 mt-2 text-right">
                Overall IC: <span className={overallIC >= 0 ? "text-emerald-400" : "text-red-400"}>{fmt(overallIC, 3)}</span>
                {" "}· A score near ±1 is ideal; near 0 means no predictive power.
              </p>
            )}
          </Section>
        )}

        {/* ── Confidence vs Return scatter ── */}
        {scatterPoints.length > 0 && (
          <Section title="Signal Confidence vs. Actual Return" icon={<BarChart2 size={14} className="text-emerald-400" />}>
            <div className="flex items-center gap-3 mb-3">
              {HORIZONS.map(h => (
                <button key={h} onClick={() => setScatterHorizon(h)}
                  className={`text-xs px-2 py-0.5 rounded ${scatterHorizon === h ? "bg-emerald-700 text-white" : "bg-slate-800 text-slate-400 hover:bg-slate-700"}`}>
                  {h}
                </button>
              ))}
            </div>
            <ResponsiveContainer width="100%" height={260}>
              <ScatterChart margin={{ top: 4, right: 12, bottom: 20, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis type="number" dataKey="confidence" name="Confidence" domain={[0, 1]}
                  tick={{ fill: "#64748b", fontSize: 10 }} label={{ value: "Signal Confidence", position: "insideBottom", fill: "#475569", fontSize: 10, offset: -10 }} />
                <YAxis type="number" dataKey="return_pct" name="Return %" tick={{ fill: "#64748b", fontSize: 10 }} width={42} />
                <ReferenceLine y={0} stroke="#475569" strokeDasharray="4 2" />
                <Tooltip
                  contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8, fontSize: 11 }}
                  formatter={(v: number, name: string) => [name === "return_pct" ? `${fmt(v, 2)}%` : fmt(v, 3), name === "return_pct" ? "Return" : "Confidence"]}
                  content={({ payload }) => {
                    if (!payload?.length) return null;
                    const d = payload[0].payload;
                    return (
                      <div className="bg-slate-800 border border-slate-700 rounded-lg p-2 text-xs">
                        <p className="font-semibold text-slate-200">{d.symbol} · {d.event_type || "—"}</p>
                        <p>Confidence: <span className="text-violet-300">{fmt(d.confidence, 3)}</span></p>
                        <p>Return: <span className={d.return_pct >= 0 ? "text-emerald-300" : "text-red-300"}>{fmt(d.return_pct, 2)}%</span></p>
                      </div>
                    );
                  }}
                />
                <Scatter
                  data={scatterPoints}
                  fill="#6366f1"
                  shape={(props: Record<string, unknown>) => {
                    const { cx, cy, payload } = props as { cx: number; cy: number; payload: { event_type: string } };
                    return <circle cx={cx} cy={cy} r={4} fill={EVENT_COLORS[payload.event_type] ?? "#6366f1"} fillOpacity={0.75} />;
                  }}
                />
              </ScatterChart>
            </ResponsiveContainer>
            <div className="flex flex-wrap gap-2 mt-2">
              {Object.entries(EVENT_COLORS).map(([et, c]) => (
                <span key={et} className="flex items-center gap-1 text-xs text-slate-400">
                  <span className="w-2 h-2 rounded-full inline-block" style={{ background: c }} />
                  {et.replace("_", " ")}
                </span>
              ))}
            </div>
          </Section>
        )}

        {/* ── Event type attribution ── */}
        {attrData && attrData.by_event_type.length > 0 && (
          <Section title="Signal Attribution by Event Type" icon={<TrendingUp size={14} className="text-amber-400" />}>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={attrData.by_event_type} margin={{ top: 4, right: 12, bottom: 4, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="event_type" tick={{ fill: "#64748b", fontSize: 10 }} />
                <YAxis tick={{ fill: "#64748b", fontSize: 10 }} width={32} />
                <Tooltip
                  contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8, fontSize: 11 }}
                  formatter={(v: number, name: string) => [v, name]}
                />
                <Legend wrapperStyle={{ fontSize: 11, color: "#94a3b8" }} />
                <Bar dataKey="long_count" name="LONG" stackId="a" fill="#34d399" />
                <Bar dataKey="short_count" name="SHORT" stackId="a" fill="#f87171" />
                <Bar dataKey="hold_count" name="HOLD" stackId="a" fill="#64748b" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
            <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 gap-2">
              {attrData.by_event_type.slice(0, 6).map(row => (
                <div key={row.event_type} className="bg-slate-800 rounded-lg p-3">
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="w-2 h-2 rounded-full" style={{ background: EVENT_COLORS[row.event_type] ?? "#64748b" }} />
                    <span className="text-xs font-medium text-slate-300">{row.event_type.replace("_", " ")}</span>
                  </div>
                  <div className="text-xs text-slate-400">{row.count} signals</div>
                  <div className="text-xs text-slate-400">
                    avg dir: <span className={row.avg_directional_score == null ? "text-slate-500" : row.avg_directional_score >= 0 ? "text-emerald-400" : "text-red-400"}>
                      {fmt(row.avg_directional_score, 3)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
            {attrData.top_terms.length > 0 && (
              <div className="mt-4">
                <p className="text-xs text-slate-500 mb-2">Top matched keywords:</p>
                <div className="flex flex-wrap gap-1.5">
                  {attrData.top_terms.slice(0, 16).map(({ term, count }) => (
                    <span key={term} className="text-xs bg-slate-800 text-slate-300 border border-slate-700 px-2 py-0.5 rounded-full">
                      {term} <span className="text-slate-500">×{count}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </Section>
        )}

        {/* ── Perturbation test ── */}
        <Section title="Sensitivity / Perturbation Test" icon={<Zap size={14} className="text-yellow-400" />}>
          <p className="text-xs text-slate-400 mb-4">
            Nudges the entry threshold by ±N% on historical signals to check if your threshold is robust or curve-fit to noise.
          </p>
          <div className="flex items-center gap-3 mb-4">
            <label className="text-xs text-slate-400">Nudge %:</label>
            {[5, 10, 20].map(p => (
              <button key={p} onClick={() => setNudgePct(p)}
                className={`text-xs px-2 py-0.5 rounded ${nudgePct === p ? "bg-yellow-600 text-white" : "bg-slate-800 text-slate-400 hover:bg-slate-700"}`}>
                ±{p}%
              </button>
            ))}
            <button
              onClick={runPerturbation}
              disabled={loading}
              className="ml-2 flex items-center gap-1.5 text-xs bg-yellow-600 hover:bg-yellow-500 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg transition-colors"
            >
              <RefreshCw size={11} className={loading ? "animate-spin" : ""} />
              Run
            </button>
          </div>
          {perturbData && !perturbData.error && (
            <div className="grid grid-cols-3 gap-3">
              {(["nudge_down", "baseline", "nudge_up"] as const).map(key => {
                const s = perturbData[key];
                const label = key === "baseline" ? "Baseline" : key === "nudge_up" ? `+${nudgePct}%` : `−${nudgePct}%`;
                const isBase = key === "baseline";
                return (
                  <div key={key} className={`rounded-xl border p-4 ${isBase ? "border-yellow-600/60 bg-yellow-950/20" : "border-slate-700 bg-slate-800"}`}>
                    <div className={`text-xs font-semibold mb-3 ${isBase ? "text-yellow-300" : "text-slate-300"}`}>{label} threshold ×{s.threshold_multiplier}</div>
                    <div className="text-xs text-slate-400 mb-1">Signals fired: <span className="text-slate-100 font-medium">{s.signal_count}</span></div>
                    <div className="text-xs text-slate-400 mb-3">Blocked: <span className="text-slate-100 font-medium">{s.blocked_count}</span></div>
                    <div className="space-y-1">
                      {(["4h", "1d", "3d", "1w"] as const).map(h => {
                        const r = s.avg_return_by_horizon[h];
                        return (
                          <div key={h} className="flex justify-between text-xs">
                            <span className="text-slate-500">{h}</span>
                            <span className={r == null ? "text-slate-600" : r >= 0 ? "text-emerald-400" : "text-red-400"}>
                              {r == null ? "—" : `${r >= 0 ? "+" : ""}${fmt(r, 2)}%`}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {perturbData?.error && (
            <p className="text-xs text-red-400">{perturbData.error}</p>
          )}
          {!perturbData && (
            <p className="text-xs text-slate-500">Click Run to test threshold sensitivity against your trade history.</p>
          )}
        </Section>
      </main>
    </div>
  );
}
