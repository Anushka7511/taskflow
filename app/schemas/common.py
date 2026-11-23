import math
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, Field, StringConstraints

T = TypeVar("T")

# Trimmed, non-empty string helpers reused by several schemas.
Name100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Name200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class Page(BaseModel, Generic[T]):
    """Paginated collection envelope."""

    items: list[T]
    total: int = Field(description="Total number of records matching the filters")
    page: int = Field(description="Current page (1-based)")
    limit: int = Field(description="Page size requested")
    pages: int = Field(description="Total number of pages (0 when there are no records)")

    @classmethod
    def build(cls, items: list[T], total: int, page: int, limit: int) -> "Page[T]":
        return cls(items=items, total=total, page=page, limit=limit, pages=math.ceil(total / limit))


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    """Uniform error envelope returned for every non-2xx response."""

    error: ErrorBody

    model_config = {
        "json_schema_extra": {
            "examples": [{"error": {"code": "not_found", "message": "Task not found", "details": None}}]
        }
    }
