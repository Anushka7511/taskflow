import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.enums import ProjectRole
from app.models.user import User


class ProjectMembership(Base):
    """Association table of the many-to-many relation users <-> projects, carrying the role."""

    __tablename__ = "project_memberships"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        # No duplicate memberships. It also serves as the target of the composite
        # foreign key from tasks (project_id, assigned_to) and as the lookup index
        # for "is user X a member of project Y".
        UniqueConstraint("project_id", "user_id", name="uq_project_memberships_project_user"),
        Index("ix_project_memberships_user_id", "user_id"),  # "projects of user X"
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[ProjectRole] = mapped_column(Enum(ProjectRole, name="project_role"), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(lazy="raise")
