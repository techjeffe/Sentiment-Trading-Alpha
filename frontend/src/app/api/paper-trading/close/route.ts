import { NextRequest, NextResponse } from "next/server";
import { getBackendApiUrl } from "@/lib/backend-api";

const ADMIN_API_TOKEN = process.env.ADMIN_API_TOKEN;

function backendHeaders(init?: HeadersInit): Headers {
    const headers = new Headers(init);
    if (ADMIN_API_TOKEN) {
        headers.set("X-Admin-Token", ADMIN_API_TOKEN);
    }
    return headers;
}

export async function POST(req: NextRequest) {
    try {
        const body = await req.json();
        if (!body.trade_id) {
            return NextResponse.json({ error: "Missing trade_id" }, { status: 400 });
        }
        const { trade_id } = body;
        
        // Validate trade_id to prevent SSRF/path traversal
        if (!Number.isInteger(Number(trade_id)) || Number(trade_id) <= 0) {
            return NextResponse.json({ error: "Invalid trade_id" }, { status: 400 });
        }
        
        const validatedTradeId = Number(trade_id);
        const r = await fetch(`${getBackendApiUrl()}/api/v1/paper-trading/${validatedTradeId}/close`, {
            method: "POST",
            cache: "no-store",
            headers: backendHeaders({ "Content-Type": "application/json" })
        });
        if (!r.ok) {
            return NextResponse.json({ error: "Backend API error" }, { status: r.status });
        }
        return NextResponse.json(await r.json());
    } catch {
        return NextResponse.json({ error: "Failed to close paper trade" }, { status: 503 });
    }
}