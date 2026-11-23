import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.enums import TaskPriority, TaskStatus
from app.schemas.common import Name200

TaskDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=10000)]
Version = Annotated[int, Field(ge=1, description="Task version you last read (optimistic locking)")]


class TaskCreate(BaseModel):
    title: Name200
    description: TaskDescription | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: date | None = None
    assigned_to: uuid.UUID | None = Field(default=None, description="Must be a member of the project")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "Design landing page hero",
                    "description": "Three variants for A/B testing",
                    "priority": "HIGH",
                    "due_date": "2026-12-01",
                    "assigned_to": None,
                }
            ]
        }
    )


class TaskUpdate(BaseModel):
    """Partial update guarded by the version the client last read.
    Status and assignee have dedicated endpoints."""

    version: Version
    title: Name200 | None = None
    description: TaskDescription | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None

    @model_validator(mode="after")
    def _validate(self) -> "TaskUpdate":
        changes = self.model_fields_set - {"version"}
        if not changes:
            raise ValueError("At least one field to update must be provided")
        if "title" in changes and self.title is None:
            raise ValueError("title cannot be null")
        if "priority" in changes and self.priority is None:
            raise ValueError("priority cannot be null")
        return self

    model_config = ConfigDict(json_schema_extra={"examples": [{"version": 1, "title": "New title", "priority": "LOW"}]})


class TaskAssign(BaseModel):
    assigned_to: uuid.UUID | None = Field(description="User id, or null to unassign")
    version: Version

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"assigned_to": "3f2b7c1e-8a41-4c55-9d0e-6a1b2c3d4e5f", "version": 2}]}
    )


class TaskStatusUpdate(BaseModel):
    status: TaskStatus
    version: Version

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "IN_PROGRESS", "version": 1}]})


class TaskRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    assigned_to: uuid.UUID | None
    created_by: uuid.UUID | None
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    due_date: date | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskListQuery(BaseModel):
    """Validated query string for GET /projects/{id}/tasks.

    Unknown parameters are rejected (422) so typos do not silently disable a filter."""

    model_config = ConfigDict(extra="forbid")

    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    assigned_to: uuid.UUID | None = None
    due_before: date | None = Field(default=None, description="due_date <= this date")
    due_after: date | None = Field(default=None, description="due_date >= this date")
    search: str | None = Field(default=None, min_length=1, max_length=100, description="Case-insensitive title search")
    sort_by: Literal["created_at", "updated_at", "due_date", "priority"] = "created_at"
    order: Literal["asc", "desc"] = "desc"
    page: int = Field(default=1, ge=1, le=100_000)
    limit: int = Field(default=20, ge=1, le=100)
