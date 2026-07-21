"use client";

import { useState, useEffect } from "react";
import { NewsFilter } from "./components/NewsFilter";
import { NewsList } from "./components/NewsList";
import { NewsDetail } from "./components/NewsDetail";

export default function NewsPage() {
  const [items, setItems] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [selectedItem, setSelectedItem] = useState<any>(null);
  
  // Filters
  const [symbol, setSymbol] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [source, setSource] = useState("");
  const [limit] = useState(50);
  const [offset, setOffset] = useState(0);

  const fetchNews = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (symbol) params.append("symbol", symbol);
      if (startDate) params.append("start_date", startDate);
      if (endDate) params.append("end_date", endDate);
      if (source) params.append("source", source);
      params.append("limit", limit.toString());
      params.append("offset", offset.toString());

      const res = await fetch(`/api/v1/news?${params.toString()}`);
      const data = await res.json();
      
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch (err) {
      console.error("Failed to fetch news:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchNews();
  }, [symbol, startDate, endDate, source, offset]);

  const handleFilterChange = (newFilters: any) => {
    setSymbol(newFilters.symbol || "");
    setStartDate(newFilters.startDate || "");
    setEndDate(newFilters.endDate || "");
    setSource(newFilters.source || "");
    setOffset(0);
  };

  const handleItemClick = (item: any) => {
    setSelectedItem(item);
  };

  const handleCloseDetail = () => {
    setSelectedItem(null);
  };

  return (
    <div className="news-page">
      <h1 className="text-2xl font-bold mb-4">📰 News & Filings</h1>
      
      <NewsFilter
        symbol={symbol}
        startDate={startDate}
        endDate={endDate}
        source={source}
        onFilterChange={handleFilterChange}
      />
      
      <div className="mt-4">
        <p className="text-sm text-gray-600">
          Showing {items.length} of {total} items
        </p>
      </div>
      
      {loading ? (
        <div className="text-center py-8">Loading...</div>
      ) : (
        <NewsList
          items={items}
          onItemClick={handleItemClick}
        />
      )}
      
      {selectedItem && (
        <NewsDetail
          item={selectedItem}
          onClose={handleCloseDetail}
        />
      )}
    </div>
  );
}
