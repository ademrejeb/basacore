"use server";

import { revalidatePath } from "next/cache";

import { apiFetch, ApiError } from "@/lib/api";

export interface ActionResult {
  ok: boolean;
  message?: string;
  data?: Record<string, string>;
}

const OK: ActionResult = { ok: true };

function failure(error: unknown): ActionResult {
  if (error instanceof ApiError) {
    return { ok: false, message: error.message };
  }
  return {
    ok: false,
    message: "The request could not be completed. Please try again.",
  };
}

function text(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

function optional(form: FormData, key: string): string | undefined {
  const value = text(form, key);
  return value ? value : undefined;
}

function number(form: FormData, key: string): number | undefined {
  const value = optional(form, key);
  if (value === undefined) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function boolean(form: FormData, key: string): boolean {
  return form.get(key) === "on" || form.get(key) === "true";
}

function jsonObject(form: FormData, key: string, label: string): Record<string, unknown> | string {
  const value = text(form, key);
  if (!value) return {};
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      return `${label} must be a JSON object.`;
    }
    return parsed as Record<string, unknown>;
  } catch {
    return `${label} is not valid JSON.`;
  }
}

async function mutate(
  path: string,
  init: RequestInit,
  revalidate: string[],
  successMessage?: string,
  extract?: (payload: Record<string, unknown>) => Record<string, string> | undefined,
): Promise<ActionResult> {
  try {
    const payload = await apiFetch<Record<string, unknown>>(path, init);
    for (const target of revalidate) revalidatePath(target);
    return {
      ok: true,
      message: successMessage,
      data: extract ? extract(payload) : undefined,
    };
  } catch (error) {
    return failure(error);
  }
}

// ---------------------------------------------------------------- organizations

export async function createOrganizationAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const body: Record<string, unknown> = { name: text(form, "name") };
  for (const key of ["slug", "description", "billing_email"]) {
    const value = optional(form, key);
    if (value) body[key] = value;
  }
  return mutate("/v1/organizations", { method: "POST", body: JSON.stringify(body) }, [
    "/organizations",
    "/dashboard",
  ]);
}

export async function updateOrganizationAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const body: Record<string, unknown> = {};
  for (const key of ["name", "description", "billing_email", "status"]) {
    const value = optional(form, key);
    if (value !== undefined) body[key] = value;
  }
  return mutate(`/v1/organizations/${id}`, { method: "PATCH", body: JSON.stringify(body) }, [
    "/organizations",
    `/organizations/${id}`,
  ]);
}

// ----------------------------------------------------------------- applications

export async function createApplicationAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const authConfig: Record<string, unknown> = {};
  const mfa = optional(form, "mfa");
  if (mfa) authConfig.mfa = mfa;
  const sessionTtl = number(form, "session_ttl_minutes");
  if (sessionTtl !== undefined) authConfig.session_ttl_minutes = Math.trunc(sessionTtl);
  const body: Record<string, unknown> = {
    organization_id: text(form, "organization_id"),
    name: text(form, "name"),
    auth_config: authConfig,
  };
  for (const key of ["slug", "description", "owner_id", "base_url"]) {
    const value = optional(form, key);
    if (value) body[key] = value;
  }
  return mutate("/v1/applications", { method: "POST", body: JSON.stringify(body) }, [
    "/applications",
    "/dashboard",
  ]);
}

export async function updateApplicationAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const body: Record<string, unknown> = {};
  for (const key of ["name", "description", "owner_id", "base_url"]) {
    const value = optional(form, key);
    if (value !== undefined) body[key] = value;
  }
  return mutate(`/v1/applications/${id}`, { method: "PATCH", body: JSON.stringify(body) }, [
    "/applications",
    `/applications/${id}`,
    `/applications/${id}/integration`,
    `/applications/${id}/clients`,
  ]);
}

export async function setApplicationStatusAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const body = {
    status: text(form, "status"),
    reason: optional(form, "reason"),
  };
  return mutate(`/v1/applications/${id}/status`, { method: "POST", body: JSON.stringify(body) }, [
    "/applications",
    `/applications/${id}`,
    `/applications/${id}/integration`,
    `/applications/${id}/clients`,
  ]);
}

