import { useState } from "react";

interface NewsListProps {
  items: any[];
  onItemClick: (item: any) => void;
}

export function NewsList({ items, onItemClick }: NewsListProps) {
  const getSourceIcon = (source: string) => {
    switch (source) {
      case "edgar": return "📄";  // SEC filing
      case "rss": return "📰";   // RSS article
      case "truth_social": return "🐦";  // Truth Social
      default: return "📌";
    }
  };

  const formatDate = (dateStr: string) => {
    if (!dateStr) return "N/A";
    const date = new Date(dateStr);
    return date.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  };

  if (items.length === 0) {
    return (
      <div className="text-center py-8 text-gray-500">
        No items found. Try adjusting your filters.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {items.map((item) => (
        <div
          key={item.id}
          onClick={() => onItemClick(item)}
          className="bg-white p-4 rounded-lg shadow hover:shadow-md cursor-pointer transition"
        >
          <div className="flex items-start justify-between">
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-lg">{getSourceIcon(item.source)}</span>
                <span className="text-xs font-medium text-gray-500 uppercase">
                  {item.source_label}
                </span>
                {item.symbol && (
                  <span className="px-2 py-1 bg-blue-100 text-blue-800 text-xs font-medium rounded">
                    {item.symbol}
                  </span>
                )}
              </div>
              <h3 className="text-sm font-medium text-gray-900 mb-1">
                {item.title}
              </h3>
              <p className="text-xs text-gray-600 line-clamp-2">
                {item.summary}
              </p>
            </div>
            <div className="text-xs text-gray-500 ml-4">
              {formatDate(item.published_at)}
            </div>
          </div>
          
          {item.processed && (
            <div className="mt-2 flex items-center gap-2">
              <span className="px-2 py-1 bg-green-100 text-green-800 text-xs rounded">
                ✓ Processed
              </span>
              {item.details?.has_summary && (
                <span className="px-2 py-1 bg-purple-100 text-purple-800 text-xs rounded">
                  AI Summary
                </span>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
