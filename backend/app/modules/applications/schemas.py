from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.enums import ApplicationStatus, EnvironmentKind, IsolationStrategy

APP_SCOPES = {
    "app:config.read",
    "app:usage.write",
    "app:audit.write",
    "app:tenant.resolve",
    "app:authorization.check",
    "app:identity.read",
}


class ApplicationCreate(BaseModel):
    organization_id: uuid.UUID
    name: str = Field(min_length=2, max_length=200)
    slug: str | None = Field(default=None, max_length=80, pattern=r"^[a-z0-9-]+$")
    description: str | None = Field(default=None, max_length=2000)
    owner_id: uuid.UUID | None = None
    base_url: str | None = Field(default=None, max_length=500)
    isolation_strategy: IsolationStrategy = IsolationStrategy.SHARED
    auth_config: dict[str, Any] = Field(default_factory=dict)
    integration_config: dict[str, Any] = Field(default_factory=dict)
    resource_allocation: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("base_url must be an absolute http(s) URL")
        return value.rstrip("/") if value else value


class ApplicationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    owner_id: uuid.UUID | None = None
    base_url: str | None = Field(default=None, max_length=500)
    isolation_strategy: IsolationStrategy | None = None
    auth_config: dict[str, Any] | None = None
    integration_config: dict[str, Any] | None = None
    resource_allocation: dict[str, Any] | None = None
    status: ApplicationStatus | None = None


class StatusChange(BaseModel):
    status: ApplicationStatus
    reason: str | None = Field(default=None, max_length=300)


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    slug: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    kind: EnvironmentKind = EnvironmentKind.CUSTOM
    base_url: str | None = Field(default=None, max_length=500)
    api_base_url: str | None = Field(default=None, max_length=500)
    is_default: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class EnvironmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    base_url: str | None = Field(default=None, max_length=500)
    api_base_url: str | None = Field(default=None, max_length=500)
    status: str | None = Field(default=None, pattern=r"^(pending|active|degraded|down|maintenance)$")
    is_default: bool | None = None
    config: dict[str, Any] | None = None


class EnvironmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    name: str
    slug: str
    kind: str
    base_url: str | None
    api_base_url: str | None
    status: str
    is_default: bool
    config: dict[str, Any]
    last_health_check_at: datetime | None
    health_latency_ms: int | None
    created_at: datetime


class CredentialCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    scopes: list[str] = Field(min_length=1)
    expires_in_days: int | None = Field(default=None, ge=1, le=730)

    @field_validator("scopes")
    @classmethod
    def valid_scopes(cls, value: list[str]) -> list[str]:
        unknown = set(value) - APP_SCOPES
        if unknown:
            raise ValueError(f"Unknown scopes: {sorted(unknown)}")
        return sorted(set(value))


class ConfigPublish(BaseModel):
    config: dict[str, Any]
    environment_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=300)


class FeatureFlagCreate(BaseModel):
    key: str = Field(min_length=2, max_length=120, pattern=r"^[a-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool = False
    rollout_percentage: int = Field(default=100, ge=0, le=100)


class FeatureFlagUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None
    rollout_percentage: int | None = Field(default=None, ge=0, le=100)


class FeatureFlagOverrideRequest(BaseModel):
    enabled: bool


class IntegrationCreate(BaseModel):
    provider: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=2, max_length=120)
    config: dict[str, Any] = Field(default_factory=dict)


LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _require_absolute_url(value: str, label: str) -> urlsplit:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"{label} must be an absolute http(s) URL")
    if parts.username or parts.password:
        raise ValueError(f"{label} must not contain userinfo")
    if parts.fragment:
        raise ValueError(f"{label} must not contain a fragment")
    return parts


def _https_or_loopback(value: str, label: str) -> str:
    parts = _require_absolute_url(value, label)
    if parts.scheme == "https":
        return value
    if parts.hostname in LOOPBACK_HOSTS:
        return value
    raise ValueError(f"{label} must use HTTPS, or HTTP on localhost/127.0.0.1/[::1]")


class WebhookCreate(BaseModel):
    url: str = Field(max_length=500)
    description: str | None = Field(default=None, max_length=300)
    events: list[str] = Field(min_length=1)
    active: bool = True

    @field_validator("url")
    @classmethod
    def https_only(cls, value: str) -> str:
        return _https_or_loopback(value.strip(), "Webhook URL")


CLIENT_SCOPES = {"openid", "profile", "email", "offline_access"}


def _validate_redirect_uris(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        value = value.strip()
        if not value:
            continue
        _https_or_loopback(value, "Redirect URI")
        if value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        raise ValueError("At least one redirect URI is required")
    return cleaned


def _validate_origins(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        value = value.strip().rstrip("/")
        if not value:
            continue
        parts = _require_absolute_url(value, "Allowed origin")
        if parts.path not in ("", "/") or parts.query:
            raise ValueError("Allowed origins must contain only scheme and host")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


class ApplicationClientCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    client_type: Literal["confidential", "public"] = "confidential"
    redirect_uris: list[str] = Field(min_length=1)
    post_logout_redirect_uris: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    pkce_required: bool = True

    @field_validator("redirect_uris")
    @classmethod
    def valid_redirects(cls, value: list[str]) -> list[str]:
        return _validate_redirect_uris(value)

    @field_validator("post_logout_redirect_uris")
    @classmethod
    def valid_logout_redirects(cls, value: list[str]) -> list[str]:
        if not value:
            return []
        return _validate_redirect_uris(value)

    @field_validator("allowed_origins")
    @classmethod
    def valid_origins(cls, value: list[str]) -> list[str]:
        return _validate_origins(value)

    @field_validator("scopes")
    @classmethod
    def valid_scopes(cls, value: list[str]) -> list[str]:
        unknown = set(value) - CLIENT_SCOPES
        if unknown:
            raise ValueError(f"Unknown scopes: {sorted(unknown)}")
        if "openid" not in value:
            raise ValueError("The openid scope is required for end-user login")
        return sorted(set(value))


class ApplicationClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    redirect_uris: list[str] | None = None
    post_logout_redirect_uris: list[str] | None = None
    allowed_origins: list[str] | None = None
    scopes: list[str] | None = None
    pkce_required: bool | None = None
    status: Literal["active", "disabled"] | None = None

    @field_validator("redirect_uris")
    @classmethod
    def valid_redirects(cls, value: list[str] | None) -> list[str] | None:
        return _validate_redirect_uris(value) if value is not None else None

    @field_validator("post_logout_redirect_uris")
    @classmethod
    def valid_logout_redirects(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _validate_redirect_uris(value) if value else []

    @field_validator("allowed_origins")
    @classmethod
    def valid_origins(cls, value: list[str] | None) -> list[str] | None:
        return _validate_origins(value) if value is not None else None

    @field_validator("scopes")
    @classmethod
    def valid_scopes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = set(value) - CLIENT_SCOPES
        if unknown:
            raise ValueError(f"Unknown scopes: {sorted(unknown)}")
        if "openid" not in value:
            raise ValueError("The openid scope is required for end-user login")
        return sorted(set(value))


class EmbeddedLoginRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    client_secret: str = Field(min_length=1, max_length=256)
    tenant: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    scope: str | None = Field(default=None, max_length=500)


class EmbeddedMfaRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    client_secret: str = Field(min_length=1, max_length=256)
    tenant: str = Field(min_length=1, max_length=80)
    challenge_token: str
    code: str = Field(min_length=6, max_length=20)
    scope: str | None = Field(default=None, max_length=500)