export async function createEnvironmentAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const body: Record<string, unknown> = {
    name: text(form, "name"),
    slug: text(form, "slug"),
    kind: text(form, "kind") || "custom",
    is_default: false,
  };
  for (const key of ["base_url", "api_base_url"]) {
    const value = optional(form, key);
    if (value) body[key] = value;
  }
  return mutate(
    `/v1/applications/${id}/environments`,
    { method: "POST", body: JSON.stringify(body) },
    [`/applications/${id}`, `/applications/${id}/integration`, `/applications/${id}/clients`],
  );
}

export async function createCredentialAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const scopes = form
    .getAll("scopes")
    .map((value) => String(value))
    .filter(Boolean);
  const body: Record<string, unknown> = {
    name: text(form, "name"),
    scopes,
  };
  const expires = number(form, "expires_in_days");
  if (expires) body.expires_in_days = expires;
  return mutate(
    `/v1/applications/${id}/credentials`,
    { method: "POST", body: JSON.stringify(body) },
    [`/applications/${id}`, `/applications/${id}/integration`, `/applications/${id}/clients`],
    "Credential created. Copy the secret now — it will not be shown again.",
    (payload) => ({
      client_id: String(payload.id ?? ""),
      client_secret: String(payload.client_secret ?? ""),
      scopes: scopes.join(" "),
    }),
  );
}

export async function revokeCredentialAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const credentialId = text(form, "credential_id");
  return mutate(
    `/v1/applications/${id}/credentials/${credentialId}`,
    { method: "DELETE" },
    [`/applications/${id}`, `/applications/${id}/integration`, `/applications/${id}/clients`],
  );
}

const CLIENT_SCOPES = ["openid", "profile", "email", "offline_access"];

function uriList(form: FormData, key: string): string[] {
  return text(form, key)
    .split(/[\n,]+/)
    .map((value) => value.trim())
    .filter(Boolean);
}

function clientScopes(form: FormData): string[] {
  return form
    .getAll("scopes")
    .map((value) => String(value))
    .filter((value) => CLIENT_SCOPES.includes(value));
}

export async function createApplicationClientAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const body: Record<string, unknown> = {
    name: text(form, "name"),
    client_type: text(form, "client_type") || "confidential",
    redirect_uris: uriList(form, "redirect_uris"),
    post_logout_redirect_uris: uriList(form, "post_logout_redirect_uris"),
    allowed_origins: uriList(form, "allowed_origins"),
    scopes: clientScopes(form),
    pkce_required: boolean(form, "pkce_required"),
  };
  return mutate(
    `/v1/applications/${id}/clients`,
    { method: "POST", body: JSON.stringify(body) },
    [`/applications/${id}`, `/applications/${id}/integration`, `/applications/${id}/clients`],
    "Login client created. Copy the secret now — it will not be shown again.",
    (payload) => {
      const data: Record<string, string> = {
        client_id: String(payload.client_id ?? ""),
      };
      if (payload.client_secret) data.client_secret = String(payload.client_secret);
      return data;
    },
  );
}

export async function updateApplicationClientAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const clientId = text(form, "client_id");
  const body: Record<string, unknown> = {
    name: text(form, "name"),
    redirect_uris: uriList(form, "redirect_uris"),
    post_logout_redirect_uris: uriList(form, "post_logout_redirect_uris"),
    allowed_origins: uriList(form, "allowed_origins"),
    scopes: clientScopes(form),
    pkce_required: boolean(form, "pkce_required"),
    status: text(form, "status") || "active",
  };
  return mutate(
    `/v1/applications/${id}/clients/${clientId}`,
    { method: "PATCH", body: JSON.stringify(body) },
    [`/applications/${id}`, `/applications/${id}/integration`, `/applications/${id}/clients`],
    "Login client updated. Disabling it revokes every outstanding session.",
  );
}

export async function rotateApplicationClientSecretAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const clientId = text(form, "client_id");
  return mutate(
    `/v1/applications/${id}/clients/${clientId}/rotate-secret`,
    { method: "POST" },
    [`/applications/${id}`, `/applications/${id}/integration`, `/applications/${id}/clients`],
    "Secret rotated. Copy it now — the previous secret stops working immediately.",
    (payload) => ({
      client_id: String(payload.client_id ?? ""),
      client_secret: String(payload.client_secret ?? ""),
    }),
  );
}

