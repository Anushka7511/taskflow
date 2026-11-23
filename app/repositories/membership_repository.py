import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.enums import ProjectRole
from app.models.membership import ProjectMembership


class MembershipRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, membership: ProjectMembership) -> ProjectMembership:
        self.db.add(membership)
        self.db.flush()
        return membership

    def get(self, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectMembership | None:
        stmt = select(ProjectMembership).where(
            ProjectMembership.project_id == project_id, ProjectMembership.user_id == user_id
        )
        return self.db.scalar(stmt)

    def get_with_user(self, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectMembership | None:
        stmt = (
            select(ProjectMembership)
            .options(joinedload(ProjectMembership.user))
            .where(ProjectMembership.project_id == project_id, ProjectMembership.user_id == user_id)
        )
        return self.db.scalar(stmt)

    def list_for_project(self, project_id: uuid.UUID) -> list[ProjectMembership]:
        stmt = (
            select(ProjectMembership)
            .options(joinedload(ProjectMembership.user))
            .where(ProjectMembership.project_id == project_id)
            .order_by(ProjectMembership.joined_at, ProjectMembership.id)
        )
        return list(self.db.scalars(stmt))

    def count_with_role(self, project_id: uuid.UUID, role: ProjectRole) -> int:
        stmt = select(func.count()).select_from(ProjectMembership).where(
            ProjectMembership.project_id == project_id, ProjectMembership.role == role
        )
        return self.db.scalar(stmt) or 0

    def delete(self, membership: ProjectMembership) -> None:
        self.db.delete(membership)
        self.db.flush()
