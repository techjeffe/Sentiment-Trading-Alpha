import { NextRequest, NextResponse } from "next/server";

import { getBackendApiUrl } from "@/lib/backend-api";

export async function POST(req: NextRequest) {
    try {
        const body = await req.text();
        const r = await fetch(`${getBackendApiUrl()}/api/v1/alpha/perturbation`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
            cache: "no-store",
        });
        if (!r.ok) {
            return NextResponse.json({ error: "Backend API error" }, { status: r.status });
        }
        return NextResponse.json(await r.json());
    } catch {
        return NextResponse.json({ error: "Failed to run perturbation test" }, { status: 503 });
    }
}
