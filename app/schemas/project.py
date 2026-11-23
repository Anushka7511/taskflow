import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.common import Name200

Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]


class ProjectCreate(BaseModel):
    name: Name200
    description: Description | None = None

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"name": "Website Redesign", "description": "Q1 marketing site refresh"}]}
    )


class ProjectUpdate(BaseModel):
    """Partial update: only the fields that are sent are changed."""

    name: Name200 | None = None
    description: Description | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "ProjectUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class ProjectRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    owner_id: uuid.UUID = Field(description="User who created the project")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
