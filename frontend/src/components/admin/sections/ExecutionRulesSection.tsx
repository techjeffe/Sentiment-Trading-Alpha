"use client";

/**
 * Execution Rules — the 4-rule refinement set (regime throttle, overnight 3x
 * de-risk, counter-trend cooldown, run-length protection) plus IC-based sizing.
 *
 * Storage: a single `execution_rules_json` blob of PARTIAL per-section
 * overrides ("regime_filter": {...}). A section with no entry uses its
 * logic_config.json default. This UI shows one "Override defaults" toggle per
 * section: ON seeds the section from the current JSON (or defaults) and makes
 * the fields editable; OFF removes the section from the blob entirely.
 * Blank a field to drop that key and fall back to its default.
 */

import { useState } from "react";
import { AppConfig } from "@/lib/utils/config-normalizer";

type ExecutionRulesSectionProps = {
    config: AppConfig;
    setConfig: React.Dispatch<React.SetStateAction<AppConfig>>;
};

type FieldDef = {
    key: string;
    label: string;
    kind: "number" | "text" | "list" | "toggle";
    min?: number;
    max?: number;
    step?: number;
    placeholder?: string;
    tooltip: string;
};

type SectionDef = {
    id: keyof typeof DEFAULT_RULES;
    title: string;
    blurb: string;
    tooltip: string;
    fields: FieldDef[];
};

const DEFAULT_RULES = {
    regime_filter: { enabled: true, chop_ma_spread_pct: 1.0, chop_atr_pct: 2.5, choppy_leverage_cap: 2 },
    overnight_derisk: { enabled: true, start_et: "15:00", require_ic_strong: true, exempt_convictions: ["HIGH"] },
    counter_trend_cooldown: { enabled: true, stop_out_window_days: 5, required_consecutive_stopouts: 2, cooldown_hours: 72 },
    run_length_protection: { enabled: true, convictions: ["HIGH"], trail_buffer_atr_mult: 1.5, min_trail_pct: 3.0, max_trail_pct: 8.0 },
    ic_scaling: { enabled: true, sensitivity: 0.5, min_multiplier: 0.85, max_multiplier: 1.2, strong_pct: 90.0 },
} as const;

type RulesShape = {
    regime_filter?: Record<string, unknown>;
    overnight_derisk?: Record<string, unknown>;
    counter_trend_cooldown?: Record<string, unknown>;
    run_length_protection?: Record<string, unknown>;
    ic_scaling?: Record<string, unknown>;
};

