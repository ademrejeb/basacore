export interface CurrentUser {
  id: string;
  email: string;
  full_name: string;
  user_type: string;
  status: string;
  avatar_url: string | null;
  job_title: string | null;
  department: string | null;
  mfa_enabled: boolean;
  last_login_at: string | null;
}

export interface Scope {
  scope_type: string;
  scope_id: string | null;
  scope_name: string;
  role_keys: string[];
  permissions: string[];
}

export interface Session {
  user: CurrentUser;
  scopes: Scope[];
  platform_permissions: string[];
  memberships: Array<Record<string, unknown>>;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  status: string;
  billing_email: string | null;
  logo_url: string | null;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  counts?: {
    applications?: number;
    tenants?: number;
    users?: number;
  };
}

export interface OrganizationDetail extends Organization {
  counts: {
    applications: number;
    tenants: number;
    application_owners: number;
    users: number;
  };
}

export interface Application {
  id: string;
  organization_id: string;
  name: string;
  slug: string;
  description: string | null;
  status: string;
  owner_id: string | null;
  base_url: string | null;
  isolation_strategy: string;
  auth_config: Record<string, unknown>;
  integration_config: Record<string, unknown>;
  subscription_config: Record<string, unknown>;
  resource_allocation: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  organization_name?: string | null;
  owner_name?: string | null;
  counts?: {
    tenants?: number;
    active?: number;
    trial?: number;
    suspended?: number;
  };
}

export interface Environment {
  id: string;
  application_id: string;
  name: string;
  slug: string;
  kind: string;
  base_url: string | null;
  api_base_url: string | null;
  status: string;
  is_default: boolean;
  config: Record<string, unknown>;
  last_health_check_at: string | null;
  health_latency_ms: number | null;
  created_at: string;
}

export interface FeatureFlag {
  id: string;
  key: string;
  name: string;
  description: string | null;
  enabled: boolean;
  rollout_percentage: number;
  updated_at: string;
}

