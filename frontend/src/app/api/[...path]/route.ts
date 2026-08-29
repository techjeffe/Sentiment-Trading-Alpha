import { NextRequest, NextResponse } from "next/server";

import { getBackendApiUrl } from "@/lib/backend-api";

// Catch-all proxy: forwards any /api/* request that has no dedicated route
// handler to the FastAPI backend. Replaces the old next.config.js rewrite,
// which shadowed dynamic route handlers ([id], [symbol], ...) in Next.js 16.
// Specific handlers (static or dynamic) always win over this catch-all.

const ADMIN_API_TOKEN = process.env.ADMIN_API_TOKEN;

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(req: NextRequest, ctx: RouteContext) {
    const { path } = await ctx.params;
    const url = `${getBackendApiUrl()}/api/${path.join("/")}${req.nextUrl.search}`;

    const headers = new Headers(req.headers);
    headers.delete("host");
    headers.delete("content-length");
    if (ADMIN_API_TOKEN) {
        headers.set("X-Admin-Token", ADMIN_API_TOKEN);
    }

    const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer();
    const resp = await fetch(url, { method: req.method, headers, body, cache: "no-store" });

    const respHeaders = new Headers(resp.headers);
    // Keep redirects on this origin so the admin token still applies and CORS is avoided.
    const location = respHeaders.get("location");
    if (location) {
        try {
            const loc = new URL(location, getBackendApiUrl());
            respHeaders.set("location", loc.pathname + loc.search);
        } catch {
            // leave as-is
        }
    }
    return new NextResponse(resp.body, { status: resp.status, headers: respHeaders });
}

export function GET(req: NextRequest, ctx: RouteContext) {
    return proxy(req, ctx);
}
export function POST(req: NextRequest, ctx: RouteContext) {
    return proxy(req, ctx);
}
export function PUT(req: NextRequest, ctx: RouteContext) {
    return proxy(req, ctx);
}
export function PATCH(req: NextRequest, ctx: RouteContext) {
    return proxy(req, ctx);
}
export function DELETE(req: NextRequest, ctx: RouteContext) {
    return proxy(req, ctx);
}