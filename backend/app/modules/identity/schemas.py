from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.enums import UserStatus, UserType


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    remember_me: bool = False


class MfaVerifyRequest(BaseModel):
    challenge_token: str
    code: str = Field(min_length=6, max_length=20)


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class UserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    user_type: UserType
    status: UserStatus
    avatar_url: str | None = None
    job_title: str | None = None
    department: str | None = None
    mfa_enabled: bool = False
    last_login_at: datetime | None = None


class TokenBundle(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    refresh_expires_in: int
    session_id: uuid.UUID


class LoginResponse(BaseModel):
    status: Literal["authenticated", "mfa_required", "mfa_enrollment_required"]
    tokens: TokenBundle | None = None
    challenge_token: str | None = None
    recovery_codes: list[str] | None = None
    user: UserSummary | None = None


class SessionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    ip_address: str | None
    user_agent: str | None
    location: str | None
    mfa_verified: bool
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    current: bool = False


class AuthEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    event_type: str
    outcome: str
    ip_address: str | None
    user_agent: str | None
    created_at: datetime
    event_metadata: dict[str, Any]


class IdentityScope(BaseModel):
    scope_type: str
    scope_id: uuid.UUID | None
    scope_name: str
    role_keys: list[str]
    permissions: list[str]


class MeResponse(BaseModel):
    user: UserSummary
    scopes: list[IdentityScope]
    platform_permissions: list[str]
    memberships: list[dict[str, Any]]


class MfaEnrollmentResponse(BaseModel):
    secret: str
    provisioning_uri: str
    recovery_codes: list[str] | None = None


class MfaConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class MfaEnrollRequest(BaseModel):
    current_password: str | None = Field(default=None, min_length=1, max_length=256)
    code: str | None = Field(default=None, min_length=6, max_length=20)


class MfaSetupStartRequest(BaseModel):
    challenge_token: str = Field(min_length=16, max_length=4096)


class MfaSetupConfirmRequest(BaseModel):
    challenge_token: str = Field(min_length=16, max_length=4096)
    code: str = Field(min_length=6, max_length=6)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class PasswordConfirmRequest(BaseModel):
    """Re-authentication proof for actions that only need the current password."""

    current_password: str = Field(min_length=1, max_length=256)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=12, max_length=256)


class InviteAcceptRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)
    new_password: str = Field(min_length=12, max_length=256)
    full_name: str | None = Field(default=None, min_length=2, max_length=200)


class UserCreateRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=12, max_length=256)
    user_type: UserType = UserType.INTERNAL
    job_title: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    role_keys: list[str] = Field(default_factory=list)
    organization_id: uuid.UUID | None = None

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class UserUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    status: UserStatus | None = None
    job_title: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    user_type: UserType | None = None


class RoleAssignmentRequest(BaseModel):
    role_key: str
    scope_type: str
    scope_id: uuid.UUID | None = None


class RoleOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str | None
    scope_type: str
    is_system: bool
    permissions: list[str]


class UserDetail(UserSummary):
    created_at: datetime
    email_verified_at: datetime | None
    failed_login_count: int
    locked_until: datetime | None
    roles: list[dict[str, Any]] = Field(default_factory=list)
