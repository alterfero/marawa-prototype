from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette import status as http_status


class ApiErrorResponse(BaseModel):
    code: str
    message: str
    details: Any | None = None


STATUS_CODE_DEFAULTS = {
    http_status.HTTP_401_UNAUTHORIZED: "unauthorized",
    http_status.HTTP_403_FORBIDDEN: "forbidden",
    http_status.HTTP_400_BAD_REQUEST: "bad_request",
    http_status.HTTP_404_NOT_FOUND: "not_found",
    http_status.HTTP_409_CONFLICT: "conflict",
    http_status.HTTP_413_CONTENT_TOO_LARGE: "payload_too_large",
    http_status.HTTP_422_UNPROCESSABLE_CONTENT: "request_validation_error",
    http_status.HTTP_500_INTERNAL_SERVER_ERROR: "internal_server_error",
}


def api_error(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ApiErrorResponse(code=code, message=message, details=details).model_dump(exclude_none=True),
    )


def _default_message_for_status(status_code: int) -> str:
    if status_code == http_status.HTTP_401_UNAUTHORIZED:
        return "Authentication is required."
    if status_code == http_status.HTTP_403_FORBIDDEN:
        return "You do not have access to this resource."
    if status_code == http_status.HTTP_404_NOT_FOUND:
        return "The requested resource was not found."
    if status_code == http_status.HTTP_409_CONFLICT:
        return "The request could not be completed because the resource changed."
    if status_code == http_status.HTTP_413_CONTENT_TOO_LARGE:
        return "The uploaded file exceeds the configured size limit."
    if status_code == http_status.HTTP_422_UNPROCESSABLE_CONTENT:
        return "Request validation failed."
    if status_code >= 500:
        return "The server encountered an unexpected error."
    return "The request failed."


def _normalize_http_exception(status_code: int, detail: Any) -> dict[str, Any]:
    if isinstance(detail, Mapping):
        code = detail.get("code")
        message = detail.get("message")
        details = detail.get("details")

        if isinstance(code, str) and isinstance(message, str):
            return ApiErrorResponse(code=code, message=message, details=details).model_dump(exclude_none=True)

        if isinstance(message, str):
            return ApiErrorResponse(
                code=STATUS_CODE_DEFAULTS.get(status_code, "error"),
                message=message,
                details=details,
            ).model_dump(exclude_none=True)

        extras = {key: value for key, value in detail.items() if key not in {"code", "message", "details"}}
        if "detail" in detail and not extras:
            return ApiErrorResponse(
                code=STATUS_CODE_DEFAULTS.get(status_code, "error"),
                message=str(detail["detail"]),
            ).model_dump()

        return ApiErrorResponse(
            code=STATUS_CODE_DEFAULTS.get(status_code, "error"),
            message=_default_message_for_status(status_code),
            details={**({"details": details} if details is not None else {}), **extras} or None,
        ).model_dump(exclude_none=True)

    if isinstance(detail, list):
        return ApiErrorResponse(
            code=STATUS_CODE_DEFAULTS.get(status_code, "error"),
            message=_default_message_for_status(status_code),
            details={"errors": detail},
        ).model_dump(exclude_none=True)

    if isinstance(detail, str):
        return ApiErrorResponse(
            code=STATUS_CODE_DEFAULTS.get(status_code, "error"),
            message=detail,
        ).model_dump()

    return ApiErrorResponse(
        code=STATUS_CODE_DEFAULTS.get(status_code, "error"),
        message=_default_message_for_status(status_code),
    ).model_dump()


async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_normalize_http_exception(exc.status_code, exc.detail),
    )


async def request_validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=ApiErrorResponse(
            code="request_validation_error",
            message="Request validation failed.",
            details={"errors": exc.errors()},
        ).model_dump(exclude_none=True),
    )


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ApiErrorResponse(
            code="internal_server_error",
            message="The server encountered an unexpected error.",
        ).model_dump(),
    )
