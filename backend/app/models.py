"""Import every model so Alembic and the application see one metadata graph."""

from app.modules.access.models import (  # noqa: F401
    Permission,
    Role,
    RolePermission,
    SecurityPolicy,
    UserRole,
)
from app.modules.applications.models import (  # noqa: F401
    ApiCredential,
    Application,
    ApplicationClient,
    ApplicationConfiguration,
    ApplicationEnvironment,
    ApplicationSession,
    AuthorizationCode,
    FeatureFlag,
    FeatureFlagOverride,
    Integration,
    Webhook,
)
from app.modules.audit.models import AuditLog, FileObject, SecretMetadata  # noqa: F401
from app.modules.billing.models import (  # noqa: F401
    Invoice,
    Payment,
    Plan,
    Subscription,
    UsageRecord,
)
from app.modules.identity.models import (  # noqa: F401
    AuthenticationEvent,
    MfaRecoveryCode,
    PasswordHistory,
    User,
    UserSession,
    VerificationToken,
)
from app.modules.infrastructure.models import (  # noqa: F401
    Deployment,
    Incident,
    InfrastructureResource,
    MonitoringAlert,
)
from app.modules.organizations.models import Organization  # noqa: F401
from app.modules.projects.models import (  # noqa: F401
    Notification,
    Project,
    Task,
    TaskComment,
    WorkflowDefinition,
    WorkflowExecution,
)
from app.modules.tenants.models import (  # noqa: F401
    Tenant,
    TenantConfiguration,
    TenantDomain,
    TenantMembership,
    TenantResourceQuota,
)
