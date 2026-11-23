import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.core.permissions import Permission, can_modify_task
from app.database.errors import violated_constraint
from app.database.session import unit_of_work
from app.models.enums import TaskStatus
from app.models.membership import ProjectMembership
from app.models.task import Task
from app.models.user import User
from app.repositories.membership_repository import MembershipRepository
from app.repositories.task_repository import TaskRepository
from app.schemas.common import Page
from app.schemas.task import TaskAssign, TaskCreate, TaskListQuery, TaskRead, TaskStatusUpdate, TaskUpdate
from app.services.access import AccessService, require_permission

ASSIGNEE_FK = "fk_tasks_assignee_is_project_member"

# Workflow: TODO <-> IN_PROGRESS -> DONE, and DONE can be re-opened to IN_PROGRESS.
# Jumping TODO -> DONE directly is not allowed: work must be started first.
ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.TODO: frozenset({TaskStatus.IN_PROGRESS}),
    TaskStatus.IN_PROGRESS: frozenset({TaskStatus.TODO, TaskStatus.DONE}),
    TaskStatus.DONE: frozenset({TaskStatus.IN_PROGRESS}),
}


class TaskService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tasks = TaskRepository(db)
        self.memberships = MembershipRepository(db)
        self.access = AccessService(self.memberships)

    # ---------------------------------------------------------------- helpers
    def _require_assignee_is_member(self, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
        if self.memberships.get(project_id, user_id) is None:
            raise BadRequestError("Assignee must be a member of the project")

    def _load_for_actor(self, task_id: uuid.UUID, actor: User) -> tuple[Task, ProjectMembership]:
        task = self.tasks.get(task_id)
        membership = self.memberships.get(task.project_id, actor.id) if task else None
        if task is None or membership is None:  # same answer for both: no existence leak
            raise NotFoundError("Task not found")
        return task, membership

    @staticmethod
    def _require_can_modify(task: Task, membership: ProjectMembership) -> None:
        allowed = can_modify_task(
            membership.role,
            is_creator=task.created_by == membership.user_id,
            is_assignee=task.assigned_to == membership.user_id,
        )
        if not allowed:
            raise ForbiddenError("Members can only modify tasks they created or are assigned to")

    @staticmethod
    def _check_version(task: Task, expected_version: int) -> None:
        """First line of defence: reject writes based on an outdated read."""
        if task.version != expected_version:
            raise ConflictError(
                "Task was modified by someone else. Reload it and retry.",
                details={"submitted_version": expected_version, "current_version": task.version},
            )

    def _flush_versioned(self) -> None:
        """Second line of defence: SQLAlchemy emits `UPDATE ... WHERE id=? AND version=?`.
        If another transaction committed between our SELECT and this UPDATE, zero rows
        match and StaleDataError is raised - the update is never silently lost."""
        try:
            self.db.flush()
        except StaleDataError as exc:
            raise ConflictError("Task was modified concurrently. Reload it and retry.") from exc
        except IntegrityError as exc:
            if violated_constraint(exc) == ASSIGNEE_FK:
                raise BadRequestError("Assignee must be a member of the project") from exc
            raise

    def _modify(self, task_id: uuid.UUID, actor: User, version: int, changes: dict[str, Any], *, validate=None) -> Task:
        with unit_of_work(self.db):
            task, membership = self._load_for_actor(task_id, actor)
            self._require_can_modify(task, membership)
            self._check_version(task, version)
            if validate is not None:
                validate(task)
            for field, value in changes.items():
                setattr(task, field, value)
            self._flush_versioned()
        return task

    # ------------------------------------------------------------- operations
    def create(self, project_id: uuid.UUID, membership: ProjectMembership, data: TaskCreate) -> Task:
        require_permission(membership, Permission.CREATE_TASK)
        if data.assigned_to is not None:
            self._require_assignee_is_member(project_id, data.assigned_to)
        task = Task(
            project_id=project_id,
            created_by=membership.user_id,
            title=data.title,
            description=data.description,
            priority=data.priority,
            due_date=data.due_date,
            assigned_to=data.assigned_to,
        )
        try:
            with unit_of_work(self.db):
                self.tasks.add(task)
        except IntegrityError as exc:
            if violated_constraint(exc) == ASSIGNEE_FK:  # member removed between check and insert
                raise BadRequestError("Assignee must be a member of the project") from exc
            raise
        return task

    def list_for_project(
        self, project_id: uuid.UUID, membership: ProjectMembership, query: TaskListQuery
    ) -> Page[TaskRead]:
        require_permission(membership, Permission.VIEW_TASKS)
        items, total = self.tasks.list_for_project(project_id, query)
        return Page[TaskRead].build([TaskRead.model_validate(t) for t in items], total, query.page, query.limit)

    def get(self, task_id: uuid.UUID, actor: User) -> Task:
        task, membership = self._load_for_actor(task_id, actor)
        require_permission(membership, Permission.VIEW_TASKS)
        return task

    def update(self, task_id: uuid.UUID, actor: User, data: TaskUpdate) -> Task:
        changes = data.model_dump(exclude_unset=True, exclude={"version"})
        return self._modify(task_id, actor, data.version, changes)

    def update_status(self, task_id: uuid.UUID, actor: User, data: TaskStatusUpdate) -> Task:
        def validate(task: Task) -> None:
            if data.status is task.status:
                raise BadRequestError(f"Task is already {task.status.value}")
            allowed = ALLOWED_TRANSITIONS[task.status]
            if data.status not in allowed:
                raise BadRequestError(
                    f"Invalid status transition {task.status.value} -> {data.status.value}",
                    details={"allowed": sorted(s.value for s in allowed)},
                )

        return self._modify(task_id, actor, data.version, {"status": data.status}, validate=validate)

    def assign(self, task_id: uuid.UUID, actor: User, data: TaskAssign) -> Task:
        def validate(task: Task) -> None:
            if data.assigned_to is not None:
                self._require_assignee_is_member(task.project_id, data.assigned_to)

        return self._modify(task_id, actor, data.version, {"assigned_to": data.assigned_to}, validate=validate)

    def delete(self, task_id: uuid.UUID, actor: User) -> None:
        with unit_of_work(self.db):
            task, membership = self._load_for_actor(task_id, actor)
            self._require_can_modify(task, membership)
            try:
                self.tasks.delete(task)
            except StaleDataError as exc:
                raise ConflictError("Task was modified concurrently. Reload it and retry.") from exc
