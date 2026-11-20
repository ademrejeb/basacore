import { cookies } from "next/headers";

import type { Session } from "./types";

const API_BASE =
  process.env.INTERNAL_API_BASE_URL ?? process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
const WEB_BASE = process.env.WEB_BASE_URL ?? "http://localhost:3002";

export interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
    request_id?: string;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly requestId: string | undefined;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error?.message ?? `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = body.error?.code ?? "request_failed";
    this.details = body.error?.details;
    this.requestId = body.error?.request_id;
  }
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/**
 * Server-side API call. Cookies are forwarded explicitly so the browser never
 * talks to the API origin directly, and unsafe requests carry a trusted Origin
 * so the API's CSRF check can pass.
 */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const cookieStore = await cookies();
  const headers = new Headers(init.headers);
  const cookieHeader = cookieStore.toString();
  if (cookieHeader) headers.set("cookie", cookieHeader);
  headers.set("accept", "application/json");
  if (!SAFE_METHODS.has((init.method ?? "GET").toUpperCase())) {
    headers.set("origin", WEB_BASE);
    if (init.body && !headers.has("content-type")) {
      headers.set("content-type", "application/json");
    }
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    throw new ApiError(503, {
      error: { code: "api_unreachable", message: "The Basacore API is not reachable." },
    });
  }

  if (!response.ok) {
    let body: ApiErrorBody = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // Non-JSON error bodies are surfaced with the status code only.
    }
    throw new ApiError(response.status, body);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function getSession(): Promise<Session | null> {
  try {
    return await apiFetch<Session>("/v1/auth/me");
  } catch (error) {
    if (error instanceof ApiError && [401, 403, 503].includes(error.status)) return null;
    throw error;
  }
}

export function can(session: Session, permission: string): boolean {
  return session.platform_permissions.includes(permission);
}

export function queryString(params: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const suffix = query.toString();
  return suffix ? `?${suffix}` : "";
}
