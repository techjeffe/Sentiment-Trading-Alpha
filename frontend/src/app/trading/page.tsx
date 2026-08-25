"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState, useEffect, useCallback, useMemo, Suspense } from "react";
import { AppHeader } from "@/components/AppHeader";
import DispatchErrorBanner from "@/components/DispatchErrorBanner";
import {
    TrendingUp, TrendingDown, Minus, RefreshCw, Trash2,
    DollarSign, BarChart2, Activity, ChevronDown,
} from "lucide-react";
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";

// â”€â”€â”€ Types â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

type MarketStatus = {
    status: "open" | "pre-market" | "after-hours" | "closed";
    label: string;
    tradeable: boolean;
};

type Summary = {
    total_trades: number;
    open_positions: number;
    closed_trades: number;
    total_deployed: number;
    realized_pnl: number;
    open_pnl: number;
    total_pnl: number;
    total_pnl_pct: number;
    win_count: number;
    loss_count: number;
    win_rate: number;
    avg_win: number;
    avg_loss: number;
};

type OpenPosition = {
    id: number;
    underlying: string;
    execution_ticker: string;
    signal_type: "LONG" | "SHORT";
    leverage: string;
    amount: number;
    shares: number;
    entry_price: number;
    current_price: number;
    entered_at: string;
    market_session: string;
    unrealized_pnl: number;
    unrealized_pnl_pct: number;
    conviction_level: "HIGH" | "MEDIUM" | "LOW" | null;
    trading_type: "POSITION" | "SWING" | "VOLATILE_EVENT" | "SCALP" | null;
    holding_period_hours: number | null;
    holding_window_until: string | null;
    window_active: boolean;
    window_remaining_minutes: number | null;
};

type ClosedTrade = {
    id: number;
    underlying: string;
    execution_ticker: string;
    signal_type: "LONG" | "SHORT";
    leverage: string;
    amount: number;
    shares: number;
    entry_price: number;
    exit_price: number;
    entered_at: string;
    exited_at: string;
    realized_pnl: number;
    realized_pnl_pct: number;
    market_session: string;
    conviction_level: "HIGH" | "MEDIUM" | "LOW" | null;
    trading_type: "POSITION" | "SWING" | "VOLATILE_EVENT" | "SCALP" | null;
    holding_period_hours: number | null;
    close_reason: string | null;
};

type EquityPoint = {
    at: string;
    cumulative_pnl: number;
    trade_pnl: number;
    trade_pnl_pct: number;
    ticker: string;
    underlying: string;
};

type AlpacaOrder = {
    id: number;
    paper_trade_id: number | null;
    alpaca_order_id: string | null;
    symbol: string;
    side: string;
    notional: number | null;
    qty: number | null;
    order_type: string;
    limit_price: number | null;
    status: string | null;
    filled_qty: number | null;
    filled_avg_price: number | null;
    trading_mode: string;
    error_message: string | null;
    submitted_at: string | null;
    filled_at: string | null;
    created_at: string | null;
};

type BrokerMode = "paper" | "live";
type TradingTrack = "strategy_paper" | "alpaca_paper" | "alpaca_live";

type AlpacaAccount = {
    trading_mode?: BrokerMode;
    account_number?: string;
    status?: string;
    equity?: string | number;
    portfolio_value?: string | number;
    last_equity?: string | number;
    cash?: string | number;
    buying_power?: string | number;
    unrealized_pl?: string | number;
    daytrade_count?: string | number;
    daytrading_buying_power?: string | number;
    pattern_day_trader?: boolean | string;
    trading_blocked?: boolean | string;
};

type AlpacaPosition = {
    symbol: string;
    qty: string | number;
    avg_entry_price: string | number;
    current_price: string | number;
    last_price: string | number;
    market_value: string | number;
    unrealized_pnl: string | number;
    unrealized_plpc: string | number;
    side: "long" | "short";
    session?: string;
    buy_current_price?: string | number;
    sell_current_price?: string | number;
    today_cost_basis?: string | number;
    today_pl?: string | number;
    today_plpc?: string | number;
    asset_meta?: {
        alias_symbol?: string;
        custom_name?: string;
        custom_slug?: string;
        order_pricing_helper_type?: string;
    };
};

type AlpacaStatus = {
    execution_mode?: "off" | "paper" | "live";
    live_trading_enabled: boolean;
    secrets?: {
        paper?: { configured?: boolean };
        live?: { configured?: boolean };
    };
};

const PREFERRED_TRADING_TRACK_KEY = "preferredTradingTrack";

type TradingData = {
    market: MarketStatus;
    paper_trade_amount: number;
    summary: Summary;
    open_positions: OpenPosition[];
    closed_trades: ClosedTrade[];
    equity_curve: EquityPoint[];
};

type AlpacaPortfolioHistory = {
    timestamp: number[];
    equity: number[];
    profit_loss: number[];
    profit_loss_pct: number[];
    base_value: number;
};

// â”€â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function pnlColor(val: number) {
    if (val > 0) return "text-emerald-400";
    if (val < 0) return "text-red-400";
    return "text-slate-400";
}

function pnlBg(val: number) {
    if (val > 0) return "bg-emerald-500/10 text-emerald-400 border-emerald-500/20";
    if (val < 0) return "bg-red-500/10 text-red-400 border-red-500/20";
    return "bg-slate-700/30 text-slate-400 border-slate-600/20";
}

function fmt(val: number, decimals = 2) {
    return (val >= 0 ? "+" : "") + val.toFixed(decimals);
}

function fmtDollar(val: number) {
    return (val >= 0 ? "+$" : "-$") + Math.abs(val).toFixed(2);
}

function fmtMoney(val: string | number | null | undefined) {
    const num = typeof val === "number" ? val : Number(val ?? NaN);
    if (!Number.isFinite(num)) return "—";
    return `$${num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function toNumber(val: string | number | null | undefined) {
    const num = typeof val === "number" ? val : Number(val ?? NaN);
    return Number.isFinite(num) ? num : null;
}

function fmtDate(iso: string | null) {
    if (!iso) return "-";
    return new Date(iso).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
}

function SessionBadge({ session }: { session: string }) {
    const map: Record<string, string> = {
        open: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20",
        "pre-market": "bg-amber-500/10 text-amber-300 border-amber-500/20",
        "after-hours": "bg-blue-500/10 text-blue-300 border-blue-500/20",
        closed: "bg-slate-700/30 text-slate-400 border-slate-600/20",
    };
    const cls = map[session] || map.closed;
    return (
        <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-medium border ${cls}`}>
            {session}
        </span>
    );
}

function DirectionBadge({ signal }: { signal: string }) {
    if (signal === "LONG") {
        return (
            <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-500/20">
                <TrendingUp size={10} /> LONG
            </span>
        );
    }
    return (
        <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold bg-red-500/10 text-red-300 border border-red-500/20">
            <TrendingDown size={10} /> SHORT
        </span>
    );
}

function ConvictionBadge({ conviction, tradingType }: { conviction: string | null; tradingType: string | null }) {
    const isHigh = conviction === "HIGH";
    const label = tradingType ?? conviction ?? "—";
    const highBg = "bg-yellow-500/15 text-yellow-300 border-yellow-500/30";
    const colors: Record<string, string> = {
        POSITION: "bg-purple-500/10 text-purple-300 border-purple-500/20",
        SWING: "bg-blue-500/10 text-blue-300 border-blue-500/20",
        VOLATILE_EVENT: "bg-amber-500/10 text-amber-300 border-amber-500/20",
        SCALP: "bg-slate-500/10 text-slate-300 border-slate-500/20",
    };
    const cls = colors[tradingType ?? ""] ?? "bg-slate-500/10 text-slate-400 border-slate-500/20";
    return (
        <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold border ${isHigh ? highBg : cls}`}>
            {isHigh && "⚡ "}
            {label}
        </span>
    );
}

function WindowBadge({ active, remaining }: { active: boolean; remaining: number | null }) {
    if (!active || remaining == null) return <span className="text-slate-600 text-[10px]">—</span>;
    const hrs = Math.floor(remaining / 60);
    const mins = remaining % 60;
    const label = hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
    return (
        <span className="inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-500/20">
            🔒 {label}
        </span>
    );
}

function MarketBadge({ market }: { market: MarketStatus }) {
    const map: Record<string, string> = {
        open: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20",
        "pre-market": "bg-amber-500/10 text-amber-300 border-amber-500/20",
        "after-hours": "bg-blue-500/10 text-blue-300 border-blue-500/20",
        closed: "bg-slate-700/30 text-slate-400 border-slate-600/20",
    };
    const cls = map[market.status] || map.closed;
    const dot = market.tradeable ? "bg-emerald-400" : "bg-slate-500";
    return (
        <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium border ${cls}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${dot} shrink-0`} />
            {market.label}
        </span>
    );
}

function modeLabel(mode: BrokerMode) {
    return mode === "live" ? "Live" : "Paper";
}

function modeBadgeClass(mode: BrokerMode) {
    return mode === "live"
        ? "bg-rose-600/20 text-rose-300 border-rose-600/30"
        : "bg-sky-500/10 text-sky-300 border-sky-500/20";
}

function trackLabel(track: TradingTrack) {
    if (track === "strategy_paper") return "Strategy Paper";
    if (track === "alpaca_paper") return "Alpaca Paper";
    return "Alpaca Live";
}

// â”€â”€â”€ Stat Card â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function StatCard({ label, value, sub, color = "" }: { label: string; value: string; sub?: string; color?: string }) {
    return (
        <div className="rounded-xl border border-white/8 p-4" style={{ background: "rgba(30,41,59,0.7)" }}>
            <p className="text-[10px] uppercase tracking-widest text-slate-500">{label}</p>
            <p className={`text-2xl font-black mt-1 ${color || "text-white"}`}>{value}</p>
            {sub && <p className="text-[11px] text-slate-500 mt-0.5">{sub}</p>}
        </div>
    );
}

