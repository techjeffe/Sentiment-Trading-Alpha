"use client";

import { normalizeFeedUrl, normalizeArticleLimit } from "@/lib/constants/feed-utils";
import { AppConfig } from "@/lib/utils/config-normalizer";
import { NewsSourceManager } from "./NewsSourceManager";

type RssSectionProps = {
    config: AppConfig;
    setConfig: React.Dispatch<React.SetStateAction<AppConfig>>;
    depthOptions: Array<{
        key: AppConfig["rss_article_detail_mode"];
        label: string;
        tagline: string;
        pipeline: string;
    }>;
    enabledFeeds: Set<string>;
    toggleFeed: (url: string) => void;
    updateCustomFeed: (index: number, value: string) => void;
    updateCustomFeedLabel: (url: string, value: string) => void;
    toggleCustomFeedTracked: (url: string) => void;
    customFeedSlots: Array<{ url: string; label: string }>;
    updateArticleLimit: (key: "light" | "normal" | "detailed", value: string) => void;
};

export function RssSection({
    config, setConfig, depthOptions, enabledFeeds, toggleFeed,
    updateCustomFeed, updateCustomFeedLabel, toggleCustomFeedTracked, customFeedSlots, updateArticleLimit,
}: RssSectionProps) {

    return (
        <section id="rss" className="scroll-mt-24 rounded-2xl border border-slate-800 bg-slate-900/70 p-5 space-y-5">
            <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-slate-200">RSS Sources</h2>
                <span className="text-xs text-slate-500 font-mono">
                    {config.rss_article_limits[config.rss_article_detail_mode]} articles/feed · {depthOptions.find(o => o.key === config.rss_article_detail_mode)?.label}
                </span>
            </div>

            <div>
                <p className="text-xs text-slate-400 mb-3">Articles per feed — by depth</p>
                <p className="text-[11px] text-slate-500 mb-3">
                    How many articles to fetch from each feed at each depth level. Total ingested = articles/feed × active feeds.
                    The active depth ({depthOptions.find(o => o.key === config.rss_article_detail_mode)?.label}) is highlighted.
                </p>
                <div className="grid grid-cols-3 gap-3">
                    {(["light", "normal", "detailed"] as const).map((depth) => (
                        <label key={depth} className={`block rounded-xl border p-3 ${config.rss_article_detail_mode === depth ? "border-blue-700/60 bg-blue-950/20" : "border-slate-800 bg-slate-950/40"}`}>
                            <span className={`block text-xs font-medium capitalize mb-2 ${config.rss_article_detail_mode === depth ? "text-blue-300" : "text-slate-400"}`}>{depth}</span>
                            <input
                                type="number"
                                min={1} max={50}
                                value={config.rss_article_limits[depth]}
                                onChange={(e) => setConfig((c) => ({
                                    ...c,
                                    rss_article_limits: { ...c.rss_article_limits, [depth]: normalizeArticleLimit(e.target.value, c.rss_article_limits[depth]) },
                                }))}
                                className="w-full rounded-lg border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm text-white outline-none focus:border-blue-400"
                            />
                            <span className="block mt-1.5 text-[10px] text-slate-600">per feed, 1–50</span>
                        </label>
                    ))}
                </div>
            </div>

            <div className="space-y-3">
                <p className="text-xs text-slate-400">Default RSS feeds</p>
                <div className="space-y-2">
                    {config.default_rss_feeds.map((feed) => (
                        <label key={feed.url} className="flex items-center justify-between gap-4 rounded-xl border border-slate-800 bg-slate-950/60 px-4 py-3 text-sm">
                            <div>
                                <p className="text-slate-100">{feed.label}</p>
                                <p className="text-xs text-slate-500 break-all">{feed.url}</p>
                            </div>
                            <input
                                type="checkbox"
                                checked={enabledFeeds.has(feed.url)}
                                onChange={() => toggleFeed(feed.url)}
                            />
                        </label>
                    ))}
                </div>
            </div>

            <div className="space-y-3">
                <p className="text-xs text-slate-400">Custom RSS feeds</p>
                <div className="space-y-3">
                    {customFeedSlots.map(({ url, label }, index) => (
                        <div key={`custom-feed-${index}`} className="rounded-xl border border-slate-800 bg-slate-950/40 p-4 space-y-3">
                            <div className="grid grid-cols-1 md:grid-cols-[220px_1fr] gap-3">
                                <label className="block">
                                    <span className="text-xs text-slate-400">Feed name</span>
                                    <input
                                        type="text"
                                        value={label}
                                        onChange={(e) => updateCustomFeedLabel(url, e.target.value)}
                                        placeholder={`Custom feed ${index + 1}`}
                                        disabled={!url}
                                        className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm disabled:opacity-50"
                                    />
                                </label>
                                <label className="block">
                                    <span className="text-xs text-slate-400">Feed URL</span>
                                    <input
                                        type="url"
                                        value={url}
                                        onChange={(e) => updateCustomFeed(index, e.target.value)}
                                        placeholder={`https://example.com/feed-${index + 1}.xml`}
                                        className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm"
                                    />
                                </label>
                            </div>
                            <label className="flex items-center gap-2 text-sm text-slate-300">
                                <input
                                    type="checkbox"
                                    checked={!!url && enabledFeeds.has(url)}
                                    disabled={!url}
                                    onChange={() => toggleCustomFeedTracked(url)}
                                />
                                Include in analysis
                            </label>
                        </div>
                    ))}
                </div>
                <p className="text-xs text-slate-500">
                    Add up to {config.max_custom_rss_feeds} extra feeds for targeted sources like tech news, and give them friendly names that appear across the app.
                </p>
            </div>

            {/* Enhanced News Source Management */}
            <div className="pt-6 border-t border-slate-800">
                <NewsSourceManager />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <label className="block">
                    <span className="text-xs text-slate-400">Overall post cap</span>
                    <input
                        type="number" min={1} max={200}
                        value={config.max_posts}
                        onChange={(e) => setConfig((current) => ({ ...current, max_posts: Number(e.target.value) || 50 }))}
                        className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2"
                    />
                    <p className="mt-2 text-xs text-slate-500">
                        Final cap after feed collection. Higher RSS depth can exceed this when needed.
                    </p>
                </label>
                <label className="block">
                    <span className="text-xs text-slate-400">Lookback days</span>
                    <input
                        type="number" min={7} max={30}
                        value={config.lookback_days}
                        onChange={(e) => setConfig((current) => ({ ...current, lookback_days: Number(e.target.value) || 14 }))}
                        className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2"
                    />
                </label>
            </div>
        </section>
    );
}