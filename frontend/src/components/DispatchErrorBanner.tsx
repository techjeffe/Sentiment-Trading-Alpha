"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, X } from "lucide-react";

type DispatchError = {
    id: number;
    symbol: string;
    underlying?: string | null;
    error_type: string;
    error_message: string;
    trading_mode: string;
    created_at: string | null;
};

const POLL_INTERVAL_MS = 15000; // Poll every 15 seconds

export default function DispatchErrorBanner() {
    const [errors, setErrors] = useState<DispatchError[]>([]);
    const [isAcknowledging, setIsAcknowledging] = useState<number | null>(null);

    const fetchErrors = useCallback(async () => {
        try {
            const response = await fetch("/api/alpaca/orphans", { cache: "no-store" });
            if (!response.ok) return;
            const data = await response.json() as DispatchError[];
            setErrors(data);
        } catch {
            // best effort only
        }
    }, []);

    useEffect(() => {
        void fetchErrors();
        const intervalId = window.setInterval(() => {
            void fetchErrors();
        }, POLL_INTERVAL_MS);
        return () => window.clearInterval(intervalId);
    }, [fetchErrors]);

    const acknowledge = useCallback(async (errorId: number) => {
        setIsAcknowledging(errorId);
        try {
            const response = await fetch(`/api/alpaca/orphans/${errorId}/acknowledge`, {
                method: "POST",
            });
            if (response.ok) {
                setErrors(prev => prev.filter(e => e.id !== errorId));
            }
        } finally {
            setIsAcknowledging(null);
        }
    }, []);

    const dismissAll = useCallback(async () => {
        setIsAcknowledging(-1);
        try {
            await Promise.all(
                errors.map(e =>
                    fetch(`/api/alpaca/orphans/${e.id}/acknowledge`, { method: "POST" })
                )
            );
            setErrors([]);
        } finally {
            setIsAcknowledging(null);
        }
    }, [errors]);

    if (errors.length === 0) {
        return null;
    }

    return (
        <div className="sticky top-0 z-[100] border-b border-red-700/60 bg-red-950/20 backdrop-blur-sm">
            <div className="mx-auto flex max-w-7xl items-start justify-between gap-4 px-4 py-3 text-sm text-red-100">
                <div className="flex-1 space-y-1.5">
                    <div className="flex items-center gap-2">
                        <AlertTriangle size={16} className="text-red-400 shrink-0" />
                        <span className="font-semibold text-red-200">Live Dispatch Error{errors.length > 1 ? "s" : ""}</span>
                    </div>
                    {errors.map((err) => (
                        <div key={err.id} className="flex items-center gap-2 text-xs text-red-300">
                            <span className="font-mono font-semibold">{err.symbol}</span>
                            <span>{err.error_message}</span>
                            <button
                                type="button"
                                onClick={() => void acknowledge(err.id)}
                                disabled={isAcknowledging === err.id}
                                className="shrink-0 rounded border border-red-500/40 bg-red-500/10 px-2 py-0.5 text-[10px] font-semibold text-red-200 hover:bg-red-500/20 disabled:opacity-50"
                            >
                                {isAcknowledging === err.id ? "..." : "Dismiss"}
                            </button>
                        </div>
                    ))}
                </div>
                <button
                    type="button"
                    onClick={dismissAll}
                    disabled={isAcknowledging === -1}
                    className="shrink-0 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-200 hover:bg-red-500/20 disabled:opacity-50"
                >
                    {isAcknowledging === -1 ? "..." : "Dismiss All"}
                </button>
            </div>
        </div>
    );
}