// â”€â”€â”€ Equity Curve â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function EquityCurve({ data }: { data: EquityPoint[] }) {
    if (data.length === 0) {
        return (
            <div className="flex items-center justify-center h-32 text-slate-500 text-sm">
                No closed trades yet
            </div>
        );
    }

    const chartData = [{ at: "start", cumulative_pnl: 0, ticker: "" }, ...data].map((d, i) => ({
        x: i,
        pnl: d.cumulative_pnl,
        label: d.at === "start" ? "Start" : fmtDate(d.at),
        ticker: "ticker" in d ? d.ticker : "",
    }));

    const minPnl = Math.min(0, ...data.map(d => d.cumulative_pnl));
    const maxPnl = Math.max(0, ...data.map(d => d.cumulative_pnl));

    return (
        <ResponsiveContainer width="100%" height={160}>
            <LineChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="x" hide />
                <YAxis
                    domain={[minPnl - 1, maxPnl + 1]}
                    tickFormatter={(v) => `$${v >= 0 ? "+" : ""}${v.toFixed(0)}`}
                    tick={{ fill: "#64748b", fontSize: 10 }}
                    width={52}
                />
                <Tooltip
                    contentStyle={{ background: "#1e293b", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, fontSize: 11 }}
                    labelFormatter={(_, payload) => payload?.[0]?.payload?.label ?? ""}
                    formatter={(value: number) => [fmtDollar(value), "Cumulative P&L"]}
                />
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" strokeDasharray="4 4" />
                <Line
                    type="monotone"
                    dataKey="pnl"
                    stroke={data[data.length - 1]?.cumulative_pnl >= 0 ? "#34d399" : "#f87171"}
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 4 }}
                />
            </LineChart>
        </ResponsiveContainer>
    );
}

