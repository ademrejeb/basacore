import { BasacoreApiError, BasacoreConfigurationError } from "./errors.js";
import type {
  AuditOutcome,
  AuditWriteResult,
  AuthorizationCheckResult,
  ResolveTenantParams,
  ServiceIdentity,
  TenantContext,
  TenantList,
  TenantSummary,
  TokenResponse,
  UsageReportResult,
  UsageSummary,
} from "./types.js";

export type FetchLike = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;

export interface BasacoreClientOptions {
  /** Base URL of the Basacore API, e.g. `https://api.example.com`. */
  baseUrl: string;
  /** API credential UUID issued from the Basacore dashboard. */
  clientId: string;
  /** API credential secret. Shown once when the credential is created. */
  clientSecret: string;
  /** Optional fetch implementation (useful for tests or custom runtimes). */
  fetch?: FetchLike;
  /** Per-request timeout in milliseconds. Defaults to 15 seconds. */
  timeoutMs?: number;
  /** Refresh the token this many milliseconds before it expires. Defaults to 30s. */
  tokenSkewMs?: number;
}

export interface ListTenantsParams {
  limit?: number;
  offset?: number;
  status?: string;
}

export interface AuthorizationCheckInput {
  userId: string;
  tenantId: string;
  permission: string;
}

export interface UsageReportInput {
  tenantId: string;
  metric: string;
  value: number;
  idempotencyKey?: string;
  metadata?: Record<string, unknown>;
}

export interface AuditWriteInput {
  action: string;
  resourceType: string;
  resourceId?: string;
  tenantId?: string;
  outcome?: AuditOutcome;
  metadata?: Record<string, unknown>;
}

interface CachedToken {
  value: string;
  expiresAt: number;
}

interface ApiErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
    request_id?: string;
  };
}

export class BasacoreClient {
  private readonly baseUrl: string;
  private readonly clientId: string;
  private readonly clientSecret: string;
  private readonly fetchImpl: FetchLike;
  private readonly timeoutMs: number;
  private readonly tokenSkewMs: number;

  private cachedToken: CachedToken | null = null;
  private pendingToken: Promise<string> | null = null;

  constructor(options: BasacoreClientOptions) {
    if (!options.baseUrl?.trim()) {
      throw new BasacoreConfigurationError("baseUrl is required.");
    }
    if (!options.clientId?.trim() || !options.clientSecret?.trim()) {
      throw new BasacoreConfigurationError("clientId and clientSecret are required.");
    }
    const fetchImpl = options.fetch ?? globalThis.fetch;
    if (!fetchImpl) {
      throw new BasacoreConfigurationError(
        "No fetch implementation is available. Pass options.fetch in this runtime.",
      );
    }

    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.clientId = options.clientId;
    this.clientSecret = options.clientSecret;
    this.fetchImpl = fetchImpl.bind(globalThis);
    this.timeoutMs = options.timeoutMs ?? 15_000;
    this.tokenSkewMs = options.tokenSkewMs ?? 30_000;
  }

  /** Return a valid service token, requesting one if necessary. */
  async getAccessToken(): Promise<string> {
    if (this.cachedToken && this.cachedToken.expiresAt > Date.now()) {
      return this.cachedToken.value;
    }
    if (!this.pendingToken) {
      this.pendingToken = this.fetchToken().finally(() => {
        this.pendingToken = null;
      });
    }
    return this.pendingToken;
  }

  /** Service application identity and the scopes granted to this credential. */
  getIdentity(): Promise<ServiceIdentity> {
    return this.request<ServiceIdentity>("/v1/app/me");
  }

  /** List tenants owned by the calling application. */
  listTenants(params: ListTenantsParams = {}): Promise<TenantList> {
    const query = new URLSearchParams();
    if (params.limit !== undefined) query.set("limit", String(params.limit));
    if (params.offset !== undefined) query.set("offset", String(params.offset));
    if (params.status) query.set("status", params.status);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return this.request<TenantList>(`/v1/app/tenants${suffix}`);
  }

  /** Resolve a tenant by its slug or by one of its verified domains. */
  async resolveTenant(params: ResolveTenantParams): Promise<TenantSummary> {
    const query = new URLSearchParams();
    if (params.slug) query.set("slug", params.slug);
    if (params.domain) query.set("domain", params.domain);
    if (!query.toString()) {
      throw new BasacoreConfigurationError("resolveTenant requires either slug or domain.");
    }
    return this.request<TenantSummary>(`/v1/app/tenants/resolve?${query.toString()}`);
  }

