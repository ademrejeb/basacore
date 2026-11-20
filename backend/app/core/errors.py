from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        *,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class NotFound(AppError):
    def __init__(self, resource: str, identifier: str | None = None) -> None:
        super().__init__(
            "not_found",
            f"{resource} not found." if not identifier else f"{resource} '{identifier}' not found.",
            404,
        )


class Forbidden(AppError):
    def __init__(self, message: str = "You do not have permission to perform this action.") -> None:
        super().__init__("forbidden", message, 403)


class Unauthorized(AppError):
    def __init__(self, message: str = "Authentication required.") -> None:
        super().__init__("unauthorized", message, 401)


class Conflict(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("conflict", message, 409)


class ValidationFailed(AppError):
    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__("validation_failed", message, 422, details=details)


class QuotaExceeded(AppError):
    def __init__(self, metric: str, limit: int, used: float) -> None:
        super().__init__(
            "quota_exceeded",
            f"The {metric} quota has been exceeded.",
            402,
            details={"metric": metric, "limit": limit, "used": used},
        )


class OAuthError(Exception):
    """RFC 6749 token-endpoint error, returned in OAuth's own envelope."""

    def __init__(self, error: str, description: str, status_code: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code


def _body(code: str, message: str, request_id: str | None, details: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": {"code": code, "message": message, "request_id": request_id}}
    if details is not None:
        payload["error"]["details"] = details
    return payload


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code, exc.message, request_id, exc.details),
            headers={"X-Request-ID": request_id} if request_id else None,
        )

    @app.exception_handler(OAuthError)
    async def oauth_error_handler(request: Request, exc: OAuthError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "error_description": exc.description},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(p) for p in err["loc"] if p != "body"), "message": err["msg"]}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_body(
                "validation_failed",
                "The request payload is invalid.",
                getattr(request.state, "request_id", None),
                details,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(code, str(exc.detail), request_id),
        )