const SECTIONS: SectionDef[] = [
    {
        id: "regime_filter",
        title: "Regime Filter (3x Leverage Throttle)",
        blurb: "In choppy markets, downshift 3x ETFs (TQQQ/SPXL/SQQQ/SPXS) to 2x (QLD/SSO/QID/SDS).",
        tooltip: "Classifies the market from QQQ/SPY indicators: choppy when the 50/200 MA spread is tight or ATR is high with no trend. Choppy regime caps leverage at choppy_leverage_cap. Regime 'unknown' (missing data) fails open — no throttling.",
        fields: [
            { key: "chop_ma_spread_pct", label: "Chop MA Spread (%)", kind: "number", min: 0, max: 10, step: 0.1, tooltip: "50/200-day MA gap below this % = trend undefined = whipsaw zone. Lower = stricter (only very tight clusters count as chop)." },
            { key: "chop_atr_pct", label: "Chop ATR (%)", kind: "number", min: 0.5, max: 10, step: 0.1, tooltip: "14-day ATR % at/above this, with no clear trend, also counts as choppy (volatile churn)." },
            { key: "choppy_leverage_cap", label: "Choppy Leverage Cap (x)", kind: "number", min: 1, max: 3, step: 1, tooltip: "Max leverage allowed while regime = choppy. 2 = QLD/SSO instead of TQQQ/SPXL. 3 = disable the throttle." },
        ],
    },
    {
        id: "overnight_derisk",
        title: "Overnight 3x De-Risk",
        blurb: "Force-liquidate 3x positions before the close unless conviction + rolling IC are exceptional.",
        tooltip: "3x leveraged ETFs decay when held overnight. After start_et, any open 3x position is force-closed (and no new 3x entry is allowed) unless conviction is exempt AND the symbol's trailing IC clears its own 90th-percentile bar. Missing IC evidence never exempts — fail closed.",
        fields: [
            { key: "start_et", label: "De-Risk Start (ET)", kind: "text", placeholder: "15:00", tooltip: "Clock time (America/New_York) after which 3x positions are liquidated and new 3x entries blocked. Earlier = more conservative (less overnight risk, fewer scalps)." },
            { key: "require_ic_strong", label: "Require IC ≥ 90th pct for exemption", kind: "toggle", tooltip: "When ON, a 3x hold survives the time-stop only if the symbol's trailing IC clears its own 90th-percentile bar. OFF = conviction alone is enough to hold." },
            { key: "exempt_convictions", label: "Exempt Convictions (comma list)", kind: "list", placeholder: "HIGH", tooltip: "Conviction levels that may hold a 3x position past the de-risk window (still subject to require_ic_strong). Empty/blank = every 3x position is liquidated." },
        ],
    },
    {
        id: "counter_trend_cooldown",
        title: "Counter-Trend Cooldown",
        blurb: "Two stop-outs in one direction within 5 days → 72h cool-off for that symbol+direction.",
        tooltip: "Persistent counter-trend whipsaws (e.g. USO/BITU in macro downtrends) bleed via repeated same-direction re-entries. After N consecutive stopped-out trades in the same direction inside the window, new signals for that symbol+direction are blocked for cooldown_hours. A non-stop-out close (e.g. take-profit) resets the chain.",
        fields: [
            { key: "required_consecutive_stopouts", label: "Stop-outs before cooldown", kind: "number", min: 1, max: 10, step: 1, tooltip: "Consecutive stopped-out closes (stop_loss_hit / trailing_stop_hit) in the same direction that trigger the cool-off." },
            { key: "stop_out_window_days", label: "Lookback Window (days)", kind: "number", min: 1, max: 30, step: 1, tooltip: "How far back the consecutive stop-outs are counted. Stop-outs older than this don't count." },
            { key: "cooldown_hours", label: "Cooldown (hours)", kind: "number", min: 1, max: 720, step: 1, tooltip: "Mandatory cool-off after the trigger, measured from the most recent stop-out. 72 = 3 days." },
        ],
    },
    {
        id: "run_length_protection",
        title: "Run-Length Protection",
        blurb: "High-conviction single-stock winners trail on take-profit instead of being closed.",
        tooltip: "NOW/NET/NVDA-style multi-week winners get cut at the fixed take_profit_pct. This converts the take-profit breach into a widened ATR trailing stop so momentum compounds. Only applies to the listed convictions on underlyings OUTSIDE the leveraged ETF families (QQQ/SPY/USO/IBIT/BITO).",
        fields: [
            { key: "convictions", label: "Convictions (comma list)", kind: "list", placeholder: "HIGH", tooltip: "Conviction levels eligible for run-length protection. HIGH-only by default so medium/lower convictions keep fixed take-profit." },
            { key: "trail_buffer_atr_mult", label: "Trail Buffer (× ATR %)", kind: "number", min: 0.5, max: 5, step: 0.1, tooltip: "Trailing buffer = 14-day ATR % × this multiplier. Higher = wider stop, gives a volatile winner more room." },
            { key: "min_trail_pct", label: "Min Trail Buffer (%)", kind: "number", min: 1, max: 15, step: 0.5, tooltip: "Floor on the trailing buffer: a low-ATR name can't get a sub-1-2% stop that fires on noise." },
            { key: "max_trail_pct", label: "Max Trail Buffer (%)", kind: "number", min: 2, max: 25, step: 0.5, tooltip: "Ceiling on the trailing buffer so a spiky name can't carry an oversized loss window." },
        ],
    },
    {
        id: "ic_scaling",
        title: "IC-Based Position Sizing",
        blurb: "Scale size by the symbol's trailing Information Coefficient (confidence → realized return).",
        tooltip: "Rolling Spearman IC between signal confidence and realized 1d return. Positive IC = the edge is real → larger allocation; negative IC shrinks size. Multiplier = clamp(1 + IC × sensitivity, min_multiplier, max_multiplier) applied after the conviction scalar. Also: strong_pct is the bar the overnight de-risk exemption checks.",
        fields: [
            { key: "sensitivity", label: "IC Sensitivity", kind: "number", min: 0, max: 3, step: 0.1, tooltip: "How strongly IC moves size. 0.5 → a +0.3 IC adds ~15% to the position. 0 = disable the sizing effect entirely." },
            { key: "min_multiplier", label: "Min Size Multiplier", kind: "number", min: 0.5, max: 1.5, step: 0.01, tooltip: "Floor for the IC multiplier. 0.85 = worst-case negative IC still keeps 85% of the conviction-scaled size." },
            { key: "max_multiplier", label: "Max Size Multiplier", kind: "number", min: 1, max: 2, step: 0.01, tooltip: "Ceiling for the IC multiplier. 1.2 = a strong positive IC can add up to 20% on top of the conviction scalar." },
            { key: "strong_pct", label: "Strong-IC Percentile (%)", kind: "number", min: 50, max: 99, step: 1, tooltip: "Percentile bar for 'IC strong'. The overnight de-risk exemption only applies when the current IC ≥ this percentile of the symbol's own recent IC distribution. 90 = top decile." },
        ],
    },
];

