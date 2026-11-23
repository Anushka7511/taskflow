import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.core.dependencies import CurrentUser, ProjectMember, ProjectServiceDep, require_project_role
from app.models.enums import ProjectRole
from app.models.membership import ProjectMembership
from app.routers.responses import errors
from app.schemas.common import Page
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("", response_model=ProjectRead, status_code=201, summary="Create a project", responses=errors(401, 422))
def create_project(data: ProjectCreate, user: CurrentUser, service: ProjectServiceDep):
    """Create a project. The creator automatically becomes its **OWNER**.

    - **Auth:** Bearer token
    """
    return service.create(user, data)


@router.get("", response_model=Page[ProjectRead], summary="List my projects", responses=errors(401, 422))
def list_projects(
    user: CurrentUser,
    service: ProjectServiceDep,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Paginated list of the projects the caller is a member of (any role). **Auth:** Bearer token."""
    return service.list_for_user(user, page, limit)


@router.get("/{project_id}", response_model=ProjectRead, summary="Get a project", responses=errors(401, 404, 422))
def get_project(project_id: uuid.UUID, membership: ProjectMember, service: ProjectServiceDep):
    """**Auth:** project member (any role). Non-members receive 404."""
    return service.get(project_id, membership)


@router.patch(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Update project name/description",
    responses=errors(401, 403, 404, 422),
)
def update_project(
    project_id: uuid.UUID,
    data: ProjectUpdate,
    membership: Annotated[
        ProjectMembership, Depends(require_project_role(ProjectRole.OWNER, ProjectRole.ADMIN))
    ],
    service: ProjectServiceDep,
):
    """**Auth:** OWNER or ADMIN. Only the fields sent are changed."""
    return service.update(project_id, membership, data)


@router.delete(
    "/{project_id}",
    status_code=204,
    summary="Delete a project",
    responses=errors(401, 403, 404),
)
def delete_project(
    project_id: uuid.UUID,
    membership: Annotated[ProjectMembership, Depends(require_project_role(ProjectRole.OWNER))],
    service: ProjectServiceDep,
):
    """**Auth:** OWNER only. Permanently deletes the project, its memberships and all its tasks."""
    service.delete(project_id, membership)
    return Response(status_code=204)
