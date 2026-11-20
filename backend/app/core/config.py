from __future__ import annotations

import base64
import ipaddress
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every value is validated at process start."""

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:3002"
    cors_origins: str = "http://localhost:3002,http://localhost:3003"

    database_url: str = Field(
        default="postgresql+asyncpg://platform_app:change-me-app@localhost:5434/basa",
        json_schema_extra={"secret": True},
    )
    migration_database_url: str | None = None
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 10

    redis_url: str | None = None

    jwt_issuer: str = "http://localhost:8000"
    jwt_audience: str = "basacore"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    mfa_challenge_ttl_minutes: int = 5
    app_access_token_ttl_minutes: int = 15
    app_refresh_token_ttl_days: int = 30
    id_token_ttl_minutes: int = 15
    auth_code_ttl_seconds: int = 60
    keys_dir: Path = Path("keys")
    jwt_private_key: str | None = None
    jwt_public_key: str | None = None

    encryption_key: str | None = None
    encryption_key_file: Path | None = None

    cookie_secure: bool = False
    cookie_domain: str | None = None
    allowed_origins: str = "http://localhost:3002,http://localhost:3003"
    max_request_bytes: int = 2 * 1024 * 1024

    # Comma-separated CIDRs of reverse proxies whose X-Forwarded-For header is
    # trusted. Empty means the header is ignored and the socket peer is used, so
    # a client can never forge its own address for audit records or rate limits.
    trusted_proxy_cidrs: str = ""

    # Serve /docs, /redoc and /openapi.json in production. Off by default
    # because the schema maps the entire administrative surface.
    expose_api_docs: bool = False

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True

    seed_admin_email: str = "admin@basa.dev"
    seed_admin_password: str = "Adm1n!Basa#2026"
    seed_demo: bool = False
    seed_reference: bool = True

    @field_validator("encryption_key")
    @classmethod
    def validate_fernet_key(cls, value: str | None) -> str | None:
        if value:
            try:
                raw = base64.urlsafe_b64decode(value.encode())
            except Exception as exc:  # noqa: BLE001
                raise ValueError("ENCRYPTION_KEY must be urlsafe base64") from exc
            if len(raw) != 32:
                raise ValueError("ENCRYPTION_KEY must decode to 32 bytes")
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env in ("production", "staging")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def trusted_proxy_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return _parse_cidrs(self.trusted_proxy_cidrs)

    @property
    def keys_path(self) -> Path:
        return Path(self.keys_dir)


@lru_cache
def _parse_cidrs(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks = []
    for part in raw.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError as exc:
            raise ValueError(f"TRUSTED_PROXY_CIDRS contains an invalid CIDR: {candidate}") from exc
    return tuple(networks)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.is_production:
        missing = []
        if not settings.encryption_key:
            missing.append("ENCRYPTION_KEY")
        if not settings.jwt_private_key and not (settings.keys_path / "jwt_private.pem").exists():
            missing.append("JWT private key (JWT_PRIVATE_KEY or keys/jwt_private.pem)")
        if not settings.smtp_host:
            missing.append("SMTP_HOST")
        if settings.cookie_secure is False:
            raise RuntimeError("COOKIE_SECURE must be true in production")
        if settings.seed_demo:
            raise RuntimeError("SEED_DEMO must be false outside local development")
        if missing:
            raise RuntimeError(f"Missing required production configuration: {', '.join(missing)}")
        if settings.seed_admin_password == "Adm1n!Basa#2026":
            raise RuntimeError("SEED_ADMIN_PASSWORD must be changed outside local development")
        if "change-me" in settings.database_url:
            raise RuntimeError("DATABASE_URL still contains the development password")
        for name in ("api_base_url", "web_base_url", "jwt_issuer"):
            if not getattr(settings, name).startswith("https://"):
                raise RuntimeError(f"{name.upper()} must use HTTPS in production")
        for origin in settings.cors_origins_list + settings.allowed_origins_list:
            if not origin.startswith("https://"):
                raise RuntimeError("CORS_ORIGINS and ALLOWED_ORIGINS must use HTTPS in production")
        if not settings.allowed_origins_list:
            raise RuntimeError("ALLOWED_ORIGINS must not be empty in production")
    return settings


def load_or_create_dev_secret(path: Path, factory) -> str:
    """Persist local development key material outside source control."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = factory()
    path.write_text(value, encoding="utf-8")
    if not secrets.compare_digest(value, value):  # pragma: no cover - defensive
        raise RuntimeError("unreachable")
    return value
