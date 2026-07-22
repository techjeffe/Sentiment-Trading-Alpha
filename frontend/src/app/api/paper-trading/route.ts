import { NextResponse } from "next/server";

import { getBackendApiUrl } from "@/lib/backend-api";

export async function GET() {
    try {
        const r = await fetch(`${getBackendApiUrl()}/api/v1/paper-trading/summary`, { cache: "no-store" });
        if (!r.ok) {
            return NextResponse.json({ error: "Backend API error" }, { status: r.status });
        }
        return NextResponse.json(await r.json());
    } catch {
        return NextResponse.json({ error: "Failed to load paper trading data" }, { status: 503 });
    }
}

function backendHeaders(init?: HeadersInit): Headers {
    const headers = new Headers(init);
    const ADMIN_API_TOKEN = process.env.ADMIN_API_TOKEN;
    if (ADMIN_API_TOKEN) {
        headers.set("X-Admin-Token", ADMIN_API_TOKEN);
    }
    return headers;
}

export async function DELETE() {
    try {
        const r = await fetch(`${getBackendApiUrl()}/api/v1/paper-trading/reset`, {
            method: "DELETE",
            headers: backendHeaders(),
            cache: "no-store",
        });
        if (!r.ok) {
            return NextResponse.json({ error: "Backend API error" }, { status: r.status });
        }
        return NextResponse.json(await r.json());
    } catch {
        return NextResponse.json({ error: "Failed to reset paper trading" }, { status: 503 });
    }
}
