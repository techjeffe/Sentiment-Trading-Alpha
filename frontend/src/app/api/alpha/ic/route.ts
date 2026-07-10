import { NextRequest, NextResponse } from "next/server";

import { getBackendApiUrl } from "@/lib/backend-api";

export async function GET(req: NextRequest) {
    try {
        const qs = req.nextUrl.search;
        const r = await fetch(`${getBackendApiUrl()}/api/v1/alpha/ic${qs}`, { cache: "no-store" });
        if (!r.ok) {
            return NextResponse.json({ error: "Backend API error" }, { status: r.status });
        }
        return NextResponse.json(await r.json());
    } catch {
        return NextResponse.json({ error: "Failed to load IC data" }, { status: 503 });
    }
}
