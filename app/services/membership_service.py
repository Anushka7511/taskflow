import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.permissions import Permission, can_grant_role, can_remove_member
from app.database.errors import violated_constraint
from app.database.session import unit_of_work
from app.models.enums import ProjectRole
from app.models.membership import ProjectMembership
from app.models.user import User
from app.repositories.membership_repository import MembershipRepository
from app.repositories.project_repository import ProjectRepository
from app.repositories.user_repository import UserRepository
from app.schemas.membership import MemberInvite, MemberRoleUpdate
from app.services.access import AccessService, require_permission


class MembershipService:
    """Membership changes are serialised per project with SELECT ... FOR UPDATE on the
    project row. Without it, two owners demoting each other at the same moment could
    each see 'another owner exists' and leave the project with no owner at all.
    The actor's own membership is re-read AFTER the lock, so a concurrent demotion of
    the actor is honoured."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.projects = ProjectRepository(db)
        self.memberships = MembershipRepository(db)
        self.users = UserRepository(db)
        self.access = AccessService(self.memberships)

    def _lock_and_get_actor(self, project_id: uuid.UUID, actor: User) -> ProjectMembership:
        if self.projects.lock(project_id) is None:
            raise NotFoundError("Project not found")
        return self.access.require_member(project_id, actor.id)

    def list_members(self, membership: ProjectMembership) -> list[ProjectMembership]:
        require_permission(membership, Permission.VIEW_MEMBERS)
        return self.memberships.list_for_project(membership.project_id)

    def add_member(self, project_id: uuid.UUID, actor: User, data: MemberInvite) -> ProjectMembership:
        try:
            with unit_of_work(self.db):
                actor_membership = self._lock_and_get_actor(project_id, actor)
                require_permission(actor_membership, Permission.INVITE_MEMBER)
                if not can_grant_role(actor_membership.role, data.role):
                    raise ForbiddenError(f"{actor_membership.role.value}s can only invite members with role MEMBER")

                invitee = self.users.get_by_email(data.email)
                if invitee is None:
                    raise NotFoundError("No registered user with that email")
                if self.memberships.get(project_id, invitee.id) is not None:
                    raise ConflictError("User is already a member of this project")

                self.memberships.add(
                    ProjectMembership(project_id=project_id, user_id=invitee.id, role=data.role)
                )
                created = self.memberships.get_with_user(project_id, invitee.id)
        except IntegrityError as exc:
            if violated_constraint(exc) == "uq_project_memberships_project_user":
                raise ConflictError("User is already a member of this project") from exc
            raise
        assert created is not None
        return created

    def change_role(
        self, project_id: uuid.UUID, actor: User, target_user_id: uuid.UUID, data: MemberRoleUpdate
    ) -> ProjectMembership:
        with unit_of_work(self.db):
            actor_membership = self._lock_and_get_actor(project_id, actor)
            require_permission(actor_membership, Permission.CHANGE_MEMBER_ROLE)

            target = self.memberships.get_with_user(project_id, target_user_id)
            if target is None:
                raise NotFoundError("Member not found")
            if target.role == data.role:
                return target
            if target.role is ProjectRole.OWNER and self.memberships.count_with_role(project_id, ProjectRole.OWNER) <= 1:
                raise ConflictError("Cannot demote the last owner; promote another owner first")

            target.role = data.role
            self.db.flush()
        return target

    def remove_member(self, project_id: uuid.UUID, actor: User, target_user_id: uuid.UUID) -> None:
        """Removing a member also un-assigns their tasks: the composite FK on tasks is
        ON DELETE SET NULL (assigned_to), so the database does it atomically."""
        with unit_of_work(self.db):
            actor_membership = self._lock_and_get_actor(project_id, actor)
            target = self.memberships.get(project_id, target_user_id)
            if target is None:
                raise NotFoundError("Member not found")

            is_self = target.user_id == actor.id
            if not is_self:
                require_permission(actor_membership, Permission.REMOVE_MEMBER)
            if not can_remove_member(actor_membership.role, target.role, is_self):
                raise ForbiddenError(
                    f"{actor_membership.role.value}s cannot remove a member with role {target.role.value}"
                )
            if target.role is ProjectRole.OWNER and self.memberships.count_with_role(project_id, ProjectRole.OWNER) <= 1:
                raise ConflictError("Cannot remove the last owner; promote another owner first")

            self.memberships.delete(target)
