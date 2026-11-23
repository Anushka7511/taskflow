import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.schemas.common import Name100


def _normalise_email(value: str) -> str:
    return value.strip().lower()


Email = Annotated[EmailStr, AfterValidator(_normalise_email)]


class UserCreate(BaseModel):
    name: Name100
    email: Email
    password: str = Field(min_length=8, max_length=128, description="8-128 characters")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"name": "Priya Sharma", "email": "priya@example.com", "password": "S3cure-passw0rd"}]
        }
    )


class UserRead(BaseModel):
    """Public representation of a user. Never includes the password hash."""

    id: uuid.UUID
    name: str
    email: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=128)

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"email": "priya@example.com", "password": "S3cure-passw0rd"}]}
    )


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds")