export interface Credential {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  status: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface ApplicationClient {
  id: string;
  application_id: string;
  name: string;
  client_id: string;
  client_type: "confidential" | "public";
  redirect_uris: string[];
  post_logout_redirect_uris: string[];
  allowed_origins: string[];
  scopes: string[];
  pkce_required: boolean;
  status: string;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConfigVersion {
  id: string;
  version: number;
  environment_id?: string | null;
  note: string | null;
  config: Record<string, unknown>;
  created_at: string;
}

export interface ApplicationDetail extends Application {
  organization_name: string | null;
  owner: { id: string; name: string; email: string } | null;
  counts: {
    tenants?: number;
    active?: number;
    trial?: number;
    suspended?: number;
  };
  environments: Environment[];
  flags: FeatureFlag[];
  credentials: Credential[];
  clients: ApplicationClient[];
  config_history: ConfigVersion[];
}

export interface Tenant {
  id: string;
  application_id: string;
  organization_id: string;
  name: string;
  slug: string;
  status: string;
  owner_id: string | null;
  isolation_strategy: string;
  quotas: Record<string, number>;
  configuration: Record<string, unknown>;
  branding: Record<string, unknown>;
  trial_ends_at: string | null;
  suspended_at: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  application_name: string | null;
  organization_name?: string | null;
  plan_name: string | null;
  subscription_status: string | null;
  mrr_cents: number;
  users: number;
}

export interface TenantUser {
  id: string;
  email: string;
  full_name: string;
  status: string;
  membership_status: string;
  is_billing_contact?: boolean;
  mfa_enabled: boolean;
  roles?: string[];
  role_names?: string[];
  last_login_at: string | null;
  joined_at?: string | null;
}

export interface TenantDomain {
  id: string;
  domain: string;
  is_primary: boolean;
  status: string;
  verification_token: string;
  ssl_status: string;
  ssl_expires_at: string | null;
  verified_at: string | null;
}

export interface UsageLine {
  metric: string;
  used: number;
  limit: number;
  percent: number;
  over_limit: boolean;
}

export interface TenantDetail extends Omit<Tenant, "users"> {
  domains: TenantDomain[];
  counts: { users: number };
  users: TenantUser[];
  subscription: {
    id: string;
    status: string;
    billing_interval: string;
    plan_id: string;
    plan_name: string | null;
    plan_key: string | null;
    mrr_cents: number;
    current_period_start: string;
    current_period_end: string;
    trial_ends_at: string | null;
    cancel_at_period_end: boolean;
    discount_percent: number;
  } | null;
  usage: UsageLine[];
  invoices: Array<{
    id: string;
    number: string;
    amount_cents: number;
    currency: string;
    status: string;
    issued_at: string;
    due_at: string;
    paid_at: string | null;
  }>;
  config_history: ConfigVersion[];
  features: Array<{
    id: string;
    key: string;
    name: string;
    enabled: boolean;
    rollout_percentage: number;
    override: boolean | null;
  }>;
  activity: Array<Record<string, unknown>>;
}

export interface Plan {
  id: string;
  application_id: string | null;
  key: string;
  name: string;
  description: string | null;
  price_monthly_cents: number;
  price_yearly_cents: number;
  currency: string;
  limits: Record<string, number>;
  features: string[];
  is_public: boolean;
  sort_order: number;
}

export interface Subscription {
  id: string;
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  tenant_status: string;
  application_id: string;
  application_name: string;
  plan_id: string;
  plan_name: string;
  plan_key: string;
  status: string;
  billing_interval: string;
  mrr_cents: number;
  current_period_start: string;
  current_period_end: string;
  trial_ends_at: string | null;
  cancel_at_period_end: boolean;
  discount_percent: number;
}

export interface Invoice {
  id: string;
  subscription_id: string;
  number: string;
  amount_cents: number;
  currency: string;
  status: string;
  period_start: string;
  period_end: string;
  issued_at: string;
  due_at: string;
  paid_at: string | null;
  created_at: string;
  tenant_id: string;
  tenant_name: string;
}

export interface BillingSummary {
  mrr_cents: number;
  arr_cents: number;
  active_subscriptions: number;
  status_distribution: Record<string, number>;
  plan_distribution: Record<string, number>;
  open_invoices: number;
  outstanding_cents: number;
}

export interface Role {
  id: string;
  key: string;
  name: string;
  description: string;
  scope_type: string;
  organization_id: string | null;
  is_system: boolean;
  permissions: string[];
  assignments: number;
  created_at: string;
}

export interface Permission {
  key: string;
  description: string;
  category: string;
}

export interface AuditLog {
  id: string;
  actor_id: string | null;
  actor_email: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  scope_type: string;
  scope_id: string | null;
  outcome: string;
  ip_address: string | null;
  user_agent: string | null;
  request_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface DashboardSummary {
  generated_at: string;
  organizations: { total: number; active: number };
  applications: { total: number; active: number };
  tenants: {
    total: number;
    active: number;
    trial: number;
    suspended: number;
    provisioning: number;
    archived: number;
    new_this_month: number;
  };
  users: { total: number; internal: number; external: number; active: number };
  billing: {
    mrr_cents: number;
    arr_cents: number;
    active_subscriptions: number;
    open_invoices: number;
    outstanding_cents: number;
  };
  incidents: { open: number; sev1: number; sev2: number };
  trends: {
    tenant_growth: Array<{ month: string; tenants: number }>;
    revenue: Array<{ month: string; revenue_cents: number }>;
  };
  application_distribution: Array<{ name: string; tenants: number }>;
  recent_audit: AuditLog[];
}

export interface InfrastructureSummary {
  total_resources: number;
  by_type: Array<{ type: string; count: number; monthly_cost_cents: number }>;
  by_status: Record<string, number>;
  monthly_cost_cents: number;
}

export interface Incident {
  id: string;
  title: string;
  description: string;
  severity: string;
  status: string;
  application_id: string | null;
  application_name: string | null;
  tenant_id: string | null;
  owner_id: string | null;
  impact: string | null;
  started_at: string;
  resolved_at: string | null;
  postmortem: string | null;
}

export interface Alert {
  id: string;
  name: string;
  severity: string;
  status: string;
  condition: Record<string, unknown>;
  application_id: string;
  application_name: string;
  last_triggered_at: string;
  resolved_at: string | null;
}

export interface AuthSession {
  id: string;
  user_id: string;
  ip_address: string | null;
  user_agent: string | null;
  location: string | null;
  mfa_verified: boolean;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  revoked_at: string | null;
  current: boolean;
}

export interface AuthEvent {
  id: string;
  email: string | null;
  event_type: string;
  outcome: string;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
  event_metadata: Record<string, unknown>;
}

export interface UsageReport {
  period_start: string;
  generated_at: string;
  tenant_count: number;
  over_limit_count: number;
  metrics: Array<{ metric: string; total: number }>;
  tenants: Array<{
    id: string;
    name: string;
    slug: string;
    status: string;
    application_id: string;
    application_name: string | null;
    metrics: UsageLine[];
  }>;
}

export interface UserAccount {
  id: string;
  email: string;
  full_name: string;
  user_type: string;
  status: string;
  avatar_url: string | null;
  job_title: string | null;
  department: string | null;
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
  email_verified_at: string | null;
  failed_login_count: number;
  locked_until: string | null;
  roles: Array<{
    role_id: string;
    role_key: string;
    role_name: string;
    scope_type: string;
    scope_id: string | null;
  }>;
}
