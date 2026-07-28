"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Info, Search, TrendingUp, FileText, AlertTriangle, CheckCircle, X, ArrowRight } from "lucide-react";

interface Opportunity {
  symbol: string;
  score: number;
  sentiment: string;
  reasoning: string;
  source_count: number;
  signal_count: number;
  is_pump_and_dump: boolean;
  flags: string[];
  sources: string[];
}

interface DiscoveryResponse {
  total_articles_processed: number;
  tickers_discovered: number;
  opportunities_found: number;
  opportunities: Opportunity[];
  execution_time_seconds: number;
}

export default function DiscoveryPage() {
  const [data, setData] = useState<DiscoveryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [minScore, setMinScore] = useState(30);
  const [maxResults, setMaxResults] = useState(20);
  const [addingToTradeList, setAddingToTradeList] = useState<string | null>(null);
  const [selectedOpportunity, setSelectedOpportunity] = useState<Opportunity | null>(null);

  const fetchOpportunities = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/v1/discover/?min_score=${minScore}&max_results=${maxResults}`);
      if (!response.ok) throw new Error(`API error: ${response.status}`);
      const result = await response.json();
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch opportunities");
    } finally {
      setLoading(false);
    }
  };

  const addToTradeList = async (opp: Opportunity) => {
    setAddingToTradeList(opp.symbol);
    try {
      // First, get current config
      const configResponse = await fetch('/api/v1/config');
      if (!configResponse.ok) {
        throw new Error('Failed to get config');
      }
      const config = await configResponse.json();
      
      // Add symbol to custom_symbols if not already there
      const customSymbols = config.custom_symbols || [];
      if (!customSymbols.includes(opp.symbol)) {
        const updatedSymbols = [...customSymbols, opp.symbol];
        
        // Update config with new symbol
        const updateResponse = await fetch('/api/v1/config', {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            custom_symbols: updatedSymbols,
          }),
        });
        
        if (!updateResponse.ok) {
          const errorData = await updateResponse.json();
          console.warn('Failed to add to tracked symbols:', errorData);
        } else {
          console.log('Added to tracked symbols');
        }
      }
      
      // Then add to trade list for tracking
      const response = await fetch('/api/v1/trade-list/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: opp.symbol,
          score: opp.score,
          sentiment: opp.sentiment,
          reasoning: opp.reasoning,
          source_count: opp.source_count,
          signal_count: opp.signal_count,
          is_pump_and_dump: opp.is_pump_and_dump,
          flags: opp.flags,
          sources: opp.sources,
        }),
      });
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to add to trade list');
      }
      alert(`✅ ${opp.symbol} added to tracked symbols and trade list!`);
    } catch (err) {
      alert(`Error: ${err instanceof Error ? err.message : 'Failed to add to trade list'}`);
    } finally {
      setAddingToTradeList(null);
    }
  };

  useEffect(() => { fetchOpportunities(); }, []);

  const getScoreColor = (score: number) => {
    if (score >= 70) return "text-green-400 bg-green-500/20 border-green-500/50";
    if (score >= 50) return "text-blue-400 bg-blue-500/20 border-blue-500/50";
    if (score >= 30) return "text-yellow-400 bg-yellow-500/20 border-yellow-500/50";
    return "text-red-400 bg-red-500/20 border-red-500/50";
  };

  const getScoreLabel = (score: number) => {
    if (score >= 70) return "Strong Buy Signal";
    if (score >= 50) return "Moderate Signal";
    if (score >= 30) return "Weak Signal";
    return "Noise";
  };

  return (
    <div className="min-h-screen bg-slate-950">
      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-2 flex items-center gap-3">
            <Search size={28} className="text-blue-400" />
            Automatic Discovery
          </h1>
          <p className="text-slate-400">Tickers automatically discovered from news articles and SEC filings</p>
        </div>

        {/* Controls */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 mb-8">
          <div className="flex items-center gap-4 flex-wrap">
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">Min Score</label>
              <input type="number" value={minScore} onChange={(e) => setMinScore(Number(e.target.value))}
                className="bg-slate-800 border border-slate-700 rounded-md px-3 py-2 w-24 text-white focus:border-blue-500 focus:outline-none" min={0} max={100} />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">Max Results</label>
              <input type="number" value={maxResults} onChange={(e) => setMaxResults(Number(e.target.value))}
                className="bg-slate-800 border border-slate-700 rounded-md px-3 py-2 w-24 text-white focus:border-blue-500 focus:outline-none" min={1} max={100} />
            </div>
            <button onClick={fetchOpportunities} disabled={loading}
              className="bg-blue-600 text-white px-6 py-2 rounded-lg hover:bg-blue-700 disabled:bg-slate-700 disabled:text-slate-400 mt-6 transition-colors">
              {loading ? "Discovering..." : "🔍 Discover"}
            </button>
          </div>
        </div>

        {/* Error */}
        {error && <div className="bg-red-500/10 border border-red-500/50 text-red-400 px-4 py-3 rounded-lg mb-8">Error: {error}</div>}

        {/* Loading */}
        {loading && (
          <div className="text-center py-12">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
            <p className="mt-2 text-slate-400">Scanning...</p>
          </div>
        )}

        {/* Results */}
        {data && !loading && (
          <>
            {/* Stats */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
                <div className="text-2xl font-bold text-white">{data.total_articles_processed}</div>
                <div className="text-sm text-slate-400">Articles Processed</div>
              </div>
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
                <div className="text-2xl font-bold text-blue-400">{data.tickers_discovered}</div>
                <div className="text-sm text-slate-400">Tickers Discovered</div>
              </div>
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
                <div className="text-2xl font-bold text-green-400">{data.opportunities_found}</div>
                <div className="text-sm text-slate-400">Opportunities Found</div>
              </div>
            </div>

            {/* Opportunities */}
            {data.opportunities.length > 0 ? (
              <div className="space-y-4">
                {data.opportunities.map((opp) => (
                  <div key={opp.symbol} className="bg-slate-900 border border-slate-800 rounded-xl p-6 hover:border-slate-700 transition-colors">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1">
                        <div className="flex items-center gap-3 mb-3">
                          <h3 className="text-xl font-bold text-white">{opp.symbol}</h3>
                          <span className={`px-3 py-1 rounded-full text-sm font-bold border ${getScoreColor(opp.score)}`}>
                            {opp.score}/100
                          </span>
                          {opp.is_pump_and_dump && (
                            <span className="px-3 py-1 rounded-full text-sm font-medium bg-red-500/20 text-red-400 border border-red-500/50">
                              <AlertTriangle size={14} className="inline mr-1" /> P&D Risk
                            </span>
                          )}
                        </div>
                        <p className="text-slate-300 mb-3">{opp.reasoning}</p>
                        <div className="flex items-center gap-4 text-sm text-slate-400">
                          <span><TrendingUp size={14} className="inline mr-1" />{opp.signal_count} signals</span>
                          <span><FileText size={14} className="inline mr-1" />{opp.source_count} sources</span>
                        </div>
                      </div>
                      <div className="flex flex-col gap-2 min-w-[160px]">
                        <button onClick={() => addToTradeList(opp)} disabled={addingToTradeList === opp.symbol || opp.is_pump_and_dump}
                          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                            opp.is_pump_and_dump ? "bg-slate-800 text-slate-500 cursor-not-allowed" : "bg-green-600 hover:bg-green-700 text-white"
                          }`}>
                          {addingToTradeList === opp.symbol ? "Adding..." : opp.is_pump_and_dump ? "⚠️ High Risk" : "📝 Add to Trade List"}
                        </button>
                        <button onClick={() => setSelectedOpportunity(opp)}
                          className="px-4 py-2 rounded-lg text-sm font-medium bg-slate-800 hover:bg-slate-700 text-blue-400 transition-colors flex items-center justify-center gap-2">
                          View Details <ArrowRight size={14} />
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-12 text-center">
                <p className="text-slate-400 text-lg">No opportunities found with score ≥ {minScore}</p>
              </div>
            )}
          </>
        )}
      </div>

      {/* Detail Modal - Shows WHY scored */}
      {selectedOpportunity && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => setSelectedOpportunity(null)}>
          <div className="bg-slate-900 border border-slate-700 rounded-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="sticky top-0 bg-slate-900 border-b border-slate-700 p-6 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <h2 className="text-2xl font-bold text-white">{selectedOpportunity.symbol}</h2>
                <span className={`px-3 py-1 rounded-full text-sm font-bold border ${getScoreColor(selectedOpportunity.score)}`}>
                  {selectedOpportunity.score}/100
                </span>
                <span className="text-xs text-slate-400">{getScoreLabel(selectedOpportunity.score)}</span>
              </div>
              <button onClick={() => setSelectedOpportunity(null)} className="text-slate-400 hover:text-white">
                <X size={24} />
              </button>
            </div>

            <div className="p-6 space-y-6">
              {/* Why This Score */}
              <div className="bg-slate-800 rounded-lg p-4">
                <h3 className="text-sm font-semibold text-slate-300 mb-3">Why This Score?</h3>
                <p className="text-slate-300 leading-relaxed">{selectedOpportunity.reasoning}</p>
              </div>

              {/* Signal Analysis */}
              <div>
                <h3 className="text-sm font-semibold text-slate-300 mb-3">Signal Analysis</h3>
                <div className="grid grid-cols-2 gap-3">
                  <div className="bg-slate-800 rounded-lg p-3">
                    <p className="text-xs text-slate-500 mb-1">Signal Count</p>
                    <p className="text-xl font-bold text-white">{selectedOpportunity.signal_count}</p>
                  </div>
                  <div className="bg-slate-800 rounded-lg p-3">
                    <p className="text-xs text-slate-500 mb-1">Source Count</p>
                    <p className="text-xl font-bold text-white">{selectedOpportunity.source_count}</p>
                  </div>
                </div>
              </div>

              {/* Sources */}
              <div>
                <h3 className="text-sm font-semibold text-slate-300 mb-3">Detection Sources</h3>
                <div className="flex flex-wrap gap-2">
                  {selectedOpportunity.sources.map((source, i) => (
                    <span key={i} className="px-3 py-1.5 bg-slate-800 rounded-lg text-sm text-slate-300 border border-slate-700">
                      {source}
                    </span>
                  ))}
                </div>
              </div>

              {/* Risk Assessment */}
              <div>
                <h3 className="text-sm font-semibold text-slate-300 mb-3">Risk Assessment</h3>
                {selectedOpportunity.is_pump_and_dump ? (
                  <div className="bg-red-500/10 border border-red-500/50 rounded-lg p-4">
                    <p className="text-red-400 font-semibold mb-2"><AlertTriangle size={16} className="inline mr-1" /> Pump & Dump Risk Detected</p>
                    <p className="text-sm text-red-300">This stock shows signs of potential pump-and-dump activity. Exercise extreme caution.</p>
                  </div>
                ) : (
                  <div className="bg-green-500/10 border border-green-500/50 rounded-lg p-4">
                    <p className="text-green-400 font-semibold mb-2"><CheckCircle size={16} className="inline mr-1" /> No Major Risk Flags</p>
                    <p className="text-sm text-green-300">No pump-and-dump indicators detected for this ticker.</p>
                  </div>
                )}
                {selectedOpportunity.flags.length > 0 && (
                  <div className="mt-3 bg-yellow-500/10 border border-yellow-500/50 rounded-lg p-4">
                    <p className="text-yellow-400 font-semibold mb-2">Warning Flags:</p>
                    <ul className="space-y-1">
                      {selectedOpportunity.flags.map((flag, i) => (
                        <li key={i} className="text-sm text-yellow-300">• {flag}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              {/* Recommendation */}
              <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-4">
                <h3 className="text-sm font-semibold text-blue-300 mb-2">Recommendation</h3>
                {selectedOpportunity.score >= 70 ? (
                  <p className="text-sm text-blue-300"><strong>Strong candidate</strong> - High-confidence signals with confirmed catalysts. Consider adding to trade list.</p>
                ) : selectedOpportunity.score >= 50 ? (
                  <p className="text-sm text-blue-300"><strong>Moderate interest</strong> - Some positive signals but lacking strong confirmation. Monitor for additional catalysts.</p>
                ) : (
                  <p className="text-sm text-blue-300"><strong>Low priority</strong> - Limited signals detected. Monitor but don't prioritize for trading.</p>
                )}
              </div>

              {/* Actions */}
              <div className="flex gap-3 pt-4 border-t border-slate-700">
                <button onClick={() => { addToTradeList(selectedOpportunity); setSelectedOpportunity(null); }}
                  disabled={selectedOpportunity.is_pump_and_dump}
                  className={`flex-1 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    selectedOpportunity.is_pump_and_dump ? "bg-slate-800 text-slate-500 cursor-not-allowed" : "bg-green-600 hover:bg-green-700 text-white"
                  }`}>
                  {selectedOpportunity.is_pump_and_dump ? "⚠️ High Risk" : "📝 Add to Trade List"}
                </button>
                <Link href={`/news?symbol=${selectedOpportunity.symbol}`}
                  className="flex-1 text-center px-4 py-2 rounded-lg text-sm font-medium bg-slate-800 hover:bg-slate-700 text-blue-400 transition-colors">
                  View News
                </Link>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
