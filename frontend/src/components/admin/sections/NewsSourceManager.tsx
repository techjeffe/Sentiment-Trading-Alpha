"use client";

import { useState, useEffect, useCallback } from "react";

type NewsSource = {
    name: string;
    url: string;
    source_type: string;
    category: string;
    enabled: boolean;
    priority: number;
    fetch_interval_minutes: number;
};

type CategoryData = {
    category_name: string;
    sources: NewsSource[];
    enabled_count: number;
    total_count: number;
};

type NewsSourceManagerProps = {
    onSourcesChange?: (sources: Record<string, NewsSource[]>) => void;
};

export function NewsSourceManager({ onSourcesChange }: NewsSourceManagerProps) {
    const [categories, setCategories] = useState<Record<string, CategoryData>>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());

    const fetchSources = useCallback(async () => {
        try {
            setLoading(true);
            const response = await fetch("/api/v1/news-sources");
            if (!response.ok) throw new Error("Failed to fetch news sources");
            const data = await response.json();
            setCategories(data);
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load news sources");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchSources();
    }, [fetchSources]);

    const toggleSource = async (name: string, enabled: boolean) => {
        try {
            const response = await fetch("/api/v1/news-sources/toggle", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, enabled }),
            });
            if (!response.ok) throw new Error("Failed to toggle source");
            
            // Update local state
            setCategories(prev => {
                const next = { ...prev };
                for (const catName in next) {
                    next[catName] = {
                        ...next[catName],
                        sources: next[catName].sources.map(s => 
                            s.name === name ? { ...s, enabled } : s
                        ),
                        enabled_count: next[catName].sources.filter(s => 
                            s.name === name ? enabled : s.enabled
                        ).length,
                    };
                }
                return next;
            });
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to toggle source");
        }
    };

    const toggleCategory = async (category: string, enabled: boolean) => {
        try {
            const response = await fetch("/api/v1/news-sources/toggle-category", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ category, enabled }),
            });
            if (!response.ok) throw new Error("Failed to toggle category");
            
            // Update local state
            setCategories(prev => ({
                ...prev,
                [category]: {
                    ...prev[category],
                    sources: prev[category].sources.map(s => ({ ...s, enabled })),
                    enabled_count: enabled ? prev[category].total_count : 0,
                },
            }));
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to toggle category");
        }
    };

    const toggleCategoryExpand = (category: string) => {
        setExpandedCategories(prev => {
            const next = new Set(prev);
            if (next.has(category)) {
                next.delete(category);
            } else {
                next.add(category);
            }
            return next;
        });
    };

    if (loading) {
        return (
        <div className="flex items-center justify-center py-12">
            <div className="text-slate-400">Loading news sources...</div>
        </div>
    );
    }

    if (error) {
        return (
            <div className="rounded-xl border border-red-800 bg-red-950/20 p-6">
                <p className="text-red-400">Error: {error}</p>
            </div>
        );
    }

    const totalSources = Object.values(categories).reduce((sum, cat) => sum + cat.total_count, 0);
    const enabledSources = Object.values(categories).reduce((sum, cat) => sum + cat.enabled_count, 0);

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <div>
                    <h3 className="text-sm font-semibold text-slate-200">News Sources</h3>
                    <p className="text-xs text-slate-400 mt-1">
                        {enabledSources} of {totalSources} sources enabled across {Object.keys(categories).length} categories
                    </p>
                </div>
            </div>

            <div className="space-y-3">
                {Object.entries(categories).map(([categoryName, category]) => (
                    <div key={categoryName} className="rounded-xl border border-slate-800 bg-slate-900/50 overflow-hidden">
                        {/* Category Header */}
                        <div 
                            className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-slate-800/50 transition-colors"
                            onClick={() => toggleCategoryExpand(categoryName)}
                        >
                            <div className="flex items-center gap-3">
                                <span className={`transform transition-transform ${expandedCategories.has(categoryName) ? 'rotate-90' : ''}`}>
                                    ▶
                                </span>
                                <div>
                                    <p className="text-sm font-medium text-slate-200 capitalize">
                                        {categoryName.replace(/_/g, ' ')}
                                    </p>
                                    <p className="text-xs text-slate-500">
                                        {category.enabled_count}/{category.total_count} enabled
                                    </p>
                                </div>
                            </div>
                            <div className="flex items-center gap-3">
                                <span className="text-xs text-slate-400">
                                    {category.enabled_count === category.total_count ? 'All On' : 
                                     category.enabled_count === 0 ? 'All Off' : 'Partial'}
                                </span>
                                <label 
                                    className="relative inline-flex items-center cursor-pointer"
                                    onClick={(e) => e.stopPropagation()}
                                >
                                    <input
                                        type="checkbox"
                                        checked={category.enabled_count === category.total_count}
                                        onChange={(e) => {
                                            e.stopPropagation();
                                            toggleCategory(categoryName, e.target.checked);
                                        }}
                                        className="sr-only peer"
                                    />
                                    <div className="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 relative"></div>
                                </label>
                            </div>
                        </div>

                        {/* Category Sources (Expanded) */}
                        {expandedCategories.has(categoryName) && (
                            <div className="border-t border-slate-800 px-4 py-3 space-y-2">
                                {category.sources.map((source) => (
                                    <label 
                                        key={source.name}
                                        className="flex items-center justify-between gap-4 px-3 py-2 rounded-lg hover:bg-slate-800/30 transition-colors"
                                    >
                                        <div className="flex-1 min-w-0">
                                            <p className="text-sm text-slate-200 truncate">{source.name}</p>
                                            <p className="text-xs text-slate-500 truncate">{source.url}</p>
                                            <div className="flex items-center gap-2 mt-1">
                                                <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                                                    source.source_type === 'direct_rss' 
                                                        ? 'bg-green-900/30 text-green-400' 
                                                        : 'bg-blue-900/30 text-blue-400'
                                                }`}>
                                                    {source.source_type === 'direct_rss' ? 'RSS' : 'Google News'}
                                                </span>
                                                <span className="text-[10px] text-slate-600">
                                                    Priority: {source.priority}
                                                </span>
                                            </div>
                                        </div>
                                        <input
                                            type="checkbox"
                                            checked={source.enabled}
                                            onChange={() => toggleSource(source.name, !source.enabled)}
                                            className="flex-shrink-0"
                                        />
                                    </label>
                                ))}
                            </div>
                        )}
                    </div>
                ))}
            </div>
        </div>
    );
}