function Tooltip({ text }: { text: string }) {
    return (
        <span
            className="group relative ml-1.5 inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-slate-600 text-[10px] font-bold text-slate-400 hover:border-slate-400 hover:text-slate-200"
            title={text}
        >
            ?
            <span className="pointer-events-none absolute left-1/2 bottom-full z-50 mb-1.5 hidden w-64 -translate-x-1/2 rounded-lg border border-slate-700 bg-slate-900 p-2.5 text-[11px] font-normal leading-snug text-slate-300 shadow-xl group-hover:block">
                {text}
            </span>
        </span>
    );
}

function readBlob(config: AppConfig): RulesShape {
    try {
        const parsed = JSON.parse(config.execution_rules_json || "{}");
        return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
        return {};
    }
}

function writeBlob(config: AppConfig, rules: RulesShape, setConfig: ExecutionRulesSectionProps["setConfig"]) {
    const clean: RulesShape = {};
    for (const key of Object.keys(DEFAULT_RULES) as Array<keyof typeof DEFAULT_RULES>) {
        const section = rules[key];
        if (section && typeof section === "object" && Object.keys(section).length > 0) {
            clean[key] = section;
        }
    }
    setConfig((c) => ({ ...c, execution_rules_json: Object.keys(clean).length ? JSON.stringify(clean) : null }));
}

