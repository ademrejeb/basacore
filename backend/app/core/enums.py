from __future__ import annotations

import enum


class UserType(str, enum.Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class UserStatus(str, enum.Enum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"


class OrganizationStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class ApplicationStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class EnvironmentKind(str, enum.Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    CUSTOM = "custom"


class EnvironmentStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DEGRADED = "degraded"
    DOWN = "down"
    MAINTENANCE = "maintenance"


class TenantStatus(str, enum.Enum):
    PROVISIONING = "provisioning"
    TRIAL = "trial"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"
    DELETED = "deleted"


class IsolationStrategy(str, enum.Enum):
    SHARED = "shared_database"
    SCHEMA = "schema_per_tenant"
    DATABASE = "database_per_tenant"
    DEDICATED = "dedicated_infrastructure"


class ScopeType(str, enum.Enum):
    PLATFORM = "platform"
    ORGANIZATION = "organization"
    APPLICATION = "application"
    TENANT = "tenant"


class MembershipStatus(str, enum.Enum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class SubscriptionStatus(str, enum.Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    PAUSED = "paused"
    CANCELED = "canceled"
    EXPIRED = "expired"


class BillingInterval(str, enum.Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    VOID = "void"
    UNCOLLECTIBLE = "uncollectible"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"


class AuditOutcome(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class DomainStatus(str, enum.Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"


class SslStatus(str, enum.Enum):
    PENDING = "pending"
    ISSUED = "issued"
    EXPIRING = "expiring"
    EXPIRED = "expired"


class CredentialStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class IncidentSeverity(str, enum.Enum):
    SEV1 = "sev1"
    SEV2 = "sev2"
    SEV3 = "sev3"
    SEV4 = "sev4"


class IncidentStatus(str, enum.Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class TaskStatus(str, enum.Enum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    CANCELED = "canceled"


class TaskPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class ProjectStatus(str, enum.Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class DeploymentStatus(str, enum.Enum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ResourceType(str, enum.Enum):
    SERVER = "server"
    CONTAINER = "container"
    DATABASE = "database"
    STORAGE = "storage"
    NETWORK = "network"
    LOAD_BALANCER = "load_balancer"
    MANAGED_SERVICE = "managed_service"


class ResourceStatus(str, enum.Enum):
    PROVISIONING = "provisioning"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    RETIRED = "retired"


class WorkflowStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
