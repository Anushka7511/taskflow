import uuid

from fastapi import APIRouter, Response

from app.core.dependencies import CurrentUser, MembershipServiceDep, ProjectMember
from app.routers.responses import errors
from app.schemas.membership import MemberInvite, MemberRead, MemberRoleUpdate

router = APIRouter(prefix="/projects/{project_id}/members", tags=["Memberships"])


@router.post(
    "",
    response_model=MemberRead,
    status_code=201,
    summary="Invite a registered user to the project",
    responses=errors(401, 403, 404, 409, 422),
)
def add_member(project_id: uuid.UUID, data: MemberInvite, user: CurrentUser, service: MembershipServiceDep):
    """**Auth:** OWNER (may grant any role) or ADMIN (may only add role MEMBER).

    Errors: 403 role escalation attempt, 404 unknown project / unregistered e-mail, 409 already a member.
    """
    return service.add_member(project_id, user, data)


@router.get(
    "",
    response_model=list[MemberRead],
    summary="List project members",
    responses=errors(401, 404, 422),
)
def list_members(project_id: uuid.UUID, membership: ProjectMember, service: MembershipServiceDep):
    """**Auth:** any project member."""
    return service.list_members(membership)


@router.patch(
    "/{user_id}",
    response_model=MemberRead,
    summary="Change a member's role",
    responses=errors(401, 403, 404, 409, 422),
)
def change_role(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    data: MemberRoleUpdate,
    user: CurrentUser,
    service: MembershipServiceDep,
):
    """**Auth:** OWNER only. 409 if this would demote the last owner."""
    return service.change_role(project_id, user, user_id, data)


@router.delete(
    "/{user_id}",
    status_code=204,
    summary="Remove a member (or leave the project)",
    responses=errors(401, 403, 404, 409, 422),
)
def remove_member(project_id: uuid.UUID, user_id: uuid.UUID, user: CurrentUser, service: MembershipServiceDep):
    """OWNER can remove anyone; ADMIN can remove MEMBERs only; anyone can remove themselves.

    409 if the target is the last owner. The removed user's tasks become unassigned.
    """
    service.remove_member(project_id, user, user_id)
    return Response(status_code=204)
