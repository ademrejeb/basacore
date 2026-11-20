"""Authorization catalogue: the single source of truth for permissions and roles.

Permissions are explicit ``resource.action`` strings. Roles are seedable
defaults; organizations may create custom roles with any subset of permissions.
Scope is part of the grant, never part of a client-supplied header.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import ScopeType


@dataclass(frozen=True)
class PermissionSpec:
    key: str
    description: str
    category: str


PERMISSIONS: tuple[PermissionSpec, ...] = (
    # Platform / application registry
    PermissionSpec("dashboard.read", "View the global dashboard", "platform"),
    PermissionSpec("organizations.read", "View organizations", "organization"),
    PermissionSpec("organizations.create", "Create organizations", "organization"),
    PermissionSpec("organizations.update", "Update organizations", "organization"),
    PermissionSpec("organizations.delete", "Archive organizations", "organization"),
    PermissionSpec("applications.read", "View applications", "application"),
    PermissionSpec("applications.create", "Register applications", "application"),
    PermissionSpec("applications.update", "Update application settings", "application"),
    PermissionSpec("applications.delete", "Archive applications", "application"),
    PermissionSpec("applications.activate", "Activate or deactivate applications", "application"),
    PermissionSpec("environments.manage", "Manage application environments", "application"),
    PermissionSpec("credentials.read", "View API credential metadata", "application"),
    PermissionSpec("credentials.manage", "Create, rotate and revoke API credentials", "application"),
    # Tenancy
    PermissionSpec("tenants.read", "View tenants", "tenant"),
    PermissionSpec("tenants.create", "Create tenants", "tenant"),
    PermissionSpec("tenants.update", "Update tenant settings", "tenant"),
    PermissionSpec("tenants.suspend", "Suspend or reactivate tenants", "tenant"),
    PermissionSpec("tenants.transfer", "Transfer tenant ownership", "tenant"),
    PermissionSpec("tenants.delete", "Archive or delete tenants", "tenant"),
    PermissionSpec("tenant.users.read", "View tenant users", "tenant"),
    PermissionSpec("tenant.users.invite", "Invite tenant users", "tenant"),
    PermissionSpec("tenant.users.update", "Update tenant users", "tenant"),
    PermissionSpec("tenant.users.disable", "Disable tenant users", "tenant"),
    PermissionSpec("tenant.billing.read", "View tenant billing", "tenant"),
    PermissionSpec("tenant.usage.read", "View tenant usage", "tenant"),
    PermissionSpec("tenant.config.read", "View tenant configuration", "tenant"),
    PermissionSpec("tenant.config.update", "Update tenant configuration", "tenant"),
    PermissionSpec("tenant.security.read", "View tenant security events", "tenant"),
    PermissionSpec("tenant.domains.read", "View tenant domains", "tenant"),
    PermissionSpec("tenant.domains.update", "Manage tenant domains", "tenant"),
    PermissionSpec("tenant.audit.read", "View tenant audit trail", "tenant"),
    # Identity & access
    PermissionSpec("users.read", "View users and identities", "identity"),
    PermissionSpec("users.invite", "Invite users", "identity"),
    PermissionSpec("users.update", "Update users", "identity"),
    PermissionSpec("users.disable", "Disable users", "identity"),
    PermissionSpec("sessions.read", "View active sessions", "identity"),
    PermissionSpec("sessions.revoke", "Revoke sessions", "identity"),
    PermissionSpec("roles.read", "View roles and grants", "identity"),
    PermissionSpec("roles.manage", "Create roles and assign grants", "identity"),
    # Security
    PermissionSpec("security.policies.read", "View security policies", "security"),
    PermissionSpec("security.policies.manage", "Manage security policies", "security"),
    PermissionSpec("security.events.read", "View security events", "security"),
    PermissionSpec("security.incidents.read", "View security incidents", "security"),
    PermissionSpec("security.incidents.manage", "Manage security incidents", "security"),
    PermissionSpec("secrets.read", "View secret metadata", "security"),
    PermissionSpec("secrets.manage", "Create, rotate and revoke secrets", "security"),
    PermissionSpec("audit.read", "Read the audit log", "security"),
    # Billing
    PermissionSpec("plans.read", "View plans", "billing"),
    PermissionSpec("plans.manage", "Create and edit plans", "billing"),
    PermissionSpec("subscriptions.read", "View subscriptions", "billing"),
    PermissionSpec("subscriptions.manage", "Change subscriptions", "billing"),
    PermissionSpec("billing.read", "View billing and invoices", "billing"),
    PermissionSpec("billing.manage", "Manage invoices and payments", "billing"),
    PermissionSpec("usage.read", "View usage and quotas", "billing"),
    # Configuration
    PermissionSpec("config.read", "Read application and tenant configuration", "configuration"),
    PermissionSpec("config.manage", "Publish configuration versions", "configuration"),
    PermissionSpec("flags.read", "View feature flags", "configuration"),
    PermissionSpec("flags.manage", "Manage feature flags and rollouts", "configuration"),
    # Integrations
    PermissionSpec("integrations.read", "View integrations and webhooks", "integration"),
    PermissionSpec("integrations.manage", "Manage integrations and webhooks", "integration"),
    # Infrastructure & observability
    PermissionSpec("infrastructure.read", "View infrastructure inventory", "infrastructure"),
    PermissionSpec("infrastructure.manage", "Manage infrastructure records", "infrastructure"),
    PermissionSpec("deployments.read", "View deployments", "infrastructure"),
    PermissionSpec("deployments.manage", "Record and roll back deployments", "infrastructure"),
    PermissionSpec("monitoring.read", "View monitoring and health", "observability"),
    PermissionSpec("monitoring.manage", "Manage alerts and monitors", "observability"),
    PermissionSpec("incidents.read", "View incidents", "observability"),
    PermissionSpec("incidents.manage", "Manage incidents", "observability"),
    # Internal operations
    PermissionSpec("projects.read", "View internal projects", "operations"),
    PermissionSpec("projects.manage", "Manage internal projects", "operations"),
    PermissionSpec("tasks.read", "View internal tasks", "operations"),
    PermissionSpec("tasks.manage", "Manage internal tasks", "operations"),
    PermissionSpec("workflows.read", "View automations", "operations"),
    PermissionSpec("workflows.manage", "Manage automations", "operations"),
    PermissionSpec("notifications.read", "View notifications", "operations"),
    PermissionSpec("notifications.manage", "Manage notification settings", "operations"),
    PermissionSpec("files.read", "View file metadata", "operations"),
    PermissionSpec("files.manage", "Manage files", "operations"),
    PermissionSpec("settings.read", "View platform settings", "platform"),
    PermissionSpec("settings.manage", "Manage platform settings", "platform"),
)

PERMISSION_KEYS: frozenset[str] = frozenset(p.key for p in PERMISSIONS)


@dataclass(frozen=True)
class RoleSpec:
    key: str
    name: str
    description: str
    scope_type: ScopeType
    permissions: tuple[str, ...]
    parent: str | None = None


PLATFORM_READ = (
    "dashboard.read",
    "organizations.read",
    "applications.read",
    "tenants.read",
    "users.read",
    "roles.read",
    "billing.read",
    "plans.read",
    "subscriptions.read",
    "usage.read",
    "config.read",
    "flags.read",
    "integrations.read",
    "infrastructure.read",
    "deployments.read",
    "monitoring.read",
    "incidents.read",
    "audit.read",
    "security.events.read",
    "security.incidents.read",
    "security.policies.read",
    "secrets.read",
    "credentials.read",
    "sessions.read",
    "projects.read",
    "tasks.read",
    "workflows.read",
    "notifications.read",
    "files.read",
    "settings.read",
)

TENANT_ADMIN_PERMISSIONS = (
    "tenants.read",
    "tenants.update",
    "tenant.users.read",
    "tenant.users.invite",
    "tenant.users.update",
    "tenant.users.disable",
    "tenant.billing.read",
    "tenant.usage.read",
    "tenant.config.read",
    "tenant.config.update",
    "tenant.security.read",
    "tenant.domains.read",
    "tenant.domains.update",
    "tenant.audit.read",
)

ROLES: tuple[RoleSpec, ...] = (
    RoleSpec(
        "super_admin",
        "Super Administrator",
        "Unrestricted platform operator. Reserved for break-glass accounts.",
        ScopeType.PLATFORM,
        ("*",),
    ),
    RoleSpec(
        "platform_admin",
        "Platform Administrator",
        "Day-to-day operator of applications, tenants, users and configuration.",
        ScopeType.PLATFORM,
        tuple(sorted(PERMISSION_KEYS - {"settings.manage", "secrets.manage"})),
    ),
    RoleSpec(
        "security_admin",
        "Security Administrator",
        "Owns authentication policy, security incidents, secrets and audit.",
        ScopeType.PLATFORM,
        (
            "dashboard.read",
            "applications.read",
            "tenants.read",
            "users.read",
            "users.update",
            "users.disable",
            "sessions.read",
            "sessions.revoke",
            "roles.read",
            "roles.manage",
            "security.policies.read",
            "security.policies.manage",
            "security.events.read",
            "security.incidents.read",
            "security.incidents.manage",
            "secrets.read",
            "secrets.manage",
            "audit.read",
            "credentials.read",
            "credentials.manage",
            "monitoring.read",
            "incidents.read",
            "incidents.manage",
            "settings.read",
        ),
    ),
    RoleSpec(
        "infrastructure_admin",
        "Infrastructure Administrator",
        "Runs environments, deployments, monitoring and incident response.",
        ScopeType.PLATFORM,
        (
            "dashboard.read",
            "applications.read",
            "tenants.read",
            "environments.manage",
            "infrastructure.read",
            "infrastructure.manage",
            "deployments.read",
            "deployments.manage",
            "monitoring.read",
            "monitoring.manage",
            "incidents.read",
            "incidents.manage",
            "security.incidents.read",
            "audit.read",
            "projects.read",
            "tasks.read",
            "tasks.manage",
        ),
    ),
    RoleSpec(
        "finance_admin",
        "Finance Administrator",
        "Owns plans, subscriptions, invoicing and revenue reporting.",
        ScopeType.PLATFORM,
        (
            "dashboard.read",
            "applications.read",
            "tenants.read",
            "plans.read",
            "plans.manage",
            "subscriptions.read",
            "subscriptions.manage",
            "billing.read",
            "billing.manage",
            "usage.read",
            "audit.read",
            "projects.read",
            "tasks.read",
        ),
    ),
    RoleSpec(
        "support_agent",
        "Support Agent",
        "Customer support with scoped read and tenant remediation access.",
        ScopeType.PLATFORM,
        (
            "dashboard.read",
            "applications.read",
            "tenants.read",
            "tenants.update",
            "tenants.suspend",
            "users.read",
            "sessions.read",
            "billing.read",
            "subscriptions.read",
            "usage.read",
            "config.read",
            "flags.read",
            "incidents.read",
            "monitoring.read",
            "security.events.read",
            "audit.read",
            "notifications.read",
        ),
    ),
    RoleSpec(
        "auditor",
        "Auditor",
        "Read-only evidence access across all platform domains.",
        ScopeType.PLATFORM,
        PLATFORM_READ,
    ),
    # Organization roles
    RoleSpec(
        "org_owner",
        "Organization Owner",
        "Owns the organization, its applications, billing and team.",
        ScopeType.ORGANIZATION,
        (
            "organizations.read",
            "organizations.update",
            "applications.read",
            "applications.create",
            "applications.update",
            "tenants.read",
            "tenants.create",
            "users.read",
            "users.invite",
            "billing.read",
            "subscriptions.read",
            "usage.read",
            "audit.read",
            "projects.read",
            "projects.manage",
            "tasks.read",
            "tasks.manage",
        ),
    ),
    RoleSpec(
        "org_admin",
        "Organization Administrator",
        "Manages the organization's applications, people and projects.",
        ScopeType.ORGANIZATION,
        (
            "organizations.read",
            "applications.read",
            "applications.create",
            "applications.update",
            "tenants.read",
            "tenants.create",
            "users.read",
            "users.invite",
            "billing.read",
            "usage.read",
            "projects.read",
            "projects.manage",
            "tasks.read",
            "tasks.manage",
        ),
    ),
    RoleSpec(
        "org_member",
        "Organization Member",
        "Read access to organization resources and project work.",
        ScopeType.ORGANIZATION,
        (
            "organizations.read",
            "applications.read",
            "tenants.read",
            "users.read",
            "projects.read",
            "tasks.read",
            "tasks.manage",
        ),
    ),
    # Application roles
    RoleSpec(
        "app_owner",
        "Application Owner",
        "Accountable owner of an application and its tenants.",
        ScopeType.APPLICATION,
        (
            "applications.read",
            "applications.update",
            "environments.manage",
            "tenants.read",
            "tenants.create",
            "tenants.update",
            "tenants.suspend",
            "tenant.users.read",
            "credentials.read",
            "credentials.manage",
            "plans.read",
            "subscriptions.read",
            "usage.read",
            "config.read",
            "config.manage",
            "flags.read",
            "flags.manage",
            "integrations.read",
            "integrations.manage",
            "deployments.read",
            "monitoring.read",
            "incidents.read",
            "audit.read",
        ),
    ),
    RoleSpec(
        "app_admin",
        "Application Administrator",
        "Operates an application without ownership transfer rights.",
        ScopeType.APPLICATION,
        (
            "applications.read",
            "environments.manage",
            "tenants.read",
            "tenants.update",
            "credentials.read",
            "credentials.manage",
            "plans.read",
            "subscriptions.read",
            "usage.read",
            "config.read",
            "config.manage",
            "flags.read",
            "flags.manage",
            "integrations.read",
            "deployments.read",
            "monitoring.read",
            "incidents.read",
        ),
    ),
    RoleSpec(
        "app_developer",
        "Application Developer",
        "Builds on the platform: config, flags, deployments and usage.",
        ScopeType.APPLICATION,
        (
            "applications.read",
            "tenants.read",
            "config.read",
            "flags.read",
            "credentials.read",
            "deployments.read",
            "monitoring.read",
            "usage.read",
        ),
    ),
    RoleSpec(
        "app_viewer",
        "Application Viewer",
        "Read-only access to an application and its tenants.",
        ScopeType.APPLICATION,
        ("applications.read", "tenants.read", "config.read", "flags.read", "usage.read", "monitoring.read"),
    ),
    # Tenant roles
    RoleSpec(
        "tenant_owner",
        "Tenant Owner",
        "Owns a tenant: people, billing, configuration, domains and audit.",
        ScopeType.TENANT,
        TENANT_ADMIN_PERMISSIONS,
    ),
    RoleSpec(
        "tenant_admin",
        "Tenant Administrator",
        "Administers a tenant's users and settings.",
        ScopeType.TENANT,
        TENANT_ADMIN_PERMISSIONS,
    ),
    RoleSpec(
        "tenant_manager",
        "Tenant Manager",
        "Coordinates a tenant team with read access to billing and usage.",
        ScopeType.TENANT,
        (
            "tenants.read",
            "tenant.users.read",
            "tenant.users.invite",
            "tenant.billing.read",
            "tenant.usage.read",
            "tenant.config.read",
            "tenant.security.read",
            "tenant.audit.read",
        ),
    ),
    RoleSpec(
        "tenant_member",
        "Tenant Member",
        "Standard customer with access to their own tenant.",
        ScopeType.TENANT,
        ("tenants.read", "tenant.usage.read", "tenant.config.read"),
    ),
)

ROLE_BY_KEY: dict[str, RoleSpec] = {r.key: r for r in ROLES}


def permissions_for_role_key(role_key: str) -> set[str]:
    role = ROLE_BY_KEY[role_key]
    if "*" in role.permissions:
        return set(PERMISSION_KEYS)
    return set(role.permissions)


def ensure_known_permissions(keys: set[str]) -> None:
    unknown = keys - PERMISSION_KEYS
    if unknown:
        raise ValueError(f"Unknown permissions: {sorted(unknown)}")


def exceeded_grants(requested: set[str], actor: set[str]) -> set[str]:
    """Return the grants in ``requested`` that the actor does not hold itself.

    Delegated administration must never be a ladder: an operator with
    ``roles.manage`` could otherwise mint a role carrying ``secrets.manage`` (or
    assign the ``super_admin`` wildcard) and escalate beyond its own ceiling. An
    actor may only hand out authority it already holds in the same scope.
    """
    if "*" in actor:
        return set()
    return set(requested) - set(actor)
