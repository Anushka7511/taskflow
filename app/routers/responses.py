"""Reusable OpenAPI error-response declarations."""

from typing import Any

from app.schemas.common import ErrorResponse

_DESCRIPTIONS = {
    400: "Bad request - the request is well-formed but violates a business rule",
    401: "Unauthorized - missing, invalid or expired token",
    403: "Forbidden - your project role does not allow this action",
    404: "Not found - resource does not exist or you are not a member of its project",
    409: "Conflict - duplicate resource, last-owner rule, or stale task version",
    422: "Validation error - body, path or query parameters are invalid",
}


def errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse, "description": _DESCRIPTIONS[code]} for code in codes}
