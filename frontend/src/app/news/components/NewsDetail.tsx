"use client";

import { useState } from "react";

interface NewsDetailProps {
  item: any;
  onClose: () => void;
}

export function NewsDetail({ item, onClose }: NewsDetailProps) {
  const [showFullText, setShowFullText] = useState(false);

  const formatDate = (dateStr: string) => {
    if (!dateStr) return "N/A";
    const date = new Date(dateStr);
    return date.toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const getSourceIcon = (source: string) => {
    switch (source) {
      case "edgar": return "📄";  // SEC filing
      case "rss": return "📰";   // RSS article
      case "truth_social": return "🐦";  // Truth Social
      default: return "📌";
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-y-auto m-4">
        {/* Header */}
        <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-2xl">{getSourceIcon(item.source)}</span>
            <span className="text-sm font-medium text-gray-500 uppercase">
              {item.source_label}
            </span>
            {item.symbol && (
              <span className="px-2 py-1 bg-blue-100 text-blue-800 text-xs font-medium rounded">
                {item.symbol}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div className="px-6 py-4">
          <h2 className="text-xl font-bold text-gray-900 mb-2">
            {item.title}
          </h2>
          
          <div className="text-sm text-gray-500 mb-4">
            {formatDate(item.published_at)}
            {item.url && (
              <a
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                className="ml-4 text-blue-600 hover:text-blue-800"
              >
                View Original →
              </a>
            )}
          </div>

          {/* LLM Summary */}
          {item.summary && item.summary !== "No summary available" && (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4">
              <h3 className="text-sm font-semibold text-blue-900 mb-2">
                🤖 AI Summary
              </h3>
              <p className="text-sm text-blue-800 whitespace-pre-wrap">
                {item.summary}
              </p>
            </div>
          )}

          {/* Processing Status */}
          <div className="flex items-center gap-2">
            {item.processed ? (
              <span className="px-2 py-1 bg-green-100 text-green-800 text-xs rounded">
                ✓ Processed
              </span>
            ) : (
              <span className="px-2 py-1 bg-yellow-100 text-yellow-800 text-xs rounded">
                ⏳ Pending Processing
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
