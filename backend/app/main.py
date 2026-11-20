from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.db import engine
from app.core.errors import install_error_handlers
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.core.rate_limit import close as close_rate_limit
from app.core.security import keyring

settings = get_settings()

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer() if settings.is_production else structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.log_level.upper(), logging.INFO)
    ),
    cache_logger_on_first_use=True,
)
logger = structlog.get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Eagerly load signing material so misconfiguration fails at startup.
    keyring()
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        logger.info("database_ready")
    except Exception as exc:  # noqa: BLE001 - readiness reports the detail
        logger.error("database_unavailable", error=str(exc))
    yield
    await close_rate_limit()
    await engine.dispose()


app = FastAPI(
    title="Basacore API",
    version="0.1.0",
    description=(
        "Centralized application, tenant and company operating platform. "
        "Every protected operation authorizes server-side; tenant data is protected "
        "by PostgreSQL row-level security in addition to application checks."
    ),
    lifespan=lifespan,
    # The schema describes every admin endpoint; keep it off the public surface
    # outside development. Operators can still enable it explicitly.
    docs_url=None if settings.is_production and not settings.expose_api_docs else "/docs",
    redoc_url=None if settings.is_production and not settings.expose_api_docs else "/redoc",
    openapi_url=None if settings.is_production and not settings.expose_api_docs else "/openapi.json",
)

app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    BodySizeLimitMiddleware,
    max_bytes=settings.max_request_bytes,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
    expose_headers=["X-Request-ID"],
    max_age=600,
)

install_error_handlers(app)
app.include_router(api_router, prefix="/v1")


@app.get("/", include_in_schema=False)
async def root() -> dict:
    payload = {
        "name": "Basacore API",
        "version": "0.1.0",
        "health": "/healthz",
        "readiness": "/readyz",
    }
    if settings.is_production and not settings.expose_api_docs:
        return payload
    return payload | {"docs": "/docs"}


@app.get("/healthz", tags=["Health"], summary="Liveness probe")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/livez", tags=["Health"], summary="Liveness probe")
async def livez() -> dict:
    return {"status": "ok"}


@app.get("/readyz", tags=["Health"], summary="Readiness probe with dependency checks")
async def readyz() -> JSONResponse:
    checks: dict[str, str] = {}
    healthy = True
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = "unavailable"
        logger.error("readiness_database_failed", error=str(exc))
        healthy = False

    if settings.redis_url:
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(settings.redis_url, socket_timeout=1)
            await client.ping()
            await client.aclose()
            checks["redis"] = "ok"
        except Exception:  # noqa: BLE001
            # Redis is an accelerator (rate limits fall back in-process), not a
            # hard dependency for correctness.
            checks["redis"] = "degraded"
    else:
        checks["redis"] = "not_configured"

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "not_ready", "checks": checks},
    )


@app.get("/.well-known/jwks.json", tags=["Identity"], summary="Public signing keys")
async def jwks() -> dict:
    return keyring().jwks()


@app.get(
    "/.well-known/oauth-authorization-server",
    tags=["Identity"],
    summary="OAuth 2.0 authorization server metadata",
)
async def oauth_metadata() -> dict:
    return {
        "issuer": settings.jwt_issuer,
        "authorization_endpoint": f"{settings.api_base_url}/v1/oauth/authorize",
        "token_endpoint": f"{settings.api_base_url}/v1/oauth/token",
        "userinfo_endpoint": f"{settings.api_base_url}/v1/oauth/userinfo",
        "revocation_endpoint": f"{settings.api_base_url}/v1/oauth/revoke",
        "end_session_endpoint": f"{settings.api_base_url}/v1/oauth/logout",
        "jwks_uri": f"{settings.api_base_url}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
            "none",
        ],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["openid", "profile", "email", "offline_access"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


@app.get(
    "/.well-known/openid-configuration",
    tags=["Identity"],
    summary="OpenID Connect discovery document",
)
async def openid_configuration() -> dict:
    metadata = await oauth_metadata()
    metadata.update(
        {
            "claims_supported": [
                "sub",
                "iss",
                "aud",
                "exp",
                "iat",
                "auth_time",
                "nonce",
                "email",
                "email_verified",
                "name",
                "preferred_username",
                "app_id",
                "client_id",
                "tenant_id",
                "tenant_slug",
                "roles",
                "permissions",
                "amr",
            ],
            "claims_parameter_supported": False,
            "request_parameter_supported": False,
            "request_uri_parameter_supported": False,
            "prompt_values_supported": ["login", "none"],
            "display_values_supported": ["page"],
        }
    )
    return metadata
