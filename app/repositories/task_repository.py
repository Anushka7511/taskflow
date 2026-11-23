import uuid

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models.task import Task
from app.schemas.task import TaskListQuery

# Explicit allow-list: user input never becomes part of the ORDER BY clause directly.
SORT_COLUMNS = {
    "created_at": Task.created_at,
    "updated_at": Task.updated_at,
    "due_date": Task.due_date,
    "priority": Task.priority,  # native enum: sorts LOW < MEDIUM < HIGH
}


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so a search for '100%' matches the literal text."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class TaskRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, task: Task) -> Task:
        self.db.add(task)
        self.db.flush()
        return task

    def get(self, task_id: uuid.UUID) -> Task | None:
        return self.db.get(Task, task_id)

    def delete(self, task: Task) -> None:
        self.db.delete(task)
        self.db.flush()

    @staticmethod
    def _conditions(project_id: uuid.UUID, q: TaskListQuery) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = [Task.project_id == project_id]
        if q.status is not None:
            conditions.append(Task.status == q.status)
        if q.priority is not None:
            conditions.append(Task.priority == q.priority)
        if q.assigned_to is not None:
            conditions.append(Task.assigned_to == q.assigned_to)
        if q.due_before is not None:
            conditions.append(Task.due_date <= q.due_before)
        if q.due_after is not None:
            conditions.append(Task.due_date >= q.due_after)
        if q.search:
            conditions.append(Task.title.ilike(f"%{_escape_like(q.search)}%", escape="\\"))
        return conditions

    def list_for_project(self, project_id: uuid.UUID, q: TaskListQuery) -> tuple[list[Task], int]:
        """Filtering, sorting and pagination all happen in SQL (bound parameters only)."""
        conditions = self._conditions(project_id, q)
        total = self.db.scalar(select(func.count()).select_from(Task).where(*conditions)) or 0

        column = SORT_COLUMNS[q.sort_by]
        ordering = column.asc() if q.order == "asc" else column.desc()
        if q.sort_by == "due_date":
            ordering = ordering.nulls_last()  # tasks without a due date always come last
        stmt = (
            select(Task)
            .where(*conditions)
            .order_by(ordering, Task.id)  # id tie-breaker => stable pages, no duplicates/gaps
            .offset((q.page - 1) * q.limit)
            .limit(q.limit)
        )
        return list(self.db.scalars(stmt)), total
