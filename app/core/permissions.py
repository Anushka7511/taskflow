"""Role-based access control policy.

Pure functions - no database access - so the rules are easy to read and to unit test.
Services call these; they are the authoritative enforcement point.

    Capability                   OWNER  ADMIN  MEMBER
    view project / members / tasks  Y      Y      Y
    update project settings         Y      Y      -
    delete project                  Y      -      -
    invite members                  Y      Y(*)   -
    change member roles             Y      -      -
    remove members                  Y      Y(**)  - (may leave)
    create tasks                    Y      Y      Y
    modify/delete any task          Y      Y      -  (only own / assigned)

    (*)  ADMIN may only invite plain MEMBERs.
    (**) ADMIN may only remove plain MEMBERs.
"""

import enum

from app.models.enums import ProjectRole


class Permission(str, enum.Enum):
    VIEW_PROJECT = "view_project"
    UPDATE_PROJECT = "update_project"
    DELETE_PROJECT = "delete_project"
    VIEW_MEMBERS = "view_members"
    INVITE_MEMBER = "invite_member"
    CHANGE_MEMBER_ROLE = "change_member_role"
    REMOVE_MEMBER = "remove_member"
    VIEW_TASKS = "view_tasks"
    CREATE_TASK = "create_task"
    MANAGE_ANY_TASK = "manage_any_task"


_ALL = frozenset(Permission)
_ADMIN = frozenset(
    {
        Permission.VIEW_PROJECT,
        Permission.UPDATE_PROJECT,
        Permission.VIEW_MEMBERS,
        Permission.INVITE_MEMBER,
        Permission.REMOVE_MEMBER,
        Permission.VIEW_TASKS,
        Permission.CREATE_TASK,
        Permission.MANAGE_ANY_TASK,
    }
)
_MEMBER = frozenset(
    {
        Permission.VIEW_PROJECT,
        Permission.VIEW_MEMBERS,
        Permission.VIEW_TASKS,
        Permission.CREATE_TASK,
    }
)

ROLE_PERMISSIONS: dict[ProjectRole, frozenset[Permission]] = {
    ProjectRole.OWNER: _ALL,
    ProjectRole.ADMIN: _ADMIN,
    ProjectRole.MEMBER: _MEMBER,
}


def has_permission(role: ProjectRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def can_grant_role(actor_role: ProjectRole, new_role: ProjectRole) -> bool:
    """Prevents privilege escalation when inviting: owners grant anything,
    admins may only create plain members."""
    if actor_role is ProjectRole.OWNER:
        return True
    if actor_role is ProjectRole.ADMIN:
        return new_role is ProjectRole.MEMBER
    return False


def can_remove_member(actor_role: ProjectRole, target_role: ProjectRole, is_self: bool) -> bool:
    if is_self:  # anybody may leave a project (last-owner rule is checked separately)
        return True
    if actor_role is ProjectRole.OWNER:
        return True
    if actor_role is ProjectRole.ADMIN:
        return target_role is ProjectRole.MEMBER
    return False


def can_modify_task(role: ProjectRole, *, is_creator: bool, is_assignee: bool) -> bool:
    if has_permission(role, Permission.MANAGE_ANY_TASK):
        return True
    return is_creator or is_assignee
