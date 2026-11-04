from __future__ import annotations

from typing import Any, Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .business import BusinessError


def business_error(
    error_code: str,
    message: str,
    retry_after_seconds: Optional[int] = None,
    extra: Optional[dict[str, Any]] = None,
) -> JSONResponse:
    payload: dict[str, Any] = {
        "ok": False,
        "error_code": error_code,
        "message": message,
    }
    if retry_after_seconds is not None:
        payload["retry_after_seconds"] = int(max(0, retry_after_seconds))
    if extra:
        payload.update(extra)
    return JSONResponse(status_code=200, content=payload)


async def business_error_handler(_: Request, exc: BusinessError) -> JSONResponse:
    return business_error(
        error_code=exc.error_code,
        message=exc.message,
        retry_after_seconds=exc.retry_after_seconds,
    )


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code_by_status = {
        400: "BAD_REQUEST",
        401: "AUTH_REQUIRED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        429: "RATE_LIMITED",
    }
    detail = exc.detail if isinstance(exc.detail, str) else "请求处理失败，请稍后重试"
    return business_error(
        error_code=code_by_status.get(int(exc.status_code), "SERVER_ERROR"),
        message=detail,
    )


async def request_validation_exception_handler(_: Request, __: RequestValidationError) -> JSONResponse:
    return business_error(
        error_code="VALIDATION_ERROR",
        message="请求参数格式不正确，请检查后重试",
    )