// â”€â”€â”€ Alpaca Equity Curve â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function AlpacaEquityCurve({ history }: { history: AlpacaPortfolioHistory }) {
    if (!history.timestamp || history.timestamp.length === 0) {
        return (
            <div className="flex items-center justify-center h-32 text-slate-500 text-sm">
                No portfolio history available yet
            </div>
        );
    }

    const chartData = history.timestamp.map((ts, i) => ({
        x: i,
        equity: history.equity[i] ?? 0,
        label: new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
    }));

    const equities = chartData.map(d => d.equity).filter(Boolean);
    const minEq = Math.min(...equities);
    const maxEq = Math.max(...equities);
    const lastPnl = (history.profit_loss ?? [])[history.profit_loss.length - 1] ?? 0;

    return (
        <ResponsiveContainer width="100%" height={160}>
            <LineChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="x" hide />
                <YAxis
                    domain={[minEq * 0.998, maxEq * 1.002]}
                    tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`}
                    tick={{ fill: "#64748b", fontSize: 10 }}
                    width={52}
                />
                <Tooltip
                    contentStyle={{ background: "#1e293b", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, fontSize: 11 }}
                    labelFormatter={(_, payload) => payload?.[0]?.payload?.label ?? ""}
                    formatter={(value: number) => [`$${value.toFixed(2)}`, "Account Equity"]}
                />
                <ReferenceLine y={history.base_value} stroke="rgba(255,255,255,0.15)" strokeDasharray="4 4" />
                <Line
                    type="monotone"
                    dataKey="equity"
                    stroke={lastPnl >= 0 ? "#34d399" : "#f87171"}
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 4 }}
                />
            </LineChart>
        </ResponsiveContainer>
    );
}

// â”€â”€â”€ Main Page â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export default function TradingPage() {
    return (
        <Suspense fallback={<div className="min-h-screen bg-slate-950 flex items-center justify-center text-slate-400">Loading...</div>}>
            <TradingPageContent />
        </Suspense>
    );
}

function TradingPageContent() {
    const searchParams = useSearchParams();
    const symbolFromUrl = searchParams.get("symbol");
    const [data, setData] = useState<TradingData | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [resetting, setResetting] = useState(false);
    const [alpacaLiveEnabled, setAlpacaLiveEnabled] = useState(false);
    const [alpacaStatus, setAlpacaStatus] = useState<AlpacaStatus | null>(null);
    const [alpacaOrders, setAlpacaOrders] = useState<AlpacaOrder[]>([]);
    const [alpacaAccounts, setAlpacaAccounts] = useState<Partial<Record<BrokerMode, AlpacaAccount>>>({});
    const [alpacaHistories, setAlpacaHistories] = useState<Partial<Record<BrokerMode, AlpacaPortfolioHistory>>>({});
    const [alpacaLivePositions, setAlpacaLivePositions] = useState<AlpacaPosition[]>([]);
    const [preferredTrack, setPreferredTrack] = useState<TradingTrack>("strategy_paper");
    const [livePrices, setLivePrices] = useState<Record<string, any>>({});
    const [liveSummary, setLiveSummary] = useState<any>(null);

    // ── Circuit breaker state (Option C #4) ─────────────────────────────
    const [circuitBreakerFired, setCircuitBreakerFired] = useState(false);
    const [circuitBreakerReason, setCircuitBreakerReason] = useState<string | null>(null);
    const [reEnabling, setReEnabling] = useState(false);

    const [paperExpanded, setPaperExpanded] = useState(false);

    // Closed trades pagination (older trades beyond the initial 4-day window)
    const [olderTrades, setOlderTrades] = useState<ClosedTrade[]>([]);
    const [olderTradesOffset, setOlderTradesOffset] = useState(0);
    const [loadingOlder, setLoadingOlder] = useState(false);
    const [totalOlderAvailable, setTotalOlderAvailable] = useState<number | null>(null);
    const [olderTradesLoadingFirst, setOlderTradesLoadingFirst] = useState(false);

    // Live closed trades pagination (older trades beyond the initial 4-day window)
    const [liveOlderTrades, setLiveOlderTrades] = useState<ClosedTrade[]>([]);
    const [liveOlderTradesOffset, setLiveOlderTradesOffset] = useState(0);
    const [loadingLiveOlder, setLoadingLiveOlder] = useState(false);
    const [liveTotalOlderAvailable, setLiveTotalOlderAvailable] = useState<number | null>(null);
    const [liveOlderTradesLoadingFirst, setLiveOlderTradesLoadingFirst] = useState(false);

    const loadOlderTrades = useCallback(async () => {
        try {
            setOlderTradesLoadingFirst(true); // Hide button while loading
            setLoadingOlder(true);
            const res = await fetch(
                `/api/paper-trading/closed-trades?offset=${olderTradesOffset}&limit=20`,
                { cache: "no-store" }
            );
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const json = await res.json();
            const trades = json.closed_trades || [];
            if (trades.length > 0) {
                setOlderTrades(prev => [...prev, ...trades]);
                setTotalOlderAvailable(json.total_available ?? null);
                setOlderTradesOffset(prev => prev + 20);
            }
            // If no trades returned, hide the button (no more older trades)
            setOlderTradesLoadingFirst(false);
        } catch (e) {
            console.error("Failed to load older closed trades:", e);
            setOlderTradesLoadingFirst(false);
        } finally {
            setLoadingOlder(false);
        }
    }, [olderTradesOffset]);

    const loadLiveOlderTrades = useCallback(async () => {
        try {
            setLiveOlderTradesLoadingFirst(true);
            setLoadingLiveOlder(true);
            const res = await fetch(
                `/api/alpaca/closed-trades?offset=${liveOlderTradesOffset}&limit=20`,
                { cache: "no-store" }
            );
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const json = await res.json();
            const trades: ClosedTrade[] = json.closed_trades || [];
            if (trades.length > 0) {
                setLiveOlderTrades(prev => [...prev, ...trades]);
                setLiveTotalOlderAvailable(json.total_available ?? null);
                setLiveOlderTradesOffset(prev => prev + 20);
            }
            setLiveOlderTradesLoadingFirst(false);
        } catch (e) {
            console.error("Failed to load older live closed trades:", e);
            setLiveOlderTradesLoadingFirst(false);
        } finally {
            setLoadingLiveOlder(false);
        }
    }, [liveOlderTradesOffset]);

    const load = useCallback(async () => {
        try {
            setLoading(true);
            setError(null);
            // Reset pagination state when reloading main data
            setOlderTrades([]);
            setOlderTradesOffset(0);
            setTotalOlderAvailable(null);
            const paperTradingResponse = await fetch("/api/paper-trading", { cache: "no-store" });
            if (!paperTradingResponse.ok) throw new Error(`HTTP ${paperTradingResponse.status}`);
            setData(await paperTradingResponse.json());
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load");
        } finally {
            setLoading(false);
        }
    }, []);

    const loadAlpaca = useCallback(async () => {
        try {
            const [statusRes, ordersRes, paperAccountRes, liveAccountRes, paperHistoryRes, liveHistoryRes, livePositionsRes, liveSummaryRes, cbStatusRes] = await Promise.all([
                fetch("/api/alpaca/status", { cache: "no-store" }),
                fetch("/api/alpaca/orders?limit=50", { cache: "no-store" }),
                fetch("/api/alpaca/account?mode=paper", { cache: "no-store" }),
                fetch("/api/alpaca/account?mode=live", { cache: "no-store" }),
                fetch("/api/alpaca/portfolio-history?mode=paper&period=1M&timeframe=1D", { cache: "no-store" }),
                fetch("/api/alpaca/portfolio-history?mode=live&period=1M&timeframe=1D", { cache: "no-store" }),
                fetch("/api/alpaca/positions?mode=live", { cache: "no-store" }),
                fetch("/api/alpaca/live-summary", { cache: "no-store" }),
                fetch("/api/alpaca/circuit-breaker/status", { cache: "no-store" }),
            ]);
            if (statusRes.ok) {
                const s = await statusRes.json();
                setAlpacaStatus(s);
                setAlpacaLiveEnabled(!!s?.live_trading_enabled);
            }
            if (ordersRes.ok) {
                setAlpacaOrders(await ordersRes.json());
            }
            const nextAccounts: Partial<Record<BrokerMode, AlpacaAccount>> = {};
            if (paperAccountRes.ok) nextAccounts.paper = await paperAccountRes.json();
            if (liveAccountRes.ok) nextAccounts.live = await liveAccountRes.json();
            setAlpacaAccounts(nextAccounts);

            const nextHistories: Partial<Record<BrokerMode, AlpacaPortfolioHistory>> = {};
            if (paperHistoryRes.ok) {
                const history = await paperHistoryRes.json();
                if (history?.timestamp?.length) nextHistories.paper = history;
            }
            if (liveHistoryRes.ok) {
                const history = await liveHistoryRes.json();
                if (history?.timestamp?.length) nextHistories.live = history;
            }
            setAlpacaHistories(nextHistories);

            if (livePositionsRes.ok) {
                const positions: AlpacaPosition[] = await livePositionsRes.json();
                setAlpacaLivePositions(positions.filter(p => {
                    const q = typeof p.qty === "number" ? p.qty : Number(p.qty ?? NaN);
                    return Number.isFinite(q) && Math.abs(q) >= 0.00005;
                }));
            }

            if (liveSummaryRes.ok) {
                setLiveSummary(await liveSummaryRes.json());
            }
            // ── Circuit breaker status (Option C #4) ─────────────────────
            if (cbStatusRes.ok) {
                const cbStatus = await cbStatusRes.json();
                setCircuitBreakerFired(!!cbStatus?.circuit_breaker_fired);
                setCircuitBreakerReason(cbStatus?.reason ?? null);
            }
        } catch { /* silent — Alpaca may not be configured */ }
    }, []);

    useEffect(() => { load(); loadAlpaca(); }, [load, loadAlpaca]);

    // If a symbol is provided in the URL, show an alert or handle it
    useEffect(() => {
        if (symbolFromUrl) {
            console.log("Symbol from URL:", symbolFromUrl);
            // You could pre-fill a search, show symbol info, etc.
        }
    }, [symbolFromUrl]);

    const priceSymbols = useMemo(() => {
        const strategySymbols = data?.open_positions.map(p => p.execution_ticker) ?? [];
        const alpacaSymbols = alpacaLivePositions.map(p => p.symbol);
        const all = Array.from(new Set([...strategySymbols, ...alpacaSymbols]));
        return all.join(",");
    }, [data?.open_positions, alpacaLivePositions]);

    useEffect(() => {
        if (!priceSymbols) return;
        const fetchPrices = async () => {
            try {
                const res = await fetch(`/api/prices?symbols=${priceSymbols}`);
                if (res.ok) setLivePrices(await res.json());
            } catch { }
        };
        fetchPrices();
        const interval = setInterval(fetchPrices, 10000);
        return () => clearInterval(interval);
    }, [priceSymbols]);

    const handleClosePosition = async (tradeId: number) => {
        if (!confirm("Are you sure you want to manually close this position?")) return;
        try {
            const res = await fetch(`/api/paper-trading/close`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ trade_id: tradeId })
            });
            if (!res.ok) throw new Error("Failed to close position");
            await load(); // Reload data immediately to reflect the change
        } catch (err: any) {
            alert(err.message);
        }
    };

    const handleCloseAlpacaPosition = async (symbol: string) => {
        if (!confirm(`Are you sure you want to close the live position for ${symbol}?`)) return;
        try {
            const res = await fetch(`/api/alpaca/positions/${symbol}/close`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ mode: "live" })
            });
            if (!res.ok) {
                const errorData = await res.json().catch(() => ({ error: res.statusText }));
                throw new Error(errorData.error || "Failed to close position");
            }
            await load();
            await loadAlpaca(); // Reload Alpaca data immediately to reflect the change
        } catch (err: any) {
            alert(err.message);
        }
    };

    // ── Circuit breaker re-enable (Option C #4) ─────────────────────────
    const handleReEnableCircuitBreaker = async () => {
        if (!confirm(
            "Re-enable live trading? This will cancel all open Alpaca orders and re-enable execution.\n\nContinue?"
        )) return;
        setReEnabling(true);
        try {
            const res = await fetch("/api/alpaca/circuit-breaker/re-enable", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ confirmed: true }),
            });
            if (!res.ok) throw new Error("Failed to re-enable trading");
            await loadAlpaca(); // Reload to reflect the change
        } catch (err: any) {
            alert(err.message);
        } finally {
            setReEnabling(false);
        }
    };

    const calculateUnrealizedPnl = (trade: any, currentPrice?: number) => {
        if (!currentPrice) return { pnl: trade.unrealized_pnl, pct: trade.unrealized_pnl_pct };

        let priceDiff = currentPrice - trade.entry_price;

        // If direct shorting is used (execution ticker is the underlying, but signal is SHORT)
        if (trade.signal_type === "SHORT" && trade.execution_ticker === trade.underlying) {
            priceDiff = -priceDiff;
        }
        // Otherwise (Longs and Inverse ETFs like SPXS or SCO), 
        // the execution ticker naturally tracks the P&L direction correctly.

        const rawReturn = priceDiff / trade.entry_price;
        const pnlPct = rawReturn * 100;
        // Use shares for exact precision, fallback to amount * return if shares is missing
        const pnlAmount = trade.shares ? (trade.shares * priceDiff) : (trade.amount * rawReturn);
        return { pnl: pnlAmount, pct: pnlPct };
    };

    const calculateUnrealizedPnlForLive = (pos: AlpacaPosition, currentPrice?: number) => {
        const qtyNum = toNumber(pos.qty);
        const entryNum = toNumber(pos.avg_entry_price);
        if (!currentPrice || !qtyNum || !entryNum) return { pnl: toNumber(pos.unrealized_pnl) ?? 0, pct: toNumber(pos.unrealized_plpc) ?? 0 };

        const sideLower = String(pos.side || "").toLowerCase();
        let priceDiff = currentPrice - entryNum;

        // For shorts, P&L is inverted
        if (sideLower === "short") {
            priceDiff = -priceDiff;
        }

        const pnlPct = (priceDiff / entryNum) * 100;
        const pnlAmount = qtyNum * priceDiff;
        return { pnl: pnlAmount, pct: pnlPct };
    };

    useEffect(() => {
        try {
            const stored = window.localStorage.getItem(PREFERRED_TRADING_TRACK_KEY);
            if (stored === "strategy_paper" || stored === "alpaca_paper" || stored === "alpaca_live") {
                setPreferredTrack(stored);
            }
        } catch { /* ignore */ }
    }, []);

    // When live trading is enabled, auto-promote the preferred track to alpaca_live
    // unless the user has explicitly chosen alpaca_paper.
    useEffect(() => {
        if (alpacaLiveEnabled && availableTracks.includes("alpaca_live")) {
            setPreferredTrack((prev) => (prev === "alpaca_paper" ? prev : "alpaca_live"));
        }
    }, [alpacaLiveEnabled]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        try {
            window.localStorage.setItem(PREFERRED_TRADING_TRACK_KEY, preferredTrack);
        } catch { /* ignore */ }
    }, [preferredTrack]);

    const handleReset = async () => {
        if (!confirm("Reset all paper trading history? This cannot be undone.")) return;
        setResetting(true);
        try {
            const r = await fetch("/api/paper-trading", { method: "DELETE" });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Reset failed");
        } finally {
            setResetting(false);
        }
    };

    const s = data?.summary;
    // Derive open P&L from live prices (same source as the position rows) so it doesn't rely on the backend PriceClient.
    const derivedOpenPnl = (() => {
        if (!data?.open_positions || data.open_positions.length === 0) return 0;
        return data.open_positions.reduce((acc, pos) => {
            const livePrice = livePrices[pos.execution_ticker]?.price;
            const { pnl } = calculateUnrealizedPnl(pos, livePrice);
            return acc + pnl;
        }, 0);
    })();
    const brokerModes: BrokerMode[] = ["paper", "live"];
    const configuredBrokerModes = useMemo(
        () => brokerModes.filter((mode) => alpacaStatus?.secrets?.[mode]?.configured),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [alpacaStatus],
    );
    const brokerOrderCounts = brokerModes.reduce((acc, mode) => {
        acc[mode] = alpacaOrders.filter((order) => order.trading_mode === mode).length;
        return acc;
    }, {} as Record<BrokerMode, number>);
    const latestLiveOrderError = alpacaOrders.find((order) => order.trading_mode === "live" && !!order.error_message) ?? null;
    const latestPaperOrderError = alpacaOrders.find((order) => order.trading_mode === "paper" && !!order.error_message) ?? null;

    // ── Live summary stats computed from Alpaca account + order fills ─────────
    const liveAccount = alpacaAccounts.live;
    const liveEquity = toNumber(liveAccount?.equity);
    // unrealized_pl comes directly from Alpaca and excludes cash deposits — use it
    // instead of equity-last_equity which would incorrectly include funding movements.
    const liveUnrealizedPnl = toNumber(liveAccount?.unrealized_pl);
    // ── Live summary: prefer server-computed data, fall back to client-side ──
    const liveSummaryData = liveSummary;
    const liveWins = liveSummaryData?.win_count ?? 0;
    const liveLosses = liveSummaryData?.loss_count ?? 0;
    const liveRealized = liveSummaryData?.realized_pnl ?? 0;
    const liveTotalTrades = liveSummaryData?.total_trades ?? 0;
    const liveWinRate = liveSummaryData?.win_rate ?? null;
    // Use computed unrealized P&L from the summary (summed from individual positions)
    const liveUnrealizedPnlFromSummary = liveSummaryData?.unrealized_pnl;
    const liveUnrealizedPnlForDisplay = liveUnrealizedPnlFromSummary != null ? liveUnrealizedPnlFromSummary : liveUnrealizedPnl;
    const liveClosedRows: Array<{ symbol: string; buyPrice: number; sellPrice: number; qty: number; pnl: number; closedAt: string | null }> =
        (liveSummaryData?.closed_trades ?? []).map((t: any) => ({
            symbol: t.symbol,
            buyPrice: t.buy_price,
            sellPrice: t.sell_price,
            qty: t.qty,
            pnl: t.pnl,
            closedAt: t.closed_at,
        }));
    const liveOpenRows: Array<{ symbol: string; side: string; fillPrice: number; qty: number; openedAt: string | null }> = [];

    const availableTracks = useMemo<TradingTrack[]>(() => [
        "strategy_paper",
        ...(configuredBrokerModes.includes("paper") ? ["alpaca_paper" as const] : []),
        ...(configuredBrokerModes.includes("live") ? ["alpaca_live" as const] : []),
    ], [configuredBrokerModes]);

    useEffect(() => {
        if (!availableTracks.includes(preferredTrack)) {
            setPreferredTrack("strategy_paper");
        }
    }, [availableTracks, preferredTrack]);

    return (
        <div className="min-h-screen" style={{ backgroundColor: "#0f172a", color: "#f8fafc" }}>
            {/* Dispatch error banner — shows when live close-before-open fails */}
            <DispatchErrorBanner />
            {/* ── Circuit breaker indicator (Option C #4) ──────────────────── */}
            {circuitBreakerFired && (
                <div className="mx-6 mt-4 rounded-xl border border-amber-500/30 bg-amber-950/20 px-5 py-4 flex items-center justify-between gap-4">
                    <div className="flex items-center gap-3">
                        <span className="text-amber-400 text-lg">⚡</span>
                        <div>
                            <p className="text-sm font-semibold text-amber-300">Circuit Breaker Active</p>
                            <p className="text-[11px] text-amber-400/80 mt-0.5">
                                {circuitBreakerReason
                                    ? <>Trigger: <span className="font-medium text-amber-300">{circuitBreakerReason}</span>. No new live orders until re-enabled.</>
                                    : "Live trading was auto-disabled by a safety limit. No new orders will be placed until re-enabled."
                                }
                            </p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={handleReEnableCircuitBreaker}
                        disabled={reEnabling}
                        className="flex-shrink-0 rounded-lg border border-amber-600/40 bg-amber-600/15 px-4 py-2 text-xs font-semibold text-amber-300 hover:bg-amber-600/25 transition-colors disabled:opacity-50"
                    >
                        {reEnabling ? "Re-enabling…" : "Re-enable Live Trading"}
                    </button>
                </div>
            )}
            {/* Header */}
            <AppHeader
                titleSlot={
                    <div className="flex items-center gap-4 flex-wrap flex-1">
                        {/* Title */}
                        <div className="flex-shrink-0">
                            <h1 className="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-emerald-400 to-blue-400">
                                Trading
                            </h1>
                        </div>

                        {/* Unified Track Selector - Segmented Control */}
                        <div className="flex items-center bg-slate-800/60 rounded-lg p-1 border border-slate-700/50">
                            {availableTracks.map((track) => {
                                const isActive = preferredTrack === track;
                                return (
                                    <button
                                        key={track}
                                        type="button"
                                        onClick={() => setPreferredTrack(track)}
                                        className={`
                                            px-3 py-1.5 text-xs font-medium rounded-md transition-all duration-200
                                            ${isActive
                                                ? track === 'strategy_paper'
                                                    ? 'bg-sky-500/20 text-sky-200 shadow-sm shadow-sky-500/20'
                                                    : track === 'alpaca_paper'
                                                        ? 'bg-cyan-500/20 text-cyan-200 shadow-sm shadow-cyan-500/20'
                                                        : 'bg-rose-500/20 text-rose-200 shadow-sm shadow-rose-500/20'
                                                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'
                                            }
                                        `}
                                    >
                                        {trackLabel(track)}
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                }
            >
                <div className="flex items-center gap-2 shrink-0">
                    {/* Status Indicators */}
                    {alpacaLiveEnabled && (
                        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-rose-500/8 border border-rose-500/15">
                            <span className="w-1.5 h-1.5 rounded-full bg-rose-400 animate-pulse shrink-0" />
                            <span className="text-[11px] font-medium text-rose-300/90">Live Armed</span>
                        </div>
                    )}
                    {data?.market && <MarketBadge market={data.market} />}

                    {/* Actions */}
                    <button
                        type="button"
                        onClick={load}
                        disabled={loading}
                        className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white border border-slate-700/60 rounded-lg px-3 py-1.5 transition-colors disabled:opacity-50 h-8"
                    >
                        <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
                        Refresh
                    </button>
                    <button
                        type="button"
                        onClick={handleReset}
                        disabled={resetting || loading}
                        className="flex items-center gap-1.5 text-xs text-red-400 hover:text-red-300 border border-red-500/20 rounded-lg px-3 py-1.5 transition-colors disabled:opacity-50 h-8"
                    >
                        <Trash2 size={12} />
                        Reset
                    </button>
                </div>
            </AppHeader>

            <main className="max-w-6xl mx-auto px-6 py-8 space-y-6">
                {error && (
                    <div className="rounded-xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-300">
                        {error}
                    </div>
                )}

                {loading && !data && (
                    <div className="flex items-center justify-center py-20 text-slate-500 text-sm">
                        <RefreshCw size={16} className="animate-spin mr-2" /> Loading...
                    </div>
                )}

                {data && (
                    <>
                        {/* Subtitle hint below header */}
                        <div className="flex items-center gap-2 text-xs text-slate-500">
                            <span>Strategy Paper is your baseline. Layer Alpaca Paper and Alpaca Live as confidence grows.</span>
                        </div>

                        {/* ── Live summary (front and center when live is preferred) ── */}
                        {preferredTrack === "alpaca_live" && alpacaLiveEnabled && (
                            <>
                                {/* Stat grids */}
                                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-xl border border-rose-500/30 bg-rose-950/10 p-3">
                                    <StatCard label="Equity" value={liveEquity != null ? fmtMoney(liveEquity) : "—"} />
                                    <StatCard
                                        label="Unrealized P&L"
                                        value={liveUnrealizedPnlForDisplay != null ? fmtDollar(liveUnrealizedPnlForDisplay) : "—"}
                                        sub="open positions"
                                        color={liveUnrealizedPnlForDisplay != null ? pnlColor(liveUnrealizedPnlForDisplay) : undefined}
                                    />
                                    <StatCard
                                        label="Realized P&L"
                                        value={fmtDollar(liveRealized)}
                                        sub={`${liveTotalTrades} closed trades`}
                                        color={pnlColor(liveRealized)}
                                    />
                                    <StatCard
                                        label="Win Rate"
                                        value={liveWinRate != null ? `${liveWinRate.toFixed(0)}%` : "—"}
                                        sub={liveTotalTrades > 0 ? `${liveWins}W / ${liveLosses}L` : "no closed trades"}
                                        color={liveWinRate != null ? (liveWinRate >= 50 ? "text-emerald-400" : "text-red-400") : undefined}
                                    />
                                </div>
                                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-xl border border-rose-500/30 bg-rose-950/10 p-3">
                                    <StatCard label="Cash" value={liveAccount?.cash != null ? fmtMoney(liveAccount.cash) : "—"} />
                                    <StatCard label="Buying Power" value={liveAccount?.buying_power != null ? fmtMoney(liveAccount.buying_power) : "—"} />
                                    <StatCard label="Open Positions" value={String(alpacaLivePositions.length)} />
                                    <StatCard label="Total Trades" value={String(liveTotalTrades)} />
                                </div>

                                {/* Live open positions — from Alpaca live positions endpoint */}
                                <div className="rounded-xl border border-rose-500/20 overflow-hidden" style={{ background: "rgba(30,41,59,0.7)" }}>
                                    <div className="px-5 py-4 border-b border-white/8 flex items-center gap-2">
                                        <Activity size={14} className="text-rose-400" />
                                        <p className="text-sm font-semibold text-white">Live Open Positions</p>
                                        <span className="ml-auto text-[10px] text-slate-500">{alpacaLivePositions.length} position{alpacaLivePositions.length !== 1 ? "s" : ""}</span>
                                    </div>
                                    {alpacaLivePositions.length > 0 ? (
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-xs">
                                                <thead>
                                                    <tr className="border-b border-white/6 text-[10px] uppercase tracking-wider text-slate-500">
                                                        <th className="px-4 py-2.5 text-left">Symbol</th>
                                                        <th className="px-4 py-2.5 text-left">Side</th>
                                                        <th className="px-4 py-2.5 text-right">Qty</th>
                                                        <th className="px-4 py-2.5 text-right">Avg Entry</th>
                                                        <th className="px-4 py-2.5 text-right">Current</th>
                                                        <th className="px-4 py-2.5 text-right">Holdings</th>
                                                        <th className="px-4 py-2.5 text-right">Unrealized P&L</th>
                                                        <th className="px-4 py-2.5 text-right">Actions</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {alpacaLivePositions.map((pos, i) => {
                                                        const qtyNum = toNumber(pos.qty);
                                                        const entryNum = toNumber(pos.avg_entry_price);
                                                        const currentNum = toNumber(pos.current_price);
                                                        const livePrice = livePrices[pos.symbol]?.price;
                                                        const { pnl: pnlNum, pct: pnlPct } = calculateUnrealizedPnlForLive(pos, livePrice);
                                                        const displayPrice = livePrice ?? toNumber(pos.current_price);
                                                        const sideLower = String(pos.side || "").toLowerCase();
                                                        return (
                                                            <tr key={i} className="border-b border-white/4 hover:bg-white/4 transition-colors">
                                                                <td className="px-4 py-3 font-semibold text-white">{pos.symbol}</td>
                                                                <td className="px-4 py-3">
                                                                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${sideLower === "long" ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>{sideLower}</span>
                                                                </td>
                                                                <td className="px-4 py-3 text-right font-mono text-slate-300">{qtyNum != null ? qtyNum.toFixed(4) : "—"}</td>
                                                                <td className="px-4 py-3 text-right font-mono text-slate-300">{entryNum != null ? `$${entryNum.toFixed(2)}` : "—"}</td>
                                                                <td className="px-4 py-3 text-right font-mono text-slate-300">{currentNum != null ? `$${currentNum.toFixed(2)}` : "—"}</td>
                                                                <td className="px-4 py-3 text-right font-mono text-slate-300">
                                                                    {qtyNum != null && displayPrice != null
                                                                        ? `$${(qtyNum * displayPrice).toFixed(2)}`
                                                                        : "—"}
                                                                </td>
                                                                <td className="px-4 py-3 text-right">
                                                                    <span className={`inline-block rounded px-1.5 py-0.5 border text-[10px] font-semibold ${pnlBg(pnlNum ?? 0)}`}>
                                                                        {fmtDollar(pnlNum ?? 0)} ({fmt(pnlPct ?? 0)}%)
                                                                    </span>
                                                                </td>
                                                                <td className="px-4 py-3 text-right">
                                                                    <button
                                                                        onClick={() => handleCloseAlpacaPosition(pos.symbol)}
                                                                        className="rounded border border-rose-500/30 bg-rose-500/10 px-2 py-1 text-[10px] font-semibold text-rose-400 hover:bg-rose-500/20 transition-colors"
                                                                    >
                                                                        Close
                                                                    </button>
                                                                </td>
                                                            </tr>
                                                        );
                                                    })}
                                                </tbody>
                                            </table>
                                        </div>
                                    ) : (
                                        <div className="px-5 py-6 flex items-center gap-3 text-slate-500 text-sm">
                                            <Minus size={16} /> No open live positions
                                        </div>
                                    )}
                                </div>

                                {/* Live closed trades */}
                                <div className="rounded-xl border border-rose-500/20 overflow-hidden" style={{ background: "rgba(30,41,59,0.7)" }}>
                                    <div className="px-5 py-4 border-b border-white/8 flex items-center gap-2">
                                        <DollarSign size={14} className="text-slate-400" />
                                        <p className="text-sm font-semibold text-white">Live Closed Trades</p>
                                        <span className="ml-auto text-[10px] text-slate-500">
                                            {liveClosedRows.length} trade{liveClosedRows.length !== 1 ? "s" : ""}
                                        </span>
                                    </div>
                                    {liveClosedRows.length > 0 ? (
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-xs">
                                                <thead>
                                                    <tr className="border-b border-white/6 text-[10px] uppercase tracking-wider text-slate-500">
                                                        <th className="px-4 py-2.5 text-left">Symbol</th>
                                                        <th className="px-4 py-2.5 text-right">Buy Price</th>
                                                        <th className="px-4 py-2.5 text-right">Sell Price</th>
                                                        <th className="px-4 py-2.5 text-right">Qty</th>
                                                        <th className="px-4 py-2.5 text-right">Realized P&L</th>
                                                        <th className="px-4 py-2.5 text-left">Closed</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {liveClosedRows.map((row, i) => (
                                                        <tr key={i} className="border-b border-white/4 hover:bg-white/4 transition-colors">
                                                            <td className="px-4 py-3 font-semibold text-white">{row.symbol}</td>
                                                            <td className="px-4 py-3 text-right font-mono text-slate-300">${row.buyPrice.toFixed(2)}</td>
                                                            <td className="px-4 py-3 text-right font-mono text-slate-300">${row.sellPrice.toFixed(2)}</td>
                                                            <td className="px-4 py-3 text-right font-mono text-slate-300">{row.qty.toFixed(4)}</td>
                                                            <td className="px-4 py-3 text-right">
                                                                <span className={`inline-block rounded px-1.5 py-0.5 border text-[10px] font-semibold ${pnlBg(row.pnl)}`}>
                                                                    {fmtDollar(row.pnl)}
                                                                </span>
                                                            </td>
                                                            <td className="px-4 py-3 text-slate-400">{fmtDate(row.closedAt)}</td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    ) : (
                                        <div className="px-5 py-6 flex items-center gap-3 text-slate-500 text-sm">
                                            <Minus size={16} /> No closed live trades yet
                                        </div>
                                    )}
                                    {/* Show More button for older live trades */}
                                    {liveClosedRows.length > 0 && !liveOlderTradesLoadingFirst && (
                                        <div className="px-5 py-4 border-t border-white/6 flex justify-center">
                                            <button
                                                type="button"
                                                onClick={loadLiveOlderTrades}
                                                disabled={loadingLiveOlder}
                                                className="inline-flex items-center gap-2 rounded-lg border border-rose-700/40 bg-rose-900/20 px-4 py-2 text-xs font-medium text-rose-300 hover:text-rose-100 hover:bg-rose-800/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                            >
                                                {loadingLiveOlder ? (
                                                    <>
                                                        <RefreshCw size={12} className="animate-spin" /> Loading...
                                                    </>
                                                ) : (
                                                    <>
                                                        Show More Older Trades
                                                    </>
                                                )}
                                            </button>
                                        </div>
                                    )}
                                </div>
                            </>
                        )}

                        {/* ── Paper summary (top when paper preferred, below when live is preferred) ── */}
                        {preferredTrack !== "alpaca_live" && (
                            <>
                                <div className={`grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-xl ${preferredTrack === "strategy_paper" ? "border border-sky-500/20 p-3" : ""}`}>
                                    <StatCard
                                        label="Net P&L"
                                        value={fmtDollar(s!.total_pnl)}
                                        sub={`${fmt(s!.total_pnl_pct)}% of deployed`}
                                        color={pnlColor(s!.total_pnl)}
                                    />
                                    <StatCard
                                        label="Realized"
                                        value={fmtDollar(s!.realized_pnl)}
                                        sub={`${s!.closed_trades} closed trades`}
                                        color={pnlColor(s!.realized_pnl)}
                                    />
                                    <StatCard
                                        label="Open P&L"
                                        value={fmtDollar(derivedOpenPnl)}
                                        sub={`${s!.open_positions} open positions`}
                                        color={pnlColor(derivedOpenPnl)}
                                    />
                                    <StatCard
                                        label="Win Rate"
                                        value={`${s!.win_rate.toFixed(0)}%`}
                                        sub={`${s!.win_count}W / ${s!.loss_count}L`}
                                        color={s!.win_rate >= 50 ? "text-emerald-400" : "text-red-400"}
                                    />
                                </div>
                                <div className={`grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-xl ${preferredTrack === "strategy_paper" ? "border border-sky-500/20 p-3" : ""}`}>
                                    <StatCard label="Avg Win" value={fmtDollar(s!.avg_win)} color="text-emerald-400" />
                                    <StatCard label="Avg Loss" value={fmtDollar(s!.avg_loss)} color="text-red-400" />
                                    <StatCard label="Total Deployed" value={`$${s!.total_deployed.toFixed(0)}`} />
                                    <StatCard label="Total Trades" value={String(s!.total_trades)} />
                                </div>
                            </>
                        )}

                        {/* Broker accounts */}
                        {(configuredBrokerModes.length > 0 || alpacaOrders.length > 0) && (
                            <div className="rounded-xl border border-white/8 p-5 space-y-4" style={{ background: "rgba(30,41,59,0.7)" }}>
                                <div className="flex items-center gap-2">
                                    <Activity size={14} className="text-slate-400" />
                                    <p className="text-sm font-semibold text-white">Broker Accounts</p>
                                    <p className="text-[10px] text-slate-500 ml-auto">Tracked separately from the internal strategy paper ledger</p>
                                </div>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    {brokerModes.map((mode) => {
                                        const account = alpacaAccounts[mode];
                                        const configured = !!alpacaStatus?.secrets?.[mode]?.configured;
                                        return (
                                            <div key={mode} className={`rounded-xl border p-4 space-y-3 ${preferredTrack === (mode === "paper" ? "alpaca_paper" : "alpaca_live")
                                                ? mode === "paper"
                                                    ? "border-cyan-500/30 bg-cyan-950/10"
                                                    : "border-rose-500/30 bg-rose-950/10"
                                                : "border-white/8"
                                                }`}>
                                                <div className="flex items-center gap-2">
                                                    <span className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider ${modeBadgeClass(mode)}`}>
                                                        Alpaca {modeLabel(mode)}
                                                    </span>
                                                    {!configured && (
                                                        <span className="text-[10px] text-slate-500">Not configured</span>
                                                    )}
                                                    {mode === "live" && alpacaLiveEnabled && (
                                                        <span className="text-[10px] text-rose-300 ml-auto">execution enabled</span>
                                                    )}
                                                </div>
                                                {account ? (
                                                    <div className="grid grid-cols-2 gap-3 text-xs">
                                                        <StatCard label="Equity" value={fmtMoney(account.equity ?? account.portfolio_value)} />
                                                        <StatCard label="Cash" value={fmtMoney(account.cash)} />
                                                        <StatCard label="Buying Power" value={fmtMoney(account.buying_power)} />
                                                        <StatCard label="Last Close" value={fmtMoney(account.last_equity)} />
                                                    </div>
                                                ) : (
                                                    <div className="rounded-lg border border-dashed border-white/8 px-4 py-5 text-sm text-slate-500">
                                                        {configured ? "Account details unavailable right now" : "No credentials saved for this account"}
                                                    </div>
                                                )}
                                                {account?.status && (
                                                    <p className="text-[11px] text-slate-500">
                                                        Status: <span className="text-slate-300">{String(account.status)}</span>
                                                    </p>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        )}

                        {/* ── Equity curves: live first when live is preferred ── */}
                        {preferredTrack === "alpaca_live" ? (
                            <>
                                {alpacaHistories.live && (
                                    <div className="rounded-xl p-5 border border-rose-500/40" style={{ background: "rgba(30,41,59,0.7)" }}>
                                        <div className="flex items-center gap-2 mb-4">
                                            <span className="w-2 h-2 rounded-full shrink-0 bg-rose-400" />
                                            <p className="text-sm font-semibold text-white">Alpaca Live Equity</p>
                                            <span className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider font-medium ml-1 ${modeBadgeClass("live")}`}>LIVE</span>
                                            <p className="text-[10px] text-slate-500 ml-auto">30-day broker account equity</p>
                                        </div>
                                        <AlpacaEquityCurve history={alpacaHistories.live} />
                                    </div>
                                )}

                            </>
                        ) : (
                            <>
                                {/* Paper curve first */}
                                <div className={`rounded-xl p-5 ${preferredTrack === "strategy_paper" ? "border border-sky-500/30" : "border border-white/8"}`} style={{ background: "rgba(30,41,59,0.7)" }}>
                                    <div className="flex items-center gap-2 mb-4">
                                        <BarChart2 size={14} className="text-slate-400" />
                                        <p className="text-sm font-semibold text-white">Strategy Paper Equity</p>
                                        <p className="text-[10px] text-slate-500 ml-auto">Cumulative realized P&L over closed trades</p>
                                    </div>
                                    <EquityCurve data={data.equity_curve} />
                                </div>

                                {brokerModes
                                    .filter((mode) => !!alpacaHistories[mode])
                                    .map((mode) => (
                                        <div key={mode} className={`rounded-xl p-5 ${mode === "live"
                                            ? "border border-rose-600/30"
                                            : preferredTrack === "alpaca_paper" ? "border border-cyan-500/40" : "border border-sky-500/20"
                                            }`} style={{ background: "rgba(30,41,59,0.7)" }}>
                                            <div className="flex items-center gap-2 mb-4">
                                                <span className={`w-2 h-2 rounded-full shrink-0 ${mode === "live" ? "bg-rose-400" : "bg-sky-400"}`} />
                                                <p className="text-sm font-semibold text-white">Alpaca {modeLabel(mode)} Equity</p>
                                                <span className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider font-medium ml-1 ${modeBadgeClass(mode)}`}>
                                                    {mode.toUpperCase()}
                                                </span>
                                                <p className="text-[10px] text-slate-500 ml-auto">30-day broker account equity</p>
                                            </div>
                                            <AlpacaEquityCurve history={alpacaHistories[mode]!} />
                                        </div>
                                    ))}
                            </>
                        )}

                        {/* ── Paper Trading: collapsible expander when Alpaca Live is active ── */}
                        {preferredTrack === "alpaca_live" ? (
                            <div className="rounded-xl border border-slate-700/40 overflow-hidden" style={{ background: "rgba(15,23,42,0.5)" }}>
                                <button
                                    type="button"
                                    onClick={() => setPaperExpanded(p => !p)}
                                    className="w-full px-5 py-4 flex items-center justify-between hover:bg-white/4 transition-colors"
                                >
                                    <div className="flex items-center gap-2">
                                        <BarChart2 size={14} className="text-slate-500" />
                                        <span className="text-sm font-medium text-slate-400">Paper Trading</span>
                                        <span className="text-[10px] text-slate-600 ml-1">strategy simulation</span>
                                    </div>
                                    <ChevronDown size={14} className={`text-slate-500 transition-transform duration-200 ${paperExpanded ? "rotate-180" : ""}`} />
                                </button>
                                {paperExpanded && (
                                    <div className="border-t border-slate-700/40 px-4 pb-4 pt-4 space-y-4">
                                        {/* Paper stats */}
                                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                                            <StatCard label="Net P&L" value={fmtDollar(s!.total_pnl)} sub={`${fmt(s!.total_pnl_pct)}% of deployed`} color={pnlColor(s!.total_pnl)} />
                                            <StatCard label="Realized" value={fmtDollar(s!.realized_pnl)} sub={`${s!.closed_trades} closed`} color={pnlColor(s!.realized_pnl)} />
                                            <StatCard label="Win Rate" value={`${s!.win_rate.toFixed(0)}%`} sub={`${s!.win_count}W / ${s!.loss_count}L`} color={s!.win_rate >= 50 ? "text-emerald-400" : "text-red-400"} />
                                            <StatCard label="Open P&L" value={fmtDollar(derivedOpenPnl)} sub={`${s!.open_positions} open`} color={pnlColor(derivedOpenPnl)} />
                                        </div>
                                        {/* Paper equity curves */}
                                        <div className="rounded-xl p-5 border border-white/8" style={{ background: "rgba(30,41,59,0.7)" }}>
                                            <div className="flex items-center gap-2 mb-4">
                                                <BarChart2 size={14} className="text-slate-400" />
                                                <p className="text-sm font-semibold text-white">Strategy Paper Equity</p>
                                                <p className="text-[10px] text-slate-500 ml-auto">Cumulative realized P&L</p>
                                            </div>
                                            <EquityCurve data={data.equity_curve} />
                                        </div>
                                        {alpacaHistories.paper && (
                                            <div className="rounded-xl p-5 border border-sky-500/20" style={{ background: "rgba(30,41,59,0.7)" }}>
                                                <div className="flex items-center gap-2 mb-4">
                                                    <span className="w-2 h-2 rounded-full shrink-0 bg-sky-400" />
                                                    <p className="text-sm font-semibold text-white">Alpaca Paper Equity</p>
                                                    <span className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider font-medium ml-1 ${modeBadgeClass("paper")}`}>PAPER</span>
                                                    <p className="text-[10px] text-slate-500 ml-auto">30-day broker account equity</p>
                                                </div>
                                                <AlpacaEquityCurve history={alpacaHistories.paper} />
                                            </div>
                                        )}
                                        {/* Paper open positions */}
                                        {data.open_positions.length > 0 ? (
                                            <div className="rounded-xl border border-white/8 overflow-hidden" style={{ background: "rgba(30,41,59,0.7)" }}>
                                                <div className="px-5 py-4 border-b border-white/8 flex items-center gap-2">
                                                    <Activity size={14} className="text-emerald-400" />
                                                    <p className="text-sm font-semibold text-white">Strategy Paper Open Positions</p>
                                                    <span className="ml-auto text-[10px] text-slate-500">{data.open_positions.length} position{data.open_positions.length !== 1 ? "s" : ""}</span>
                                                </div>
                                                <div className="overflow-x-auto">
                                                    <table className="w-full text-xs">
                                                        <thead>
                                                            <tr className="border-b border-white/6 text-[10px] uppercase tracking-wider text-slate-500">
                                                                <th className="px-4 py-2.5 text-left">Ticker</th>
                                                                <th className="px-4 py-2.5 text-left">Direction</th>
                                                                <th className="px-4 py-2.5 text-left">Leverage</th>
                                                                <th className="px-4 py-2.5 text-left">Type</th>
                                                                <th className="px-4 py-2.5 text-left">Window</th>
                                                                <th className="px-4 py-2.5 text-right">Entry</th>
                                                                <th className="px-4 py-2.5 text-right">Current</th>
                                                                <th className="px-4 py-2.5 text-right">P&L</th>
                                                                <th className="px-4 py-2.5 text-left">Entered</th>
                                                                <th className="px-4 py-2.5 text-left">Session</th>
                                                                <th className="px-4 py-2.5 text-right">Actions</th>
                                                            </tr>
                                                        </thead>
                                                        <tbody>
                                                            {data.open_positions.map((pos) => {
                                                                const livePrice = livePrices[pos.execution_ticker]?.price;
                                                                const { pnl, pct } = calculateUnrealizedPnl(pos, livePrice);
                                                                const displayPrice = livePrice ?? pos.current_price;
                                                                return (
                                                                    <tr key={pos.id} className="border-b border-white/4 hover:bg-white/4 transition-colors">
                                                                        <td className="px-4 py-3 font-semibold text-white">
                                                                            {pos.execution_ticker}
                                                                            <span className="text-slate-500 font-normal ml-1 text-[10px]">({pos.underlying})</span>
                                                                        </td>
                                                                        <td className="px-4 py-3"><DirectionBadge signal={pos.signal_type} /></td>
                                                                        <td className="px-4 py-3 text-slate-300">{pos.leverage}</td>
                                                                        <td className="px-4 py-3"><ConvictionBadge conviction={pos.conviction_level} tradingType={pos.trading_type} /></td>
                                                                        <td className="px-4 py-3"><WindowBadge active={pos.window_active} remaining={pos.window_remaining_minutes} /></td>
                                                                        <td className="px-4 py-3 text-right font-mono text-slate-300">${pos.entry_price.toFixed(2)}</td>
                                                                        <td className="px-4 py-3 text-right font-mono text-slate-200">${displayPrice.toFixed(2)}</td>
                                                                        <td className="px-4 py-3 text-right">
                                                                            <span className={`inline-block rounded px-1.5 py-0.5 border text-[10px] font-semibold ${pnlBg(pnl)}`}>
                                                                                {fmtDollar(pnl)} ({fmt(pct)}%)
                                                                            </span>
                                                                        </td>
                                                                        <td className="px-4 py-3 text-slate-400">{fmtDate(pos.entered_at)}</td>
                                                                        <td className="px-4 py-3"><SessionBadge session={pos.market_session} /></td>
                                                                        <td className="px-4 py-3 text-right">
                                                                            <button
                                                                                onClick={() => handleClosePosition(pos.id)}
                                                                                className="rounded border border-rose-500/30 bg-rose-500/10 px-2 py-1 text-[10px] font-semibold text-rose-400 hover:bg-rose-500/20 transition-colors"
                                                                            >
                                                                                Close
                                                                            </button>
                                                                        </td>
                                                                    </tr>
                                                                );
                                                            })}
                                                        </tbody>
                                                    </table>
                                                </div>
                                            </div>
                                        ) : (
                                            <div className="rounded-xl border border-white/8 px-5 py-6 flex items-center gap-3 text-slate-500 text-sm" style={{ background: "rgba(30,41,59,0.7)" }}>
                                                <Minus size={16} /> No open positions
                                            </div>
                                        )}
                                        {/* Paper closed trades */}
                                        {(data.closed_trades.length > 0 || olderTrades.length > 0) ? (
                                            <div className="rounded-xl border border-white/8 overflow-hidden" style={{ background: "rgba(30,41,59,0.7)" }}>
                                                <div className="px-5 py-4 border-b border-white/8 flex items-center gap-2">
                                                    <DollarSign size={14} className="text-slate-400" />
                                                    <p className="text-sm font-semibold text-white">Strategy Paper Closed Trades</p>
                                                    <span className="ml-auto text-[10px] text-slate-500">
                                                        {data.closed_trades.length + olderTrades.length} trades
                                                        {totalOlderAvailable != null && (
                                                            <span className="ml-1">({totalOlderAvailable} older available)</span>
                                                        )}
                                                    </span>
                                                </div>
                                                <div className="overflow-x-auto">
                                                    <table className="w-full text-xs">
                                                        <thead>
                                                            <tr className="border-b border-white/6 text-[10px] uppercase tracking-wider text-slate-500">
                                                                <th className="px-4 py-2.5 text-left">Ticker</th>
                                                                <th className="px-4 py-2.5 text-left">Direction</th>
                                                                <th className="px-4 py-2.5 text-left">Leverage</th>
                                                                <th className="px-4 py-2.5 text-right">Entry</th>
                                                                <th className="px-4 py-2.5 text-right">Exit</th>
                                                                <th className="px-4 py-2.5 text-right">Realized P&L</th>
                                                                <th className="px-4 py-2.5 text-left">Closed At</th>
                                                                <th className="px-4 py-2.5 text-left">Session</th>
                                                            </tr>
                                                        </thead>
                                                        <tbody>
                                                            {[...data.closed_trades, ...olderTrades].map((trade) => (
                                                                <tr key={trade.id} className="border-b border-white/4 hover:bg-white/4 transition-colors">
                                                                    <td className="px-4 py-3 font-semibold text-white">
                                                                        {trade.execution_ticker}
                                                                        <span className="text-slate-500 font-normal ml-1 text-[10px]">({trade.underlying})</span>
                                                                    </td>
                                                                    <td className="px-4 py-3"><DirectionBadge signal={trade.signal_type} /></td>
                                                                    <td className="px-4 py-3 text-slate-300">{trade.leverage}</td>
                                                                    <td className="px-4 py-3 text-right font-mono text-slate-300">${trade.entry_price.toFixed(2)}</td>
                                                                    <td className="px-4 py-3 text-right font-mono text-slate-300">${trade.exit_price.toFixed(2)}</td>
                                                                    <td className="px-4 py-3 text-right">
                                                                        <span className={`inline-block rounded px-1.5 py-0.5 border text-[10px] font-semibold ${pnlBg(trade.realized_pnl)}`}>
                                                                            {fmtDollar(trade.realized_pnl)} ({fmt(trade.realized_pnl_pct)}%)
                                                                        </span>
                                                                    </td>
                                                                    <td className="px-4 py-3 text-slate-400">{fmtDate(trade.exited_at)}</td>
                                                                    <td className="px-4 py-3"><SessionBadge session={trade.market_session} /></td>
                                                                </tr>
                                                            ))}
                                                        </tbody>
                                                    </table>
                                                </div>
                                                {data.closed_trades.length > 0 && !olderTradesLoadingFirst && (
                                                    <div className="px-5 py-4 border-t border-white/6 flex justify-center">
                                                        <button
                                                            type="button"
                                                            onClick={loadOlderTrades}
                                                            disabled={loadingOlder}
                                                            className="inline-flex items-center gap-2 rounded-lg border border-slate-700/60 bg-slate-800/60 px-4 py-2 text-xs font-medium text-slate-300 hover:text-white hover:bg-slate-700/60 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                                        >
                                                            {loadingOlder ? <><RefreshCw size={12} className="animate-spin" /> Loading...</> : <>Show More Older Trades</>}
                                                        </button>
                                                    </div>
                                                )}
                                            </div>
                                        ) : (
                                            <div className="rounded-xl border border-white/8 px-5 py-6 flex items-center gap-3 text-slate-500 text-sm" style={{ background: "rgba(30,41,59,0.7)" }}>
                                                <Minus size={16} /> No closed trades yet &mdash; trades close when the signal changes or flips direction
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        ) : (
                            <>
                                {/* Open positions */}
                                {data.open_positions.length > 0 && (
                                    <div className="rounded-xl border border-white/8 overflow-hidden" style={{ background: "rgba(30,41,59,0.7)" }}>
                                        <div className="px-5 py-4 border-b border-white/8 flex items-center gap-2">
                                            <Activity size={14} className="text-emerald-400" />
                                            <p className="text-sm font-semibold text-white">Strategy Paper Open Positions</p>
                                            <span className="ml-auto text-[10px] text-slate-500">{data.open_positions.length} position{data.open_positions.length !== 1 ? "s" : ""}</span>
                                        </div>
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-xs">
                                                <thead>
                                                    <tr className="border-b border-white/6 text-[10px] uppercase tracking-wider text-slate-500">
                                                        <th className="px-4 py-2.5 text-left">Ticker</th>
                                                        <th className="px-4 py-2.5 text-left">Direction</th>
                                                        <th className="px-4 py-2.5 text-left">Leverage</th>
                                                        <th className="px-4 py-2.5 text-left">Type</th>
                                                        <th className="px-4 py-2.5 text-left">Window</th>
                                                        <th className="px-4 py-2.5 text-right">Entry</th>
                                                        <th className="px-4 py-2.5 text-right">Current</th>
                                                        <th className="px-4 py-2.5 text-right">P&L</th>
                                                        <th className="px-4 py-2.5 text-left">Entered</th>
                                                        <th className="px-4 py-2.5 text-left">Session</th>
                                                        <th className="px-4 py-2.5 text-right">Actions</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {data.open_positions.map((pos) => {
                                                        const livePrice = livePrices[pos.execution_ticker]?.price;
                                                        const { pnl, pct } = calculateUnrealizedPnl(pos, livePrice);
                                                        const displayPrice = livePrice ?? pos.current_price;
                                                        return (
                                                            <tr key={pos.id} className="border-b border-white/4 hover:bg-white/4 transition-colors">
                                                                <td className="px-4 py-3 font-semibold text-white">
                                                                    {pos.execution_ticker}
                                                                    <span className="text-slate-500 font-normal ml-1 text-[10px]">({pos.underlying})</span>
                                                                </td>
                                                                <td className="px-4 py-3"><DirectionBadge signal={pos.signal_type} /></td>
                                                                <td className="px-4 py-3 text-slate-300">{pos.leverage}</td>
                                                                <td className="px-4 py-3"><ConvictionBadge conviction={pos.conviction_level} tradingType={pos.trading_type} /></td>
                                                                <td className="px-4 py-3"><WindowBadge active={pos.window_active} remaining={pos.window_remaining_minutes} /></td>
                                                                <td className="px-4 py-3 text-right font-mono text-slate-300">${pos.entry_price.toFixed(2)}</td>
                                                                <td className="px-4 py-3 text-right font-mono text-slate-200">${displayPrice.toFixed(2)}</td>
                                                                <td className="px-4 py-3 text-right">
                                                                    <span className={`inline-block rounded px-1.5 py-0.5 border text-[10px] font-semibold ${pnlBg(pnl)}`}>
                                                                        {fmtDollar(pnl)} ({fmt(pct)}%)
                                                                    </span>
                                                                </td>
                                                                <td className="px-4 py-3 text-slate-400">{fmtDate(pos.entered_at)}</td>
                                                                <td className="px-4 py-3"><SessionBadge session={pos.market_session} /></td>
                                                                <td className="px-4 py-3 text-right">
                                                                    <button
                                                                        onClick={() => handleClosePosition(pos.id)}
                                                                        className="rounded border border-rose-500/30 bg-rose-500/10 px-2 py-1 text-[10px] font-semibold text-rose-400 hover:bg-rose-500/20 transition-colors"
                                                                    >
                                                                        Close
                                                                    </button>
                                                                </td>
                                                            </tr>
                                                        );
                                                    })}
                                                </tbody>
                                            </table>
                                        </div>
                                    </div>
                                )}

                                {data.open_positions.length === 0 && (
                                    <div className="rounded-xl border border-white/8 px-5 py-6 flex items-center gap-3 text-slate-500 text-sm" style={{ background: "rgba(30,41,59,0.7)" }}>
                                        <Minus size={16} /> No open positions
                                    </div>
                                )}

                                {/* Closed trades */}
                                {(data.closed_trades.length > 0 || olderTrades.length > 0) && (
                                    <div className="rounded-xl border border-white/8 overflow-hidden" style={{ background: "rgba(30,41,59,0.7)" }}>
                                        <div className="px-5 py-4 border-b border-white/8 flex items-center gap-2">
                                            <DollarSign size={14} className="text-slate-400" />
                                            <p className="text-sm font-semibold text-white">Strategy Paper Closed Trades</p>
                                            <span className="ml-auto text-[10px] text-slate-500">
                                                {data.closed_trades.length + olderTrades.length} trades
                                                {totalOlderAvailable != null && (
                                                    <span className="ml-1">({totalOlderAvailable} older available)</span>
                                                )}
                                            </span>
                                        </div>
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-xs">
                                                <thead>
                                                    <tr className="border-b border-white/6 text-[10px] uppercase tracking-wider text-slate-500">
                                                        <th className="px-4 py-2.5 text-left">Ticker</th>
                                                        <th className="px-4 py-2.5 text-left">Direction</th>
                                                        <th className="px-4 py-2.5 text-left">Leverage</th>
                                                        <th className="px-4 py-2.5 text-right">Entry</th>
                                                        <th className="px-4 py-2.5 text-right">Exit</th>
                                                        <th className="px-4 py-2.5 text-right">Realized P&L</th>
                                                        <th className="px-4 py-2.5 text-left">Closed At</th>
                                                        <th className="px-4 py-2.5 text-left">Session</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {[...data.closed_trades, ...olderTrades].map((trade) => (
                                                        <tr key={trade.id} className="border-b border-white/4 hover:bg-white/4 transition-colors">
                                                            <td className="px-4 py-3 font-semibold text-white">
                                                                {trade.execution_ticker}
                                                                <span className="text-slate-500 font-normal ml-1 text-[10px]">({trade.underlying})</span>
                                                            </td>
                                                            <td className="px-4 py-3"><DirectionBadge signal={trade.signal_type} /></td>
                                                            <td className="px-4 py-3 text-slate-300">{trade.leverage}</td>
                                                            <td className="px-4 py-3 text-right font-mono text-slate-300">${trade.entry_price.toFixed(2)}</td>
                                                            <td className="px-4 py-3 text-right font-mono text-slate-300">${trade.exit_price.toFixed(2)}</td>
                                                            <td className="px-4 py-3 text-right">
                                                                <span className={`inline-block rounded px-1.5 py-0.5 border text-[10px] font-semibold ${pnlBg(trade.realized_pnl)}`}>
                                                                    {fmtDollar(trade.realized_pnl)} ({fmt(trade.realized_pnl_pct)}%)
                                                                </span>
                                                            </td>
                                                            <td className="px-4 py-3 text-slate-400">{fmtDate(trade.exited_at)}</td>
                                                            <td className="px-4 py-3"><SessionBadge session={trade.market_session} /></td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                        {data.closed_trades.length > 0 && !olderTradesLoadingFirst && (
                                            <div className="px-5 py-4 border-t border-white/6 flex justify-center">
                                                <button
                                                    type="button"
                                                    onClick={loadOlderTrades}
                                                    disabled={loadingOlder}
                                                    className="inline-flex items-center gap-2 rounded-lg border border-slate-700/60 bg-slate-800/60 px-4 py-2 text-xs font-medium text-slate-300 hover:text-white hover:bg-slate-700/60 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                                >
                                                    {loadingOlder ? (
                                                        <>
                                                            <RefreshCw size={12} className="animate-spin" /> Loading...
                                                        </>
                                                    ) : (
                                                        <>
                                                            Show More Older Trades
                                                        </>
                                                    )}
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                )}

                                {data.closed_trades.length === 0 && olderTrades.length === 0 && (
                                    <div className="rounded-xl border border-white/8 px-5 py-6 flex items-center gap-3 text-slate-500 text-sm" style={{ background: "rgba(30,41,59,0.7)" }}>
                                        <Minus size={16} /> No closed trades yet &mdash; trades close when the signal changes or flips direction
                                    </div>
                                )}
                            </>
                        )}
                    </>
                )}

                {/* Alpaca order log */}
                {alpacaOrders.length > 0 && (
                    <div className="rounded-xl border border-slate-700/60 overflow-hidden" style={{ background: "rgba(30,41,59,0.7)" }}>
                        <div className="px-5 py-4 border-b border-white/8 flex items-center gap-2">
                            <span className="w-2 h-2 rounded-full shrink-0 bg-slate-400" />
                            <p className="text-sm font-semibold text-white">Alpaca Order Log</p>
                            {brokerModes.map((mode) => (
                                <span key={mode} className={`ml-1 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider font-medium ${modeBadgeClass(mode)}`}>
                                    {mode.toUpperCase()} {brokerOrderCounts[mode] ?? 0}
                                </span>
                            ))}
                            <span className="ml-auto text-[10px] text-slate-500">{alpacaOrders.length} orders</span>
                        </div>
                        {latestLiveOrderError && (
                            <div className="mx-5 mt-4 rounded-lg border border-amber-600/40 bg-amber-900/20 px-4 py-3 text-xs text-amber-200">
                                Latest live order failure: {latestLiveOrderError.symbol} {latestLiveOrderError.side.toUpperCase()} was blocked.
                                {" "}
                                <span className="text-amber-300">{latestLiveOrderError.error_message}</span>
                            </div>
                        )}
                        {latestPaperOrderError && (
                            <div className="mx-5 mt-4 rounded-lg border border-amber-600/40 bg-amber-900/20 px-4 py-3 text-xs text-amber-200">
                                Latest paper order blocked: {latestPaperOrderError.symbol} {latestPaperOrderError.side.toUpperCase()}
                                {" — "}
                                <span className="text-amber-300">{latestPaperOrderError.error_message}</span>
                            </div>
                        )}
                        <div className="overflow-x-auto">
                            <table className="w-full text-xs">
                                <thead>
                                    <tr className="border-b border-white/6 text-[10px] uppercase tracking-wider text-slate-500">
                                        <th className="px-4 py-2.5 text-left">Symbol</th>
                                        <th className="px-4 py-2.5 text-left">Side</th>
                                        <th className="px-4 py-2.5 text-right">Notional / Qty</th>
                                        <th className="px-4 py-2.5 text-left">Type</th>
                                        <th className="px-4 py-2.5 text-left">Status</th>
                                        <th className="px-4 py-2.5 text-right">Fill Price</th>
                                        <th className="px-4 py-2.5 text-left">Mode</th>
                                        <th className="px-4 py-2.5 text-left">Submitted</th>
                                        <th className="px-4 py-2.5 text-left">Filled</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {alpacaOrders.map((order) => {
                                        const isFilled = order.status === "filled";
                                        const isSkipped = order.status === "skipped";
                                        const isError = order.status === "error" || (!isFilled && !isSkipped && !!order.error_message);
                                        const statusColor = isFilled
                                            ? "text-emerald-300"
                                            : isError
                                                ? "text-red-400"
                                                : isSkipped
                                                    ? "text-amber-400"
                                                    : "text-slate-400";
                                        return (
                                            <tr key={order.id} className="border-b border-white/4 hover:bg-white/4 transition-colors">
                                                <td className="px-4 py-3 font-semibold text-white">{order.symbol}</td>
                                                <td className="px-4 py-3">
                                                    <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold border ${order.side === "buy"
                                                        ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/20"
                                                        : "bg-red-500/10 text-red-300 border-red-500/20"
                                                        }`}>
                                                        {order.side.toUpperCase()}
                                                    </span>
                                                </td>
                                                <td className="px-4 py-3 text-right font-mono text-slate-300">
                                                    {order.notional != null ? `$${order.notional.toFixed(2)}` : order.qty != null ? `${order.qty} sh` : "—"}
                                                </td>
                                                <td className="px-4 py-3 text-slate-400 capitalize">{order.order_type}</td>
                                                <td className={`px-4 py-3 ${statusColor}`}>
                                                    {(isError || isSkipped) && order.error_message ? (
                                                        <span title={order.error_message} className="cursor-help underline decoration-dotted">
                                                            {isSkipped ? `skipped: ${order.error_message}` : `error: ${order.error_message}`}
                                                        </span>
                                                    ) : (
                                                        order.status ?? "—"
                                                    )}
                                                </td>
                                                <td className="px-4 py-3 text-right font-mono text-slate-300">
                                                    {order.filled_avg_price != null ? `$${order.filled_avg_price.toFixed(2)}` : "—"}
                                                </td>
                                                <td className="px-4 py-3">
                                                    <span className={`rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wider ${order.trading_mode === "live"
                                                        ? "bg-rose-600/20 text-rose-300"
                                                        : "bg-slate-700 text-slate-400"
                                                        }`}>
                                                        {order.trading_mode}
                                                    </span>
                                                </td>
                                                <td className="px-4 py-3 text-slate-500">{fmtDate(order.submitted_at)}</td>
                                                <td className="px-4 py-3 text-slate-500">{order.filled_at ? fmtDate(order.filled_at) : "—"}</td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </main>
        </div>
    );
}
