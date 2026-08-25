"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Trash2, TrendingUp, TrendingDown, FileText, BarChart2, RefreshCw, Info } from "lucide-react";
import { SourceDetailModal } from "./components/SourceDetailModal";

interface TradingOpportunity {
  id: number;
  symbol: string;
  score: number;
  sentiment: string;
  reasoning: string;
  source_count: number;
  signal_count: number;
  is_pump_and_dump: boolean;
  flags: string[];
  sources: string[];
  added_at: string;
  status: string;
  notes: string | null;
}

interface TradeListSummary {
  status_counts: { [key: string]: number };
  average_score: number;
  total_opportunities: number;
  top_opportunities: Array<{
    symbol: string;
    score: number;
    sentiment: string;
  }>;
}

export default function TradeListPage() {
  const [opportunities, setOpportunities] = useState<TradingOpportunity[]>([]);
  const [summary, setSummary] = useState<TradeListSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("watchlist");
  const [minScore, setMinScore] = useState(0);
  const [newCount, setNewCount] = useState(0);
  const [discovering, setDiscovering] = useState(false);
  const [sourceModalOpp, setSourceModalOpp] = useState<TradingOpportunity | null>(null);

  // Check if opportunity is new (added in last 24 hours)
  const isNew = (addedAt: string) => {
    const addedDate = new Date(addedAt);
    const now = new Date();
    const hoursDiff = (now.getTime() - addedDate.getTime()) / (1000 * 60 * 60);
    return hoursDiff <= 24;
  };

  const fetchTradeList = async () => {
    setLoading(true);
    setError(null);
    
    try {
      const [listResponse, summaryResponse] = await Promise.all([
        fetch(`/api/v1/trade-list/?status=${statusFilter}&min_score=${minScore}`),
        fetch('/api/v1/trade-list/summary'),
      ]);
      
      if (!listResponse.ok) {
        throw new Error(`API error: ${listResponse.status}`);
      }
      
      if (!summaryResponse.ok) {
        throw new Error(`Summary API error: ${summaryResponse.status}`);
      }
      
      const listData = await listResponse.json();
      const summaryData = await summaryResponse.json();
      
      setOpportunities(listData);
      setSummary(summaryData);
      
      // Count new opportunities (added in last 24 hours)
      const newOpps = listData.filter((opp: TradingOpportunity) => isNew(opp.added_at));
      setNewCount(newOpps.length);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch trade list");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTradeList();
    // Poll for new opportunities every 2 minutes
    const interval = setInterval(fetchTradeList, 120000);
    return () => clearInterval(interval);
  }, [statusFilter, minScore]);

  const runDiscovery = async () => {
    setDiscovering(true);
    setError(null);
    try {
      // Note trailing slash: the route is /api/v1/discover/ ; hitting it
      // without the slash relies on a 307 redirect that some clients drop.
      const response = await fetch('/api/v1/discover/?auto_add=true');
      if (!response.ok) {
        throw new Error(`Discovery API error: ${response.status}`);
      }
      const data = await response.json();
      // Refresh the list so newly auto-added opportunities appear immediately.
      await fetchTradeList();
      alert(`🔍 Discovery complete: ${data.opportunities_found} opportunities found`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Discovery failed");
    } finally {
      setDiscovering(false);
    }
  };

  const removeFromTradeList = async (opportunityId: number, symbol: string) => {
    if (!confirm(`Remove ${symbol} from trade list?`)) {
      return;
    }
    
    try {
      const response = await fetch(`/api/v1/trade-list/${opportunityId}`, {
        method: 'DELETE'
      });
      
      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }
      
      alert(`✅ ${symbol} removed from trade list`);
      fetchTradeList();
    } catch (err) {
      alert(`Error: ${err instanceof Error ? err.message : 'Failed to remove'}`);
    }
  };

  const updateStatus = async (opportunityId: number, symbol: string, newStatus: string) => {
    try {
      const response = await fetch(`/api/v1/trade-list/${opportunityId}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      
      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }
      
      alert(`✅ ${symbol} status updated to ${newStatus}`);
      fetchTradeList();
    } catch (err) {
      alert(`Error: ${err instanceof Error ? err.message : 'Failed to update status'}`);
    }
  };

  const getScoreColor = (score: number) => {
    if (score >= 70) return "text-green-400 bg-green-500/20 border-green-500/50";
    if (score >= 50) return "text-blue-400 bg-blue-500/20 border-blue-500/50";
    if (score >= 30) return "text-yellow-400 bg-yellow-500/20 border-yellow-500/50";
    return "text-red-400 bg-red-500/20 border-red-500/50";
  };

  return (
    <div className="min-h-screen bg-slate-950">
      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h1 className="text-3xl font-bold text-white mb-2 flex items-center gap-3">
                <BarChart2 size={28} className="text-blue-400" />
                Trade List & Watchlist
                {newCount > 0 && (
                  <span className="ml-2 px-3 py-1 bg-green-500 text-white text-sm font-bold rounded-full animate-pulse">
                    {newCount} NEW
                  </span>
                )}
              </h1>
              <p className="text-slate-400">
                Track and manage discovered trading opportunities
              </p>
            </div>
            <button
              onClick={runDiscovery}
              disabled={discovering}
              className="bg-green-600 hover:bg-green-700 disabled:opacity-60 disabled:cursor-not-allowed text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
              title="Run discovery to find new trading opportunities"
            >
              {discovering ? "⏳ Discovering…" : "🔍 Discover New"}
            </button>
          </div>
          
          {/* Info Box */}
          <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-4 flex items-start gap-3">
            <Info size={20} className="text-blue-400 mt-0.5 shrink-0" />
            <div className="text-sm text-blue-300">
              <p className="font-semibold mb-1">What is the Trade List?</p>
              <p>This list is <strong>automatically populated</strong> with new trading opportunities discovered from news analysis. The system scans recent news articles, extracts mentioned tickers, and adds high-scoring opportunities here automatically.</p>
              <p className="mt-2">You can also manually add tickers, or remove/add them to your personal watchlist. This is separate from "Tracked Symbols" in Admin settings (which controls ongoing sentiment monitoring).</p>
            </div>
          </div>
        </div>

        {/* Summary Stats */}
        {summary && (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
              <div className="text-2xl font-bold text-white">
                {summary.total_opportunities}
              </div>
              <div className="text-sm text-slate-400">Total Opportunities</div>
            </div>
            
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
              <div className="text-2xl font-bold text-blue-400">
                {summary.status_counts?.watchlist || 0}
              </div>
              <div className="text-sm text-slate-400">In Watchlist</div>
            </div>
            
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
              <div className="text-2xl font-bold text-green-400">
                {summary.status_counts?.trading || 0}
              </div>
              <div className="text-sm text-slate-400">Active Trades</div>
            </div>
            
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
              <div className="text-2xl font-bold text-purple-400">
                {summary.average_score?.toFixed(1) || 0}
              </div>
              <div className="text-sm text-slate-400">Avg Score</div>
            </div>
          </div>
        )}

        {/* Filters */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 mb-8">
          <div className="flex items-center gap-4 flex-wrap">
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                Status Filter
              </label>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-white focus:border-blue-500 focus:outline-none"
              >
                <option value="watchlist">Watchlist</option>
                <option value="trading">Trading</option>
                <option value="closed">Closed</option>
              </select>
            </div>
            
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                Min Score
              </label>
              <input
                type="number"
                value={minScore}
                onChange={(e) => setMinScore(Number(e.target.value))}
                className="bg-slate-800 border border-slate-700 rounded-md px-3 py-2 w-24 text-white focus:border-blue-500 focus:outline-none"
                min={0}
                max={100}
              />
            </div>
            
            <button
              onClick={fetchTradeList}
              className="bg-blue-600 text-white px-6 py-2 rounded-lg hover:bg-blue-700 mt-6 transition-colors"
            >
              🔄 Refresh
            </button>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="bg-red-500/10 border border-red-500/50 text-red-400 px-4 py-3 rounded-lg mb-8">
            Error: {error}
          </div>
        )}

        {/* Loading */}
        {loading && (
          <div className="text-center py-12">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
            <p className="mt-2 text-slate-400">Loading trade list...</p>
          </div>
        )}

        {/* Opportunities List */}
        {!loading && (
          <>
            {opportunities.length > 0 ? (
              <div className="space-y-4">
                <h2 className="text-xl font-semibold text-white">
                  {statusFilter === "watchlist" ? "Watchlist" : 
                   statusFilter === "trading" ? "Active Trades" : "Closed Trades"}
                  <span className="text-sm font-normal text-slate-400 ml-2">
                    {opportunities.length} opportunities
                  </span>
                </h2>
                
                {opportunities.map((opp) => (
                  <div key={opp.id} className="bg-slate-900 border border-slate-800 rounded-xl p-6 hover:border-slate-700 transition-colors">
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center gap-3 mb-3">
                          <h3 className="text-xl font-bold text-white">
                            {opp.symbol}
                          </h3>
                          {isNew(opp.added_at) && (
                            <span className="px-2 py-1 bg-green-500 text-white text-xs font-bold rounded animate-pulse">
                              NEW
                            </span>
                          )}
                          <span className={`px-3 py-1 rounded-full text-sm font-bold border ${getScoreColor(opp.score)}`}>
                            {opp.score}/100
                          </span>
                          {opp.is_pump_and_dump && (
                            <span className="px-3 py-1 rounded-full text-sm font-medium bg-red-500/20 text-red-400 border border-red-500/50 flex items-center gap-1">
                              ⚠️ P&D Risk
                            </span>
                          )}
                          <span className={`px-3 py-1 rounded-full text-xs font-medium ${
                            opp.status === 'watchlist' ? 'bg-slate-500/20 text-slate-300' :
                            opp.status === 'trading' ? 'bg-green-500/20 text-green-400' :
                            'bg-gray-500/20 text-gray-400'
                          }`}>
                            {opp.status}
                          </span>
                        </div>
                        
                        <p className="text-slate-300 mb-3 leading-relaxed">{opp.reasoning}</p>
                        
                        <div className="flex items-center gap-4 text-sm text-slate-400 mb-2">
                          <span className="flex items-center gap-1">
                            <TrendingUp size={14} />
                            {opp.signal_count} signals
                          </span>
                          <button
                            onClick={() => setSourceModalOpp(opp)}
                            className="flex items-center gap-1 text-blue-400 hover:text-blue-300 hover:underline cursor-pointer"
                            title="Click to see source details"
                          >
                            <FileText size={14} />
                            {opp.source_count} source{opp.source_count !== 1 ? "s" : ""}
                          </button>
                          <span>📅 {new Date(opp.added_at).toLocaleDateString()}</span>
                        </div>
                        
                        {opp.flags.length > 0 && (
                          <div className="mt-3 p-3 bg-red-500/10 border border-red-500/30 rounded-lg">
                            <p className="text-sm text-red-400 font-semibold mb-1">
                              Risk Flags:
                            </p>
                            <p className="text-sm text-red-300">
                              {opp.flags.join(" • ")}
                            </p>
                          </div>
                        )}
                      </div>
                      
                      <div className="ml-4 flex flex-col gap-2 min-w-[160px]">
                        {opp.status === 'watchlist' && (
                          <>
                            <button
                              onClick={() => updateStatus(opp.id, opp.symbol, "trading")}
                              className="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                            >
                              Start Trading
                            </button>
                            <button
                              onClick={() => updateStatus(opp.id, opp.symbol, "closed")}
                              className="bg-slate-700 hover:bg-slate-600 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                            >
                              Mark Closed
                            </button>
                          </>
                        )}
                        <button
                          onClick={() => removeFromTradeList(opp.id, opp.symbol)}
                          className="bg-red-600/20 hover:bg-red-600/30 text-red-400 border border-red-500/50 px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
                        >
                          <Trash2 size={14} />
                          Remove
                        </button>
                        
                        <Link
                          href={`/news?symbol=${opp.symbol}`}
                          className="text-center px-4 py-2 rounded-lg text-sm font-medium bg-slate-800 hover:bg-slate-700 text-blue-400 transition-colors"
                        >
                          View News →
                        </Link>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-12 text-center">
                <p className="text-slate-400 text-lg mb-4">
                  No opportunities in your {statusFilter}
                </p>
                <p className="text-slate-500 text-sm">
                  New opportunities are automatically added when discovered in news analysis. Run discovery to find new trades.
                </p>
              </div>
            )}
          </>
        )}

        {/* Source Detail Modal */}
        {sourceModalOpp && (
          <SourceDetailModal
            opportunityId={sourceModalOpp.id}
            symbol={sourceModalOpp.symbol}
            onClose={() => setSourceModalOpp(null)}
          />
        )}
      </div>
    </div>
  );
}
