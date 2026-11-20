from __future__ import annotations

import ipaddress
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.errors import AppError

logger = structlog.get_logger("http")
settings = get_settings()

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

PAYLOAD_TOO_LARGE = b'{"error":{"code":"payload_too_large","message":"Request body is too large."}}'


class BodySizeLimitMiddleware:
    """Reject bodies larger than ``max_bytes``, including chunked uploads.

    ``Content-Length`` is only a hint: a client can omit it and stream an
    unbounded body. This wrapper counts the bytes actually received and, when
    the cap is crossed, answers 413 once and disconnects the application.
    """

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        received = 0
        rejected = False
        response_started = False

        async def guarded_send(message) -> None:
            nonlocal response_started, rejected
            if rejected:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        async def limited_receive():
            nonlocal received, rejected
            if rejected:
                # Once the response is sent, the application must only observe
                # a disconnect; never read more from the network.
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] != "http.request":
                return message
            received += len(message.get("body", b""))
            if received <= self.max_bytes:
                return message
            if not rejected and not response_started:
                rejected = True
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(PAYLOAD_TOO_LARGE)).encode()),
                            (b"connection", b"close"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": PAYLOAD_TOO_LARGE})
            return {"type": "http.disconnect"}

        await self.app(scope, limited_receive, guarded_send)


def _is_trusted_proxy(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(address in network for network in settings.trusted_proxy_networks)


def resolve_client_ip(request: Request) -> str:
    """Return the caller's address without trusting client-supplied headers.

    ``X-Forwarded-For`` is only read when the direct peer is a configured proxy,
    and the right-most untrusted hop is used. A browser hitting the API directly
    therefore cannot forge the IP recorded in audit logs or rate-limit keys.
    """
    peer = request.client.host if request.client else ""
    if not peer or not settings.trusted_proxy_networks or not _is_trusted_proxy(peer):
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    for hop in reversed(hops):
        if not _is_trusted_proxy(hop):
            return hop
    # Every hop is a known proxy: no client address is attributable, so keep the
    # peer rather than picking an arbitrary header value.
    return peer


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:20]}"
        request.state.request_id = request_id
        started = time.perf_counter()
        request.state.client_ip = resolve_client_ip(request)
        request.state.user_agent = request.headers.get("user-agent", "")[:500]

        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > settings.max_request_bytes:
            return Response(
                content='{"error":{"code":"payload_too_large","message":"Request body is too large."}}',
                status_code=413,
                media_type="application/json",
                headers={"X-Request-ID": request_id},
            )

        # Cookie-authenticated unsafe requests must originate from a trusted
        # origin. Bearer-authenticated machine clients are unaffected.
        if request.method not in SAFE_METHODS and settings.app_env != "test":
            origin = request.headers.get("origin")
            has_auth_cookie = bool(request.cookies.get("basa_access") or request.cookies.get("bsa_refresh"))
            if origin and origin not in settings.allowed_origins_list:
                return Response(
                    content='{"error":{"code":"invalid_origin","message":"Origin is not allowed."}}',
                    status_code=403,
                    media_type="application/json",
                    headers={"X-Request-ID": request_id},
                )
            if has_auth_cookie and not origin:
                body = (
                    '{"error":{"code":"invalid_origin","message":'
                    '"A trusted Origin header is required for cookie-authenticated requests."}}'
                )
                return Response(
                    content=body,
                    status_code=403,
                    media_type="application/json",
                    headers={"X-Request-ID": request_id},
                )

        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        logger.info(
            "request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response


def error_payload(exc: AppError, request_id: str | None) -> dict:
    return {"error": {"code": exc.code, "message": exc.message, "request_id": request_id}}
