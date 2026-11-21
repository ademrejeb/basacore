import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * OIDC discovery for the deployed suite.
 *
 * The API and the console live on different Render subdomains. The API session
 * cookie is set on the console host (the login goes through the console proxy),
 * so an authorization request sent to the API origin can never see it and
 * redirects back to /login forever.
 *
 * The console therefore advertises itself as the issuer and fronts the
 * browser-facing endpoints with its own proxy (/v1/* -> API). Token, userinfo
 * and JWKS endpoints stay on the API origin: those are server-to-server calls
 * that need no cookies.
 */
const API_BASE =
  process.env.INTERNAL_API_BASE_URL ?? process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
const WEB_BASE = (process.env.WEB_BASE_URL ?? "http://localhost:3002").replace(/\/+$/, "");

export async function GET() {
  let metadata: Record<string, unknown>;
  try {
    const response = await fetch(`${API_BASE}/.well-known/openid-configuration`, {
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) throw new Error(`discovery ${response.status}`);
    metadata = (await response.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json(
      { error: "issuer_unreachable", message: "The Basacore API is not reachable." },
      { status: 503 },
    );
  }

  return NextResponse.json(
    {
      ...metadata,
      issuer: WEB_BASE,
      authorization_endpoint: `${WEB_BASE}/v1/oauth/authorize`,
      end_session_endpoint: `${WEB_BASE}/v1/oauth/logout`,
    },
    { headers: { "cache-control": "no-store" } },
  );
}
