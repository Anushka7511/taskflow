"""Membership lookups shared by the services (the 'must belong to the project' rule)."""

import uuid

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import Permission, has_permission
from app.models.membership import ProjectMembership
from app.repositories.membership_repository import MembershipRepository


class AccessService:
    def __init__(self, memberships: MembershipRepository) -> None:
        self.memberships = memberships

    def require_member(self, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectMembership:
        """Non-members get 404, not 403, so project existence is not leaked."""
        membership = self.memberships.get(project_id, user_id)
        if membership is None:
            raise NotFoundError("Project not found")
        return membership


def require_permission(membership: ProjectMembership, permission: Permission) -> None:
    if not has_permission(membership.role, permission):
        raise ForbiddenError(f"Your role ({membership.role.value}) is not allowed to do this")