  /** Effective tenant configuration, feature flags, plan limits and usage. */
  getTenantConfig(tenantId: string, environmentId?: string): Promise<TenantContext> {
    const query = new URLSearchParams({ tenant_id: tenantId });
    if (environmentId) query.set("environment_id", environmentId);
    return this.request<TenantContext>(`/v1/app/config?${query.toString()}`);
  }

  /** Decide whether a user may perform an action in a tenant. */
  checkAuthorization(input: AuthorizationCheckInput): Promise<AuthorizationCheckResult> {
    return this.request<AuthorizationCheckResult>("/v1/app/authorization/check", {
      method: "POST",
      body: JSON.stringify({
        user_id: input.userId,
        tenant_id: input.tenantId,
        permission: input.permission,
      }),
    });
  }

  /** Report metered usage. Reusing an idempotency key returns the original record. */
  reportUsage(input: UsageReportInput): Promise<UsageReportResult> {
    return this.request<UsageReportResult>("/v1/app/usage", {
      method: "POST",
      body: JSON.stringify({
        tenant_id: input.tenantId,
        metric: input.metric,
        value: input.value,
        ...(input.idempotencyKey ? { idempotency_key: input.idempotencyKey } : {}),
        metadata: input.metadata ?? {},
      }),
    });
  }

  /** Current-period usage totals for one tenant. */
  getUsageSummary(tenantId: string): Promise<UsageSummary> {
    return this.request<UsageSummary>(
      `/v1/app/usage/summary?tenant_id=${encodeURIComponent(tenantId)}`,
    );
  }

  /** Append an audit event. Events are immutable once written. */
  writeAudit(input: AuditWriteInput): Promise<AuditWriteResult> {
    return this.request<AuditWriteResult>("/v1/app/audit", {
      method: "POST",
      body: JSON.stringify({
        action: input.action,
        resource_type: input.resourceType,
        ...(input.resourceId ? { resource_id: input.resourceId } : {}),
        ...(input.tenantId ? { tenant_id: input.tenantId } : {}),
        outcome: input.outcome ?? "success",
        metadata: input.metadata ?? {},
      }),
    });
  }

  private async fetchToken(): Promise<string> {
    const body = new URLSearchParams({
      grant_type: "client_credentials",
      client_id: this.clientId,
      client_secret: this.clientSecret,
    });
    let response: Response;
    try {
      response = await this.send("/v1/oauth/token", {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });
    } catch (error) {
      if (error instanceof BasacoreApiError) throw error;
      throw new BasacoreApiError(0, {
        code: "network_error",
        message: error instanceof Error ? error.message : "The token request failed.",
      });
    }

    if (!response.ok) {
      throw await this.toApiError(response);
    }
    const payload = (await response.json()) as TokenResponse;
    if (!payload.access_token || !payload.expires_in) {
      throw new BasacoreApiError(response.status, {
        code: "invalid_token_response",
        message: "The token endpoint returned an incomplete response.",
      });
    }
    this.cachedToken = {
      value: payload.access_token,
      expiresAt: Date.now() + Math.max(payload.expires_in * 1000 - this.tokenSkewMs, 1_000),
    };
    return this.cachedToken.value;
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    allowAuthRetry = true,
  ): Promise<T> {
    const token = await this.getAccessToken();
    const headers = new Headers(init.headers);
    headers.set("authorization", `Bearer ${token}`);
    headers.set("accept", "application/json");
    if (init.body && !headers.has("content-type")) {
      headers.set("content-type", "application/json");
    }

    const response = await this.send(path, { ...init, headers });

    if (response.status === 401 && allowAuthRetry) {
      this.cachedToken = null;
      return this.request<T>(path, init, false);
    }
    if (!response.ok) {
      throw await this.toApiError(response);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  private async send(path: string, init: RequestInit): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      return await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...init,
        signal: init.signal ?? controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  }

  private async toApiError(response: Response): Promise<BasacoreApiError> {
    let payload: ApiErrorEnvelope = {};
    const text = await response.text();
    if (text) {
      try {
        payload = JSON.parse(text) as ApiErrorEnvelope;
      } catch {
        return new BasacoreApiError(response.status, {
          code: "invalid_response",
          message: text.slice(0, 300),
          request_id: response.headers.get("x-request-id") ?? undefined,
        });
      }
    }
    return new BasacoreApiError(response.status, {
      code: payload.error?.code,
      message: payload.error?.message,
      details: payload.error?.details,
      request_id: payload.error?.request_id ?? response.headers.get("x-request-id") ?? undefined,
    });
  }
}