export async function disableApplicationClientAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const clientId = text(form, "client_id");
  return mutate(
    `/v1/applications/${id}/clients/${clientId}`,
    { method: "DELETE" },
    [`/applications/${id}`, `/applications/${id}/integration`, `/applications/${id}/clients`],
  );
}

export async function publishApplicationConfigAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const config = jsonObject(form, "config", "Configuration");
  if (typeof config === "string") return { ok: false, message: config };
  const body: Record<string, unknown> = { config };
  const environment = optional(form, "environment_id");
  if (environment) body.environment_id = environment;
  const note = optional(form, "note");
  if (note) body.note = note;
  return mutate(
    `/v1/applications/${id}/config`,
    { method: "POST", body: JSON.stringify(body) },
    [`/applications/${id}`, `/applications/${id}/integration`, `/applications/${id}/clients`],
  );
}

export async function createFlagAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const body: Record<string, unknown> = {
    key: text(form, "key"),
    name: text(form, "name"),
    enabled: boolean(form, "enabled"),
    rollout_percentage: number(form, "rollout_percentage") ?? 100,
  };
  const description = optional(form, "description");
  if (description) body.description = description;
  return mutate(
    `/v1/applications/${id}/flags`,
    { method: "POST", body: JSON.stringify(body) },
    [`/applications/${id}`, `/applications/${id}/integration`, `/applications/${id}/clients`],
  );
}

export async function updateFlagAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const flagId = text(form, "flag_id");
  const body: Record<string, unknown> = {
    enabled: text(form, "enabled") === "true",
    rollout_percentage: number(form, "rollout_percentage") ?? 0,
  };
  return mutate(
    `/v1/applications/${id}/flags/${flagId}`,
    { method: "PATCH", body: JSON.stringify(body) },
    [`/applications/${id}`, `/applications/${id}/integration`, `/applications/${id}/clients`],
  );
}

// ---------------------------------------------------------------------- tenants

export async function createTenantAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const quotas = jsonObject(form, "quotas", "Quotas");
  if (typeof quotas === "string") return { ok: false, message: quotas };
  const configuration = jsonObject(form, "configuration", "Configuration");
  if (typeof configuration === "string") return { ok: false, message: configuration };

  const body: Record<string, unknown> = {
    application_id: text(form, "application_id"),
    organization_id: text(form, "organization_id"),
    name: text(form, "name"),
    billing_interval: text(form, "billing_interval") || "monthly",
    trial_days: number(form, "trial_days") ?? 0,
    quotas,
    configuration,
  };
  for (const key of ["slug", "owner_email", "plan_id", "custom_domain"]) {
    const value = optional(form, key);
    if (value) body[key] = value;
  }
  const applicationId = text(form, "application_id");
  const organizationId = text(form, "organization_id");
  const revalidate = ["/tenants", "/dashboard", "/organizations", "/applications"];
  if (applicationId) {
    revalidate.push(`/applications/${applicationId}`, `/applications/${applicationId}/clients`);
  }
  if (organizationId) revalidate.push(`/organizations/${organizationId}`);
  return mutate(
    "/v1/tenants",
    { method: "POST", body: JSON.stringify(body) },
    revalidate,
    undefined,
    (payload) => {
      const data: Record<string, string> = {
        invite_email_sent: String(payload.invite_email_sent ?? false),
      };
      if (payload.invite_token) data.invite_token = String(payload.invite_token);
      return data;
    },
  );
}

export async function updateTenantAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const body: Record<string, unknown> = {};
  const name = optional(form, "name");
  if (name) body.name = name;
  const quotas = text(form, "quotas");
  if (quotas) {
    const parsed = jsonObject(form, "quotas", "Quotas");
    if (typeof parsed === "string") return { ok: false, message: parsed };
    body.quotas = parsed;
  }
  return mutate(`/v1/tenants/${id}`, { method: "PATCH", body: JSON.stringify(body) }, [
    "/tenants",
    `/tenants/${id}`,
  ]);
}

