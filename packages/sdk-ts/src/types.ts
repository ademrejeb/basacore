export type TenantStatus =
  | "provisioning"
  | "trial"
  | "active"
  | "suspended"
  | "archived"
  | "deleted";

export type AuditOutcome = "success" | "failure" | "denied";

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  scope: string;
}

export interface ServiceIdentity {
  application: {
    id: string;
    name: string;
    slug: string;
    status: string;
  } | null;
  credential_id: string;
  scopes: string[];
}

export interface TenantSummary {
  id: string;
  name: string;
  slug: string;
  status: TenantStatus | string;
  isolation_strategy: string;
  branding: Record<string, unknown>;
  quotas: Record<string, number>;
}

export interface TenantList {
  items: TenantSummary[];
  limit: number;
  offset: number;
}

export interface TenantContext {
  tenant: TenantSummary;
  application_id: string;
  config: Record<string, unknown>;
  features: Record<string, boolean>;
  plan: {
    id: string;
    key: string;
    name: string;
    limits: Record<string, unknown>;
    features: string[];
  } | null;
  subscription: {
    id: string;
    status: string;
    current_period_end: string | null;
    cancel_at_period_end: boolean;
  } | null;
  usage: Record<string, number>;
  config_version: number;
  generated_at: string;
}

export interface AuthorizationCheckResult {
  authorized: boolean;
  reason?: string;
  user_id: string;
  tenant_id: string;
  permission: string;
  effective_permissions: string[];
}

export interface UsageReportResult {
  id: string;
  metric: string;
  quantity: number;
  period_start: string;
  idempotent: boolean;
}

export interface UsageSummary {
  tenant_id: string;
  period_start: string;
  generated_at: string;
  metrics: Record<string, number>;
}

export interface AuditWriteResult {
  id: string;
  created_at: string;
}

export interface ResolveTenantParams {
  slug?: string;
  domain?: string;
}
