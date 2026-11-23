import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.core.dependencies import CurrentUser, ProjectMember, TaskServiceDep
from app.routers.responses import errors
from app.schemas.common import Page
from app.schemas.task import (
    TaskAssign,
    TaskCreate,
    TaskListQuery,
    TaskRead,
    TaskStatusUpdate,
    TaskUpdate,
)

router = APIRouter(tags=["Tasks"])


@router.post(
    "/projects/{project_id}/tasks",
    response_model=TaskRead,
    status_code=201,
    summary="Create a task",
    responses=errors(400, 401, 404, 422),
)
def create_task(project_id: uuid.UUID, data: TaskCreate, membership: ProjectMember, service: TaskServiceDep):
    """**Auth:** any project member. `assigned_to` (optional) must be a member of the project (else 400)."""
    return service.create(project_id, membership, data)


@router.get(
    "/projects/{project_id}/tasks",
    response_model=Page[TaskRead],
    summary="List tasks (filter, search, sort, paginate)",
    responses=errors(401, 404, 422),
)
def list_tasks(
    project_id: uuid.UUID,
    query: Annotated[TaskListQuery, Query()],
    membership: ProjectMember,
    service: TaskServiceDep,
):
    """**Auth:** any project member.

    Filters: `status`, `priority`, `assigned_to`, `due_before`, `due_after`, `search` (title).
    Sorting: `sort_by` in created_at | updated_at | due_date | priority, `order` asc | desc.
    Paging: `page` (>=1), `limit` (1-100, default 20). Unknown parameters are rejected with 422.
    """
    return service.list_for_project(project_id, membership, query)


@router.get("/tasks/{task_id}", response_model=TaskRead, summary="Get a task", responses=errors(401, 404, 422))
def get_task(task_id: uuid.UUID, user: CurrentUser, service: TaskServiceDep):
    """**Auth:** member of the task's project. Note the returned `version`: send it back when modifying."""
    return service.get(task_id, user)


@router.patch(
    "/tasks/{task_id}",
    response_model=TaskRead,
    summary="Update task fields (optimistic locking)",
    responses=errors(401, 403, 404, 409, 422),
)
def update_task(task_id: uuid.UUID, data: TaskUpdate, user: CurrentUser, service: TaskServiceDep):
    """**Auth:** OWNER/ADMIN any task; MEMBER only tasks they created or are assigned to.

    The body must contain the `version` you last read. **409** if the task changed in the meantime.
    """
    return service.update(task_id, user, data)


@router.patch(
    "/tasks/{task_id}/assign",
    response_model=TaskRead,
    summary="Assign or unassign a task",
    responses=errors(400, 401, 403, 404, 409, 422),
)
def assign_task(task_id: uuid.UUID, data: TaskAssign, user: CurrentUser, service: TaskServiceDep):
    """Set `assigned_to` to a project member, or `null` to unassign. Same permissions and version rule as update."""
    return service.assign(task_id, user, data)


@router.patch(
    "/tasks/{task_id}/status",
    response_model=TaskRead,
    summary="Change task status (validated workflow)",
    responses=errors(400, 401, 403, 404, 409, 422),
)
def update_task_status(task_id: uuid.UUID, data: TaskStatusUpdate, user: CurrentUser, service: TaskServiceDep):
    """Allowed transitions: TODO->IN_PROGRESS, IN_PROGRESS->TODO, IN_PROGRESS->DONE, DONE->IN_PROGRESS.
    Anything else returns 400 listing the allowed targets."""
    return service.update_status(task_id, user, data)


@router.delete(
    "/tasks/{task_id}",
    status_code=204,
    summary="Delete a task",
    responses=errors(401, 403, 404, 409, 422),
)
def delete_task(task_id: uuid.UUID, user: CurrentUser, service: TaskServiceDep):
    """OWNER/ADMIN any task; MEMBER only tasks they created or are assigned to."""
    service.delete(task_id, user)
    return Response(status_code=204)
