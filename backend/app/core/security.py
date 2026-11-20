from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import get_settings

settings = get_settings()

# Argon2id with the OWASP-recommended baseline. Parameters are stored in the hash,
# so raising them later remains backward compatible.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256


class PasswordPolicyError(ValueError):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password_strength(password: str, *, min_length: int = PASSWORD_MIN_LENGTH) -> list[str]:
    problems: list[str] = []
    if len(password) < max(min_length, PASSWORD_MIN_LENGTH):
        problems.append(f"Password must be at least {max(min_length, PASSWORD_MIN_LENGTH)} characters.")
    if len(password) > PASSWORD_MAX_LENGTH:
        problems.append("Password is too long.")
    if password.lower() == password:
        problems.append("Password must contain an uppercase letter.")
    if not any(c.isdigit() for c in password):
        problems.append("Password must contain a digit.")
    if not any(not c.isalnum() for c in password):
        problems.append("Password must contain a symbol.")
    return problems


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_in: int
    refresh_expires_in: int
    session_id: uuid.UUID


class KeyRing:
    """RS256 signing keys with a stable `kid` and a JWKS representation."""

    def __init__(self, private_pem: str, public_pem: str, kid: str) -> None:
        self.private_pem = private_pem
        self.public_pem = public_pem
        self.kid = kid

    @classmethod
    def load(cls) -> KeyRing:
        keys_dir = settings.keys_path
        private_path = keys_dir / "jwt_private.pem"
        public_path = keys_dir / "jwt_public.pem"

        if settings.jwt_private_key and settings.jwt_public_key:
            private_pem = settings.jwt_private_key.replace("\\n", "\n")
            public_pem = settings.jwt_public_key.replace("\\n", "\n")
        elif private_path.exists() and public_path.exists():
            private_pem = private_path.read_text(encoding="utf-8")
            public_pem = public_path.read_text(encoding="utf-8")
        elif not settings.is_production:
            key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            private_pem = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode()
            public_pem = (
                key.public_key()
                .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
                .decode()
            )
            keys_dir.mkdir(parents=True, exist_ok=True)
            private_path.write_text(private_pem, encoding="utf-8")
            public_path.write_text(public_pem, encoding="utf-8")
            try:
                private_path.chmod(0o600)
            except OSError:
                pass
        else:  # pragma: no cover - guarded by settings validation
            raise RuntimeError("JWT signing keys are not configured")

        kid = hashlib.sha256(public_pem.encode()).hexdigest()[:16]
        return cls(private_pem, public_pem, kid)

    def jwks(self) -> dict[str, Any]:
        public_key = serialization.load_pem_public_key(self.public_pem.encode())
        numbers = public_key.public_numbers()  # type: ignore[attr-defined]
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": self.kid,
                    "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
                    "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
                }
            ]
        }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


_keyring: KeyRing | None = None


def keyring() -> KeyRing:
    global _keyring
    if _keyring is None:
        _keyring = KeyRing.load()
    return _keyring


def create_access_token(
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    email: str,
    user_type: str,
    extra: dict[str, Any] | None = None,
) -> tuple[str, int]:
    now = datetime.now(UTC)
    ttl = settings.access_token_ttl_minutes * 60
    claims: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "sid": str(session_id),
        "email": email,
        "typ": "user",
        "user_type": user_type,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    if extra:
        claims.update(extra)
    token = jwt.encode(claims, keyring().private_pem, algorithm="RS256", headers={"kid": keyring().kid})
    return token, ttl


def create_service_token(
    *, application_id: uuid.UUID, credential_id: uuid.UUID, scopes: list[str], ttl_seconds: int = 3600
) -> tuple[str, int]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": f"app:{application_id}",
        "app_id": str(application_id),
        "credential_id": str(credential_id),
        "scopes": scopes,
        "typ": "service",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    token = jwt.encode(claims, keyring().private_pem, algorithm="RS256", headers={"kid": keyring().kid})
    return token, ttl_seconds


def _app_claims(
    *,
    client_id: str,
    application_id: uuid.UUID,
    tenant_id: uuid.UUID,
    tenant_slug: str,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    email: str,
    name: str,
    roles: list[str],
    permissions: list[str],
    scopes: list[str],
    auth_time: datetime,
    amr: list[str],
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "iss": settings.jwt_issuer,
        "aud": client_id,
        "sub": str(user_id),
        "client_id": client_id,
        "app_id": str(application_id),
        "tenant_id": str(tenant_id),
        "tenant_slug": tenant_slug,
        "email": email,
        "name": name,
        "roles": roles,
        "permissions": permissions,
        "scope": " ".join(scopes),
        "sid": str(session_id),
        "auth_time": int(auth_time.timestamp()),
        "amr": amr,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "jti": secrets.token_urlsafe(12),
    }