export function ExecutionRulesSection({ config, setConfig }: ExecutionRulesSectionProps) {
    const [rules, setRules] = useState<RulesShape>(() => readBlob(config));
    const defaults = (config.logic_defaults as { execution_rules?: RulesShape })?.execution_rules ?? (DEFAULT_RULES as unknown as RulesShape);

    // Seed a section with the JSON-default values when the user flips its
    // override toggle ON — editing starts from a known baseline.
    const toggleSection = (id: keyof typeof DEFAULT_RULES, on: boolean) => {
        setRules((prev) => {
            const next = { ...prev };
            if (on) {
                const base = (defaults[id] ?? DEFAULT_RULES[id]) as Record<string, unknown>;
                next[id] = { ...base };
            } else {
                delete next[id];
            }
            return next;
        });
    };

    const setField = (id: keyof typeof DEFAULT_RULES, key: string, value: unknown, clear: boolean) => {
        setRules((prev) => {
            const next = { ...prev };
            const section = { ...(next[id] ?? {}) };
            if (clear) {
                delete section[key];
                // Removing the last key = no override for the section.
                if (Object.keys(section).length === 0) delete next[id];
                else next[id] = section;
            } else {
                section[key] = value;
                next[id] = section;
            }
            return next;
        });
    };

    const save = () => {
        writeBlob(config, rules, setConfig);
    };

    const dirty = JSON.stringify(readBlob(config)) !== JSON.stringify(rules);
    const overrideCount = Object.keys(readBlob(config)).length;

    return (
        <section id="execution-rules" className="scroll-mt-24 rounded-2xl border border-slate-800 bg-slate-900/60 p-5 space-y-5">
            <div className="flex items-start justify-between gap-3">
                <div>
                    <h2 className="text-sm font-semibold text-slate-200">Execution Rules</h2>
                    <p className="text-xs text-slate-500 mt-1">
                        Whipsaw filters, leverage de-risk, and position sizing — the four-rule refinement set.
                        Each block starts from its logic_config.json default; toggle <em>Override</em> to tune it from the UI.
                    </p>
                </div>
                {overrideCount > 0 && (
                    <span className="rounded-full border border-amber-700/60 bg-amber-950/30 px-2.5 py-1 text-[11px] text-amber-300">
                        {overrideCount} block{overrideCount > 1 ? "s" : ""} overridden
                    </span>
                )}
            </div>

            {SECTIONS.map((section) => {
                const id = section.id as keyof typeof DEFAULT_RULES;
                const overridden = Boolean(rules[id] && Object.keys(rules[id] ?? {}).length > 0);
                const base = (defaults[id] ?? DEFAULT_RULES[id]) as Record<string, unknown>;
                const value = (rules[id] ?? {}) as Record<string, unknown>;

                return (
                    <div key={id} className="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-3">
                        <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                    <p className="text-sm font-semibold text-slate-200">{section.title}</p>
                                    <Tooltip text={section.tooltip} />
                                </div>
                                <p className="text-xs text-slate-500 mt-0.5">{section.blurb}</p>
                            </div>
                            <label className="flex shrink-0 items-center gap-2 text-xs text-slate-400">
                                <input
                                    type="checkbox"
                                    checked={overridden}
                                    onChange={(e) => toggleSection(id, e.target.checked)}
                                    className="accent-sky-500"
                                />
                                Override defaults
                            </label>
                        </div>

                        {overridden && (
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
                                {section.fields.map((field) => {
                                    const current = value[field.key];
                                    const hasValue = current !== undefined && current !== null && current !== "";
                                    const placeholder = field.kind === "list"
                                        ? (Array.isArray(base[field.key]) ? (base[field.key] as string[]).join(", ") : String(base[field.key] ?? ""))
                                        : String(base[field.key] ?? field.placeholder ?? "");

                                    if (field.kind === "toggle") {
                                        return (
                                            <label key={field.key} className="flex items-center gap-2 text-sm text-slate-300">
                                                <input
                                                    type="checkbox"
                                                    checked={Boolean(current)}
                                                    onChange={(e) => setField(id, field.key, e.target.checked, false)}
                                                    className="accent-sky-500"
                                                />
                                                {field.label}
                                                <Tooltip text={field.tooltip} />
                                            </label>
                                        );
                                    }

                                    if (field.kind === "list") {
                                        return (
                                            <label key={field.key} className="block">
                                                <span className="flex items-center text-xs text-slate-400">
                                                    {field.label}
                                                    <Tooltip text={field.tooltip} />
                                                </span>
                                                <input
                                                    type="text"
                                                    value={hasValue ? (current as string[]).join(", ") : ""}
                                                    placeholder={placeholder}
                                                    onChange={(e) => {
                                                        const text = e.target.value.trim();
                                                        setField(
                                                            id,
                                                            field.key,
                                                            text ? text.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean) : null,
                                                            text === "",
                                                        );
                                                    }}
                                                    className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white placeholder:text-slate-600"
                                                />
                                            </label>
                                        );
                                    }

                                    return (
                                        <label key={field.key} className="block">
                                            <span className="flex items-center text-xs text-slate-400">
                                                {field.label}
                                                <Tooltip text={field.tooltip} />
                                            </span>
                                            <input
                                                type="number"
                                                min={field.min}
                                                max={field.max}
                                                step={field.step}
                                                value={hasValue ? (current as number) : ""}
                                                placeholder={placeholder}
                                                onChange={(e) => {
                                                    if (e.target.value === "") {
                                                        setField(id, field.key, null, true);
                                                    } else {
                                                        const num = Number(e.target.value);
                                                        if (!Number.isNaN(num)) setField(id, field.key, num, false);
                                                    }
                                                }}
                                                className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white placeholder:text-slate-600"
                                            />
                                        </label>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                );
            })}

            <div className="flex items-center justify-between gap-3 border-t border-slate-800 pt-4">
                <p className="text-xs text-slate-500">
                    Changes apply on the next analysis run (no restart needed). Blank a field to fall back to its default.
                </p>
                <div className="flex items-center gap-2">
                    {dirty && (
                        <button
                            type="button"
                            onClick={() => setRules(readBlob(config))}
                            className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
                        >
                            Discard
                        </button>
                    )}
                    <button
                        type="button"
                        onClick={save}
                        disabled={!dirty}
                        className="rounded-lg bg-sky-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                        Save Execution Rules
                    </button>
                </div>
            </div>
        </section>
    );
}