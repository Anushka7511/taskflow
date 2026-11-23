"""FastAPI dependencies: DB session, authenticated user, services and RBAC gates."""

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.database.session import get_db
from app.models.enums import ProjectRole
from app.models.membership import ProjectMembership
from app.models.user import User
from app.repositories.membership_repository import MembershipRepository
from app.services.access import AccessService
from app.services.auth_service import AuthService
from app.services.membership_service import MembershipService
from app.services.project_service import ProjectService
from app.services.task_service import TaskService

DbSession = Annotated[Session, Depends(get_db)]

# auto_error=False so a missing header yields OUR 401 JSON envelope (not FastAPI's default).
bearer_scheme = HTTPBearer(auto_error=False, description="JWT from POST /auth/login")


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User:
    if credentials is None:
        raise UnauthorizedError("Authentication required")
    return AuthService(db).user_from_token(credentials.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_project_membership(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ProjectMembership:
    """Resolves the caller's membership in the project from the path.
    404 if the caller is not a member (project existence is not leaked)."""
    return AccessService(MembershipRepository(db)).require_member(project_id, user.id)


ProjectMember = Annotated[ProjectMembership, Depends(get_project_membership)]


def require_project_role(*roles: ProjectRole) -> Callable[..., ProjectMembership]:
    """Dependency factory for declarative route-level RBAC:
        membership: Annotated[ProjectMembership, Depends(require_project_role(ProjectRole.OWNER))]
    Services re-check permissions, so this is an early gate, not the only one."""

    def dependency(membership: ProjectMember) -> ProjectMembership:
        if membership.role not in roles:
            allowed = ", ".join(r.value for r in roles)
            raise ForbiddenError(f"Requires one of these roles: {allowed}")
        return membership

    return dependency


def get_auth_service(db: DbSession) -> AuthService:
    return AuthService(db)


def get_project_service(db: DbSession) -> ProjectService:
    return ProjectService(db)


def get_membership_service(db: DbSession) -> MembershipService:
    return MembershipService(db)


def get_task_service(db: DbSession) -> TaskService:
    return TaskService(db)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]
MembershipServiceDep = Annotated[MembershipService, Depends(get_membership_service)]
TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]
