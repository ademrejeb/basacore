import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Bridges the hosted login back to the API authorization endpoint.
 *
 * The application's authorization request lives on the Basacore API origin, not
 * on the console. ``return_to`` is therefore only ever accepted as a relative
 * path to that endpoint: the origin is fixed by configuration, so a tampered
 * value can never turn this into an open redirect.
 */
const API_PUBLIC_BASE =
  process.env.PUBLIC_API_BASE_URL ?? process.env.API_BASE_URL ?? "http://localhost:8000";
// Behind Render's proxy the request origin can be the internal bind address
// (0.0.0.0:10000), so the public console origin comes from configuration.
const WEB_BASE = (process.env.WEB_BASE_URL ?? "").replace(/\/+$/, "");

function parseTarget(returnTo: string): URL | null {
  if (!returnTo.startsWith("/v1/oauth/authorize")) return null;
  if (returnTo.startsWith("//") || returnTo.includes("\\")) return null;
  if (/[\r\n\t]/.test(returnTo)) return null;

  let base: URL;
  try {
    base = new URL(API_PUBLIC_BASE);
  } catch {
    return null;
  }
  if (base.protocol !== "http:" && base.protocol !== "https:") return null;

  let target: URL;
  try {
    target = new URL(returnTo, base);
  } catch {
    return null;
  }
  if (target.origin !== base.origin) return null;
  if (!target.pathname.startsWith("/v1/oauth/authorize")) return null;
  return target;
}

export async function GET(request: NextRequest) {
  const returnTo = request.nextUrl.searchParams.get("return_to") ?? "";
  const target = parseTarget(returnTo);
  if (!target) {
    return NextResponse.json(
      { error: "invalid_return_to", message: "The authorization request could not be resolved." },
      { status: 400 },
    );
  }
  // Replay the authorization request on the console origin: it is rewritten to
  // the API proxy, which forwards the session cookies, so the API sees the
  // signed-in user instead of bouncing back to /login.
  return NextResponse.redirect(new URL(target.pathname + target.search, WEB_BASE), {
    status: 303,
  });
}