export async function tenantLifecycleAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const intent = text(form, "intent");
  const reason = optional(form, "reason");
  const body: Record<string, unknown> = intent === "delete" ? { confirm_name: text(form, "confirm_name") } : {};
  if (reason) body.reason = reason;
  return mutate(`/v1/tenants/${id}/${intent}`, { method: "POST", body: JSON.stringify(body) }, [
    "/tenants",
    `/tenants/${id}`,
    "/dashboard",
  ]);
}

export async function publishTenantConfigAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const config = jsonObject(form, "config", "Configuration");
  if (typeof config === "string") return { ok: false, message: config };
  const body: Record<string, unknown> = { config };
  const note = optional(form, "note");
  if (note) body.note = note;
  return mutate(`/v1/tenants/${id}/config`, { method: "POST", body: JSON.stringify(body) }, [
    `/tenants/${id}`,
  ]);
}

export async function addTenantDomainAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const body = { domain: text(form, "domain"), is_primary: boolean(form, "is_primary") };
  return mutate(`/v1/tenants/${id}/domains`, { method: "POST", body: JSON.stringify(body) }, [
    `/tenants/${id}`,
  ]);
}

export async function verifyTenantDomainAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const domainId = text(form, "domain_id");
  return mutate(`/v1/tenants/${id}/domains/${domainId}/verify`, { method: "POST" }, [
    `/tenants/${id}`,
  ]);
}

export async function deleteTenantDomainAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const domainId = text(form, "domain_id");
  return mutate(`/v1/tenants/${id}/domains/${domainId}`, { method: "DELETE" }, [
    `/tenants/${id}`,
  ]);
}

export async function inviteTenantUserAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const body: Record<string, unknown> = {
    email: text(form, "email"),
    role_key: text(form, "role_key") || "tenant_member",
    send_email: boolean(form, "send_email"),
  };
  const fullName = optional(form, "full_name");
  if (fullName) body.full_name = fullName;
  return mutate(
    `/v1/tenants/${id}/users`,
    { method: "POST", body: JSON.stringify(body) },
    [`/tenants/${id}`],
    undefined,
    (payload) => {
      const data: Record<string, string> = {
        invite_email_sent: String(payload.invite_email_sent ?? false),
      };
      if (payload.invite_token) data.invite_token = String(payload.invite_token);
      return data;
    },
  );
}

export async function updateTenantUserAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const userId = text(form, "user_id");
  const body: Record<string, unknown> = {};
  const role = optional(form, "role_key");
  if (role) body.role_key = role;
  const status = optional(form, "status");
  if (status) body.status = status;
  return mutate(
    `/v1/tenants/${id}/users/${userId}`,
    { method: "PATCH", body: JSON.stringify(body) },
    [`/tenants/${id}`],
  );
}

export async function removeTenantUserAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const userId = text(form, "user_id");
  return mutate(`/v1/tenants/${id}/users/${userId}`, { method: "DELETE" }, [`/tenants/${id}`]);
}

// ---------------------------------------------------------------------- billing

export async function changeSubscriptionAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "id");
  const body: Record<string, unknown> = {};
  const plan = optional(form, "plan_id");
  if (plan) body.plan_id = plan;
  const interval = optional(form, "billing_interval");
  if (interval) body.billing_interval = interval;
  const status = optional(form, "status");
  if (status) body.status = status;
  const discount = number(form, "discount_percent");
  if (discount !== undefined) body.discount_percent = discount;
  body.cancel_at_period_end = boolean(form, "cancel_at_period_end");
  const reason = optional(form, "reason");
  if (reason) body.reason = reason;
  return mutate(
    `/v1/billing/subscriptions/${id}`,
    { method: "PATCH", body: JSON.stringify(body) },
    ["/billing"],
  );
}

export async function createPlanAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const limits: Record<string, number> = {};
  for (const [key, field] of [
    ["api_calls", "limit_api_calls"],
    ["seats", "limit_seats"],
    ["storage_gb", "limit_storage_gb"],
  ] as const) {
    const value = number(form, field);
    if (value !== undefined) limits[key] = Math.trunc(value);
  }
  const body: Record<string, unknown> = {
    key: text(form, "key"),
    name: text(form, "name"),
    price_monthly_cents: (number(form, "price_monthly") ?? 0) * 100,
    price_yearly_cents: (number(form, "price_yearly") ?? 0) * 100,
    limits,
  };
  const description = optional(form, "description");
  if (description) body.description = description;
  return mutate("/v1/billing/plans", { method: "POST", body: JSON.stringify(body) }, ["/billing"]);
}

