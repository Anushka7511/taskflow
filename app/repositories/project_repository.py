import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.membership import ProjectMembership
from app.models.project import Project


class ProjectRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, project: Project) -> Project:
        self.db.add(project)
        self.db.flush()
        return project

    def get(self, project_id: uuid.UUID) -> Project | None:
        return self.db.get(Project, project_id)

    def lock(self, project_id: uuid.UUID) -> Project | None:
        """SELECT ... FOR UPDATE on the project row. Membership mutations take this
        lock so that "is this the last owner?" checks cannot race each other."""
        return self.db.scalar(select(Project).where(Project.id == project_id).with_for_update())

    def list_for_user(self, user_id: uuid.UUID, *, offset: int, limit: int) -> tuple[list[Project], int]:
        member_of = ProjectMembership.user_id == user_id
        total = self.db.scalar(
            select(func.count()).select_from(ProjectMembership).where(member_of)
        ) or 0
        stmt = (
            select(Project)
            .join(ProjectMembership, ProjectMembership.project_id == Project.id)
            .where(member_of)
            .order_by(Project.created_at.desc(), Project.id)
            .offset(offset)
            .limit(limit)
        )
        return list(self.db.scalars(stmt)), total

    def delete(self, project: Project) -> None:
        self.db.delete(project)
        self.db.flush()