def create_app_access_token(
    *,
    client_id: str,
    application_id: uuid.UUID,
    tenant_id: uuid.UUID,
    tenant_slug: str,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    email: str,
    name: str,
    roles: list[str],
    permissions: list[str],
    scopes: list[str],
    auth_time: datetime,
    amr: list[str],
) -> tuple[str, int]:
    """Issue an access token scoped to one application and tenant.

    The audience is the OAuth ``client_id``, never the console audience, so an
    app token can never be accepted by the admin API.
    """
    ttl = settings.app_access_token_ttl_minutes * 60
    claims = _app_claims(
        client_id=client_id,
        application_id=application_id,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        user_id=user_id,
        session_id=session_id,
        email=email,
        name=name,
        roles=roles,
        permissions=permissions,
        scopes=scopes,
        auth_time=auth_time,
        amr=amr,
    )
    claims["typ"] = "user_app"
    claims["exp"] = int((datetime.now(UTC) + timedelta(seconds=ttl)).timestamp())
    token = jwt.encode(claims, keyring().private_pem, algorithm="RS256", headers={"kid": keyring().kid})
    return token, ttl


def create_id_token(
    *,
    client_id: str,
    application_id: uuid.UUID,
    tenant_id: uuid.UUID,
    tenant_slug: str,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    email: str,
    name: str,
    roles: list[str],
    permissions: list[str],
    scopes: list[str],
    auth_time: datetime,
    amr: list[str],
    nonce: str | None = None,
    email_verified: bool = False,
) -> tuple[str, int]:
    ttl = settings.id_token_ttl_minutes * 60
    claims = _app_claims(
        client_id=client_id,
        application_id=application_id,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        user_id=user_id,
        session_id=session_id,
        email=email,
        name=name,
        roles=roles,
        permissions=permissions,
        scopes=scopes,
        auth_time=auth_time,
        amr=amr,
    )
    claims.update(
        {
            "typ": "id",
            "email_verified": email_verified,
            "preferred_username": email,
            "azp": client_id,
            "exp": int((datetime.now(UTC) + timedelta(seconds=ttl)).timestamp()),
        }
    )
    if nonce:
        claims["nonce"] = nonce
    token = jwt.encode(claims, keyring().private_pem, algorithm="RS256", headers={"kid": keyring().kid})
    return token, ttl


def decode_app_token(token: str, *, expected_audience: str | None = None) -> TokenClaims:
    """Verify an end-user app token signature, type and (optionally) audience.

    Audience verification is explicit: callers that know the client must pass it
    so a token issued for another application cannot be replayed here.
    """
    payload = jwt.decode(
        token,
        keyring().public_pem,
        algorithms=["RS256"],
        audience=expected_audience if expected_audience else None,
        issuer=settings.jwt_issuer,
        options={
            "require": ["exp", "iat", "iss", "aud", "sub"],
            "verify_aud": expected_audience is not None,
        },
    )
    if payload.get("typ") != "user_app":
        raise jwt.InvalidTokenError("expected user_app token")
    if expected_audience and payload.get("aud") != expected_audience:
        raise jwt.InvalidTokenError("token audience mismatch")
    return TokenClaims(
        subject=payload["sub"],
        session_id=payload.get("sid"),
        email=payload.get("email"),
        token_type="user_app",
        claims=payload,
    )


def create_mfa_challenge(user_id: uuid.UUID, *, mode: str = "verify") -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "typ": "mfa",
        "mode": mode,
        "exp": int((now + timedelta(minutes=settings.mfa_challenge_ttl_minutes)).timestamp()),
        "iat": int(now.timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    return jwt.encode(claims, keyring().private_pem, algorithm="RS256", headers={"kid": keyring().kid})


@dataclass(frozen=True)
class TokenClaims:
    subject: str
    session_id: str | None
    email: str | None
    token_type: str
    claims: dict[str, Any]


def decode_token(token: str, *, expected_type: str | None = None) -> TokenClaims:
    payload = jwt.decode(
        token,
        keyring().public_pem,
        algorithms=["RS256"],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
    )
    token_type = payload.get("typ")
    if expected_type and token_type != expected_type:
        raise jwt.InvalidTokenError(f"expected {expected_type} token")
    return TokenClaims(
        subject=payload["sub"],
        session_id=payload.get("sid"),
        email=payload.get("email"),
        token_type=token_type,
        claims=payload,
    )


class SecretBox:
    """Authenticated symmetric encryption for data that must be recoverable."""

    def __init__(self) -> None:
        key = settings.encryption_key
        if not key:
            key_path = settings.encryption_key_file or (settings.keys_path / "encryption.key")
            key_path = Path(key_path)
            key_path.parent.mkdir(parents=True, exist_ok=True)
            if key_path.exists():
                key = key_path.read_text(encoding="utf-8").strip()
            elif not settings.is_production:
                key = Fernet.generate_key().decode()
                key_path.write_text(key, encoding="utf-8")
                try:
                    key_path.chmod(0o600)
                except OSError:
                    pass
            else:  # pragma: no cover
                raise RuntimeError("ENCRYPTION_KEY is required")
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("encrypted value could not be decrypted") from exc


_secret_box: SecretBox | None = None


def secret_box() -> SecretBox:
    global _secret_box
    if _secret_box is None:
        _secret_box = SecretBox()
    return _secret_box


def mask_secret(value: str) -> str:
    return value[:4] + "•" * 12 if len(value) > 4 else "••••"
