"use client";

import { useState } from "react";

interface NewsFilterProps {
  symbol: string;
  startDate: string;
  endDate: string;
  source: string;
  onFilterChange: (filters: any) => void;
}

export function NewsFilter({ symbol, startDate, endDate, source, onFilterChange }: NewsFilterProps) {
  const baseSymbols = ["NVDA", "ORCL", "MSFT", "AVGO", "MU", "NET", "NOW", "SPCX"];
  // Ensure the currently-selected symbol (e.g., passed via ?symbol= from the
  // Trade List "View News" link) is always an option, even if it's not in the
  // default tracked list.
  const symbols = symbol && !baseSymbols.includes(symbol)
    ? [symbol, ...baseSymbols]
    : baseSymbols;
  const sources = [
    { value: "", label: "All Sources" },
    { value: "edgar", label: "SEC EDGAR" },
    { value: "rss", label: "RSS Feeds" },
    { value: "truth_social", label: "Truth Social" },
    { value: "insider", label: "SEC Insider (OpenInsider)" },
  ];

  const handleChange = (key: string, value: string) => {
    onFilterChange({
      symbol,
      startDate,
      endDate,
      source,
      [key]: value,
    });
  };

  return (
    <div className="bg-white p-4 rounded-lg shadow mb-4">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* Symbol Filter */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Symbol</label>
          <select
            value={symbol}
            onChange={(e) => handleChange("symbol", e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md text-gray-900"
          >
            <option value="">All Symbols</option>
            {symbols.filter(s => s).map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>

        {/* Date Range */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Start Date</label>
          <input
            type="date"
            value={startDate}
            onChange={(e) => handleChange("startDate", e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md text-gray-900"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">End Date</label>
          <input
            type="date"
            value={endDate}
            onChange={(e) => handleChange("endDate", e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md text-gray-900"
          />
        </div>

        {/* Source Filter */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Source</label>
          <select
            value={source}
            onChange={(e) => handleChange("source", e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md text-gray-900"
          >
            {sources.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
}
