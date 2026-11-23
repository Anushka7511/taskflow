import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import TaskPriority, TaskStatus


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        # Composite FK: a task may only be assigned to a user who is a member of the
        # SAME project. When the membership row disappears, only assigned_to is
        # nulled (PostgreSQL 15+ column-list form) - project_id is NOT NULL and must stay.
        # MATCH SIMPLE semantics: the check is skipped while assigned_to IS NULL.
        ForeignKeyConstraint(
            ["project_id", "assigned_to"],
            ["project_memberships.project_id", "project_memberships.user_id"],
            name="fk_tasks_assignee_is_project_member",
            ondelete="SET NULL (assigned_to)",
        ),
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        CheckConstraint("version >= 1", name="version_positive"),
        # Indexes chosen from the expected query patterns (see README / benchmark):
        Index("ix_tasks_project_id_status", "project_id", "status"),
        Index("ix_tasks_assigned_to", "assigned_to"),
        Index("ix_tasks_project_id_created_at", "project_id", "created_at"),
        Index("ix_tasks_project_id_due_date", "project_id", "due_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status"), nullable=False, default=TaskStatus.TODO,
        server_default=TaskStatus.TODO.name,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority, name="task_priority"), nullable=False, default=TaskPriority.MEDIUM,
        server_default=TaskPriority.MEDIUM.name,
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Optimistic concurrency control: SQLAlchemy adds "AND version = :loaded_version"
    # to every UPDATE/DELETE and bumps the counter. 0 rows matched -> StaleDataError.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __mapper_args__ = {"version_id_col": version, "eager_defaults": True}
