export function money(cents: number | null | undefined, currency = "USD"): string {
  const value = (cents ?? 0) / 100;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: value % 1 === 0 ? 0 : 2,
  }).format(value);
}

export function number(value: number | null | undefined): string {
  return new Intl.NumberFormat("en-US").format(value ?? 0);
}

export function percent(value: number | null | undefined, digits = 1): string {
  return `${(value ?? 0).toFixed(digits)}%`;
}

export function date(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return "—";
  const diff = Date.now() - new Date(value).getTime();
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return date(value);
}

export type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "primary";

export function statusTone(status: string | null | undefined): Tone {
  switch ((status ?? "").toLowerCase()) {
    case "active":
    case "healthy":
    case "paid":
    case "success":
    case "resolved":
    case "verified":
    case "enabled":
      return "success";
    case "trial":
    case "trialing":
    case "provisioning":
    case "pending":
    case "degraded":
    case "warning":
    case "investigating":
    case "open":
    case "past_due":
    case "firing":
      return "warning";
    case "suspended":
    case "failed":
    case "failure":
    case "deleted":
    case "archived":
    case "critical":
    case "sev1":
    case "down":
    case "denied":
      return "danger";
    case "draft":
    case "inactive":
    case "neutral":
      return "neutral";
    default:
      return "info";
  }
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export function truncate(value: string, length = 80): string {
  return value.length <= length ? value : `${value.slice(0, length - 1)}…`;
}

export function metricLabel(metric: string): string {
  const labels: Record<string, string> = {
    api_calls: "API calls",
    storage_gb: "Storage (GB)",
    seats: "Seats",
  };
  return labels[metric] ?? titleCase(metric);
}

/** Human wording for audit actions, keeping the raw key available separately. */
export function actionLabel(action: string): string {
  const labels: Record<string, string> = {
    "application.activated": "Application activated",
    "application.updated": "Application updated",
    "application_client.created": "Sign-in client created",
    "application_client.updated": "Sign-in client updated",
    "credential.revoked": "API credential revoked",
    "invoice.paid": "Invoice paid",
    "oauth.authorize": "Sign-in started",
    "oauth.embedded_login": "Embedded sign-in",
    "oauth.token_issued": "Sign-in token issued",
    "oauth.token_refreshed": "Sign-in token refreshed",
    "plan.updated": "Plan updated",
    "security.policy_updated": "Security policy updated",
    "subscription.updated": "Subscription updated",
    "tenant.created": "Workspace created",
    "tenant.domain_verification": "Workspace domain verified",
    "tenant.ownership_transferred": "Workspace ownership transferred",
    "tenant.suspended": "Workspace suspended",
    "usage.reported": "Usage reported",
    "user.role_assigned": "Role assigned",
  };
  return (
    labels[action] ??
    titleCase(action.replace(/[._]+/g, " ").replace(/\boauth\b/gi, "OAuth"))
  );
}

/** Friendly name for the kind of record an activity entry belongs to. */
export function resourceLabel(resource: string | null | undefined): string {
  if (!resource) return "—";
  const labels: Record<string, string> = {
    application: "Application",
    application_client: "Sign-in client",
    application_session: "Session",
    organization: "Organization",
    tenant: "Workspace",
    user: "User",
    role: "Role",
    subscription: "Subscription",
    invoice: "Invoice",
    plan: "Plan",
    alert: "Alert",
    incident: "Incident",
    deployment: "Deployment",
    resource: "Infrastructure",
  };
  return labels[resource] ?? titleCase(resource);
}

/** Friendly name for a permission scope type. */
export function scopeLabel(scope: string | null | undefined): string {
  if (!scope) return "—";
  const labels: Record<string, string> = {
    platform: "Platform",
    organization: "Organization",
    application: "Application",
    tenant: "Workspace",
  };
  return labels[scope] ?? titleCase(scope);
}

/** Friendly name for a subscription or invoice status. */
export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    active: "Active",
    trialing: "Trial",
    past_due: "Past due",
    canceled: "Cancelled",
    unpaid: "Unpaid",
    incomplete: "Incomplete",
    paused: "Paused",
    paid: "Paid",
    open: "Open",
    void: "Void",
    draft: "Draft",
  };
  return labels[status] ?? titleCase(status);
}

/** Friendly name for plan feature keys. */
export function featureLabel(feature: string): string {
  const labels: Record<string, string> = {
    sso: "SSO",
    audit_export: "Audit export",
    api_access: "API access",
    custom_domains: "Custom domains",
    community_support: "Community support",
    email_support: "Email support",
    priority_support: "Priority support",
    dedicated_support: "Dedicated support",
    custom_dpa: "Custom DPA",
  };
  return labels[feature] ?? titleCase(feature);
}
