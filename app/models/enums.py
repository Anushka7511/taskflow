"""Enumerations shared by models, schemas and services.

Declaration order matters for TaskPriority: PostgreSQL sorts native enums by
declaration order, so ORDER BY priority gives LOW < MEDIUM < HIGH.
"""

import enum


class ProjectRole(str, enum.Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"


class TaskStatus(str, enum.Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class TaskPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
