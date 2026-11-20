import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_BASE =
  process.env.INTERNAL_API_BASE_URL ?? process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
const WEB_BASE = process.env.WEB_BASE_URL ?? "http://localhost:3002";

const STRIP_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "content-length",
  "accept-encoding",
  "transfer-encoding",
  // Client-controlled and security-relevant: the API uses this address for
  // audit records and rate-limit keys. Only an upstream proxy may set it, and
  // only when TRUST_FORWARDED_HEADERS opts in explicitly.
  "x-forwarded-for",
  "x-real-ip",
  "forwarded",
]);

const TRUST_FORWARDED_HEADERS = process.env.TRUST_FORWARDED_HEADERS === "1";
const STRIP_RESPONSE_HEADERS = new Set([
  "content-encoding",
  "content-length",
  "connection",
  "transfer-encoding",
  "set-cookie",
]);

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  const incoming = new URL(request.url);
  const target = `${API_BASE}/${path.join("/")}${incoming.search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    const name = key.toLowerCase();
    if (STRIP_REQUEST_HEADERS.has(name)) {
      if (TRUST_FORWARDED_HEADERS && (name === "x-forwarded-for" || name === "x-real-ip")) {
        headers.set(name, value);
      }
      return;
    }
    headers.set(key, value);
  });
  if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) {
    headers.set("origin", WEB_BASE);
  }

  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.arrayBuffer();

  let response: Response;
  try {
    response = await fetch(target, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      { error: { code: "api_unreachable", message: "The Basacore API is not reachable." } },
      { status: 503 },
    );
  }

  const outHeaders = new Headers();
  response.headers.forEach((value, key) => {
    if (!STRIP_RESPONSE_HEADERS.has(key.toLowerCase())) outHeaders.set(key, value);
  });
  const setCookies =
    typeof response.headers.getSetCookie === "function" ? response.headers.getSetCookie() : [];
  for (const cookie of setCookies) outHeaders.append("set-cookie", cookie);

  return new NextResponse(await response.arrayBuffer(), {
    status: response.status,
    headers: outHeaders,
  });
}

export {
  proxy as DELETE,
  proxy as GET,
  proxy as HEAD,
  proxy as OPTIONS,
  proxy as PATCH,
  proxy as POST,
  proxy as PUT,
};
