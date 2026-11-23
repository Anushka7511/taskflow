import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.permissions import Permission
from app.database.session import unit_of_work
from app.models.enums import ProjectRole
from app.models.membership import ProjectMembership
from app.models.project import Project
from app.models.user import User
from app.repositories.membership_repository import MembershipRepository
from app.repositories.project_repository import ProjectRepository
from app.schemas.common import Page
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from app.services.access import require_permission


class ProjectService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.projects = ProjectRepository(db)
        self.memberships = MembershipRepository(db)

    def create(self, owner: User, data: ProjectCreate) -> Project:
        """Atomic: the project and its OWNER membership are stored together or not at all."""
        project = Project(name=data.name, description=data.description, owner_id=owner.id)
        with unit_of_work(self.db):
            self.projects.add(project)
            self.memberships.add(
                ProjectMembership(project_id=project.id, user_id=owner.id, role=ProjectRole.OWNER)
            )
        return project

    def list_for_user(self, user: User, page: int, limit: int) -> Page[ProjectRead]:
        items, total = self.projects.list_for_user(user.id, offset=(page - 1) * limit, limit=limit)
        return Page[ProjectRead].build([ProjectRead.model_validate(p) for p in items], total, page, limit)

    def _get(self, project_id: uuid.UUID) -> Project:
        project = self.projects.get(project_id)
        if project is None:  # only reachable if the project vanished after the membership check
            raise NotFoundError("Project not found")
        return project

    def get(self, project_id: uuid.UUID, membership: ProjectMembership) -> Project:
        require_permission(membership, Permission.VIEW_PROJECT)
        return self._get(project_id)

    def update(self, project_id: uuid.UUID, membership: ProjectMembership, data: ProjectUpdate) -> Project:
        require_permission(membership, Permission.UPDATE_PROJECT)
        with unit_of_work(self.db):
            project = self._get(project_id)
            for field, value in data.model_dump(exclude_unset=True).items():
                setattr(project, field, value)
            self.db.flush()
        return project

    def delete(self, project_id: uuid.UUID, membership: ProjectMembership) -> None:
        """Memberships and tasks are removed by ON DELETE CASCADE in the database."""
        require_permission(membership, Permission.DELETE_PROJECT)
        with unit_of_work(self.db):
            self.projects.delete(self._get(project_id))
