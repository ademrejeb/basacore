import { describe, expect, it, vi } from "vitest";

import { BasacoreApiError, BasacoreClient, BasacoreConfigurationError } from "../src/index.js";

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>;

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function tokenResponse(token = "token-1", expiresIn = 3600): Response {
  return jsonResponse(200, {
    access_token: token,
    token_type: "Bearer",
    expires_in: expiresIn,
    scope: "app:config.read",
  });
}

function client(handler: Handler) {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push({ url, init: init ?? {} });
    return handler(url, init ?? {});
  });
  return {
    calls,
    client: new BasacoreClient({
      baseUrl: "https://api.example.dev/",
      clientId: "credential-id",
      clientSecret: "credential-secret",
      fetch,
    }),
  };
}

describe("BasacoreClient", () => {
  it("validates required configuration", () => {
    expect(() => new BasacoreClient({ baseUrl: "", clientId: "a", clientSecret: "b" })).toThrow(
      BasacoreConfigurationError,
    );
    expect(
      () => new BasacoreClient({ baseUrl: "https://x.dev", clientId: "", clientSecret: "b" }),
    ).toThrow(BasacoreConfigurationError);
  });

  it("requests a token once and reuses it", async () => {
    const { client: api, calls } = client((url) => {
      if (url.endsWith("/v1/oauth/token")) return tokenResponse();
      return jsonResponse(200, { items: [], limit: 50, offset: 0 });
    });

    await api.listTenants();
    await api.listTenants({ limit: 10, status: "active" });

    expect(calls.filter((call) => call.url.includes("/v1/oauth/token"))).toHaveLength(1);
    const listCall = calls.at(-1)!;
    expect(listCall.url).toBe("https://api.example.dev/v1/app/tenants?limit=10&status=active");
    expect(new Headers(listCall.init.headers).get("authorization")).toBe("Bearer token-1");
  });

  it("sends the documented payloads", async () => {
    const { client: api, calls } = client((url) => {
      if (url.endsWith("/v1/oauth/token")) return tokenResponse();
      if (url.endsWith("/v1/app/usage")) {
        return jsonResponse(201, {
          id: "usage-1",
          metric: "api_calls",
          quantity: 5,
          period_start: "2026-11-01",
          idempotent: false,
        });
      }
      return jsonResponse(200, {
        authorized: true,
        user_id: "user-1",
        tenant_id: "tenant-1",
        permission: "projects.read",
        effective_permissions: ["projects.read"],
      });
    });

    await api.reportUsage({
      tenantId: "tenant-1",
      metric: "api_calls",
      value: 5,
      idempotencyKey: "idem-1",
    });
    await api.checkAuthorization({
      userId: "user-1",
      tenantId: "tenant-1",
      permission: "projects.read",
    });

    const usage = calls.find((call) => call.url.endsWith("/v1/app/usage"))!;
    expect(JSON.parse(String(usage.init.body))).toEqual({
      tenant_id: "tenant-1",
      metric: "api_calls",
      value: 5,
      idempotency_key: "idem-1",
      metadata: {},
    });

    const authorization = calls.find((call) => call.url.includes("/v1/app/authorization/check"))!;
    expect(JSON.parse(String(authorization.init.body))).toEqual({
      user_id: "user-1",
      tenant_id: "tenant-1",
      permission: "projects.read",
    });
  });

  it("refreshes an expired token once on 401", async () => {
    let tokenNumber = 0;
    const { client: api, calls } = client((url, init) => {
      if (url.endsWith("/v1/oauth/token")) {
        tokenNumber += 1;
        return tokenResponse(`token-${tokenNumber}`);
      }
      const authorization = new Headers(init.headers).get("authorization");
      if (authorization === "Bearer token-1") {
        return jsonResponse(401, { error: { code: "unauthorized", message: "Expired" } });
      }
      return jsonResponse(200, {
        application: null,
        credential_id: "credential-id",
        scopes: [],
      });
    });

    const identity = await api.getIdentity();
    expect(identity.credential_id).toBe("credential-id");
    expect(calls.filter((call) => call.url.includes("/v1/oauth/token"))).toHaveLength(2);
  });

  it("surfaces structured API errors", async () => {
    const { client: api } = client((url) => {
      if (url.endsWith("/v1/oauth/token")) return tokenResponse();
      return jsonResponse(
        402,
        {
          error: {
            code: "quota_exceeded",
            message: "Quota exceeded.",
            details: { limit: 10 },
          },
        },
        { "x-request-id": "req_123" },
      );
    });

    await expect(api.reportUsage({ tenantId: "t", metric: "api_calls", value: 1 })).rejects.toMatchObject({
      name: "BasacoreApiError",
      status: 402,
      code: "quota_exceeded",
      requestId: "req_123",
    });
    await expect(
      api.reportUsage({ tenantId: "t", metric: "api_calls", value: 1 }),
    ).rejects.toBeInstanceOf(BasacoreApiError);
  });

  it("rejects resolveTenant without a lookup key", async () => {
    const { client: api } = client(() => tokenResponse());
    await expect(api.resolveTenant({})).rejects.toBeInstanceOf(BasacoreConfigurationError);
  });
});