// ---------------------------------------------------------------------- access

export async function createRoleAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const body: Record<string, unknown> = {
    key: text(form, "key"),
    name: text(form, "name"),
    permissions: form.getAll("permissions").map(String),
  };
  const scopeType = text(form, "scope_type") || "organization";
  body.scope_type = scopeType;
  const organizationId = optional(form, "organization_id");
  if (scopeType !== "platform" && organizationId) body.organization_id = organizationId;
  const description = optional(form, "description");
  if (description) body.description = description;
  return mutate("/v1/access/roles", { method: "POST", body: JSON.stringify(body) }, ["/access"]);
}

export async function createUserAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const body: Record<string, unknown> = {
    email: text(form, "email"),
    full_name: text(form, "full_name"),
    password: text(form, "password"),
    user_type: text(form, "user_type") || "internal",
    role_keys: form.getAll("role_keys").map(String),
  };
  for (const key of ["job_title", "department", "organization_id"]) {
    const value = optional(form, key);
    if (value) body[key] = value;
  }
  return mutate("/v1/users", { method: "POST", body: JSON.stringify(body) }, ["/access"]);
}

export async function unlockUserAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "user_id");
  return mutate(`/v1/users/${id}/unlock`, { method: "POST" }, ["/access"]);
}

// ------------------------------------------------------------------ operations

export async function resolveAlertAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "alert_id");
  return mutate(`/v1/monitoring/alerts/${id}/resolve`, { method: "POST" }, ["/operations"]);
}

export async function updateIncidentAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "incident_id");
  const body: Record<string, unknown> = {};
  for (const key of ["status", "severity", "impact", "postmortem"]) {
    const value = optional(form, key);
    if (value !== undefined) body[key] = value;
  }
  return mutate(`/v1/incidents/${id}`, { method: "PATCH", body: JSON.stringify(body) }, [
    "/operations",
  ]);
}

// -------------------------------------------------------------------- settings

export async function changePasswordAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const body = {
    current_password: text(form, "current_password"),
    new_password: text(form, "new_password"),
  };
  return mutate(
    "/v1/auth/password/change",
    { method: "POST", body: JSON.stringify(body) },
    ["/settings"],
    "Password changed. Other sessions have been signed out.",
  );
}

export async function revokeSessionAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  const id = text(form, "session_id");
  return mutate(`/v1/auth/sessions/${id}`, { method: "DELETE" }, ["/settings"]);
}

export async function revokeAllSessionsAction(
  _state: ActionResult | null,
  _form: FormData,
): Promise<ActionResult> {
  return mutate("/v1/auth/sessions/revoke-all", { method: "POST" }, ["/settings"]);
}

export async function enrollMfaAction(
  _state: ActionResult | null,
  _form: FormData,
): Promise<ActionResult> {
  try {
    const payload = await apiFetch<{ secret: string; provisioning_uri: string }>(
      "/v1/auth/mfa/enroll",
      { method: "POST" },
    );
    return {
      ok: true,
      data: { secret: payload.secret, provisioning_uri: payload.provisioning_uri },
    };
  } catch (error) {
    return failure(error);
  }
}

export async function confirmMfaAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  try {
    const payload = await apiFetch<{ recovery_codes: string[] | null }>(
      "/v1/auth/mfa/confirm",
      { method: "POST", body: JSON.stringify({ code: text(form, "code") }) },
    );
    revalidatePath("/settings");
    return {
      ok: true,
      message: "Multi-factor authentication is now enabled.",
      data: { recovery_codes: (payload.recovery_codes ?? []).join("\n") },
    };
  } catch (error) {
    return failure(error);
  }
}

export async function disableMfaAction(
  _state: ActionResult | null,
  form: FormData,
): Promise<ActionResult> {
  return mutate(
    "/v1/auth/mfa/disable",
    {
      method: "POST",
      body: JSON.stringify({ current_password: text(form, "current_password") }),
    },
    ["/settings"],
    "Multi-factor authentication disabled.",
  );
}

export async function noopAction(): Promise<ActionResult> {
  return OK;
}
