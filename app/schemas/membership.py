import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ProjectRole
from app.schemas.user import Email


class MemberInvite(BaseModel):
    email: Email
    role: ProjectRole = ProjectRole.MEMBER

    model_config = ConfigDict(json_schema_extra={"examples": [{"email": "rahul@example.com", "role": "MEMBER"}]})


class MemberRoleUpdate(BaseModel):
    role: ProjectRole

    model_config = ConfigDict(json_schema_extra={"examples": [{"role": "ADMIN"}]})


class MemberUser(BaseModel):
    id: uuid.UUID
    name: str
    email: str

    model_config = ConfigDict(from_attributes=True)


class MemberRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    role: ProjectRole
    joined_at: datetime
    user: MemberUser

    model_config = ConfigDict(from_attributes=True)
