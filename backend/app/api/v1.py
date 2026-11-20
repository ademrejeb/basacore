from fastapi import APIRouter

from app.modules.access.policy_router import router as policy_router
from app.modules.access.router import router as access_router
from app.modules.applications.oauth_router import router as oauth_router
from app.modules.applications.router import router as applications_router
from app.modules.applications.sdk_router import router as sdk_router
from app.modules.audit.router import router as audit_router
from app.modules.billing.router import router as billing_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.identity.router import router as auth_router
from app.modules.identity.router import users_router
from app.modules.infrastructure.router import incidents_router, monitoring_router
from app.modules.infrastructure.router import router as infrastructure_router
from app.modules.organizations.router import router as organizations_router
from app.modules.projects.router import notifications_router, tasks_router
from app.modules.projects.router import router as projects_router
from app.modules.tenants.router import router as tenants_router

api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(access_router)
api_router.include_router(policy_router)
api_router.include_router(organizations_router)
api_router.include_router(applications_router)
api_router.include_router(tenants_router)
api_router.include_router(billing_router)
api_router.include_router(audit_router)
api_router.include_router(dashboard_router)
api_router.include_router(infrastructure_router)
api_router.include_router(incidents_router)
api_router.include_router(monitoring_router)
api_router.include_router(projects_router)
api_router.include_router(tasks_router)
api_router.include_router(notifications_router)
api_router.include_router(oauth_router)
api_router.include_router(sdk_router)
