"""RBAC policy (pure unit tests) and end-to-end enforcement over HTTP."""

import uuid

import pytest

from app.core.permissions import Permission, can_grant_role, can_modify_task, can_remove_member, has_permission
from app.models.enums import ProjectRole as R
from tests.helpers import add_member, create_task


# ------------------------------------------------------------ policy matrix
@pytest.mark.parametrize(
    "role, allowed",
    [
        (R.OWNER, set(Permission)),
        (R.ADMIN, set(Permission) - {Permission.DELETE_PROJECT, Permission.CHANGE_MEMBER_ROLE}),
        (R.MEMBER, {Permission.VIEW_PROJECT, Permission.VIEW_MEMBERS, Permission.VIEW_TASKS, Permission.CREATE_TASK}),
    ],
)
def test_permission_matrix(role, allowed):
    for permission in Permission:
        assert has_permission(role, permission) == (permission in allowed), (role, permission)


def test_grant_role_rules():
    assert all(can_grant_role(R.OWNER, r) for r in R)
    assert [r for r in R if can_grant_role(R.ADMIN, r)] == [R.MEMBER]
    assert not any(can_grant_role(R.MEMBER, r) for r in R)


def test_remove_member_rules():
    assert can_remove_member(R.MEMBER, R.MEMBER, is_self=True)
    assert not can_remove_member(R.MEMBER, R.MEMBER, is_self=False)
    assert can_remove_member(R.ADMIN, R.MEMBER, is_self=False)
    assert not can_remove_member(R.ADMIN, R.ADMIN, is_self=False)
    assert not can_remove_member(R.ADMIN, R.OWNER, is_self=False)
    assert can_remove_member(R.OWNER, R.OWNER, is_self=False)


def test_task_modification_rules():
    assert can_modify_task(R.OWNER, is_creator=False, is_assignee=False)
    assert can_modify_task(R.ADMIN, is_creator=False, is_assignee=False)
    assert not can_modify_task(R.MEMBER, is_creator=False, is_assignee=False)
    assert can_modify_task(R.MEMBER, is_creator=True, is_assignee=False)
    assert can_modify_task(R.MEMBER, is_creator=False, is_assignee=True)


# ------------------------------------------------------------ every endpoint needs a token
_ID = str(uuid.uuid4())
PROTECTED = [
    ("GET", "/auth/me", None),
    ("POST", "/projects", {"name": "x"}),
    ("GET", "/projects", None),
    ("GET", f"/projects/{_ID}", None),
    ("PATCH", f"/projects/{_ID}", {"name": "x"}),
    ("DELETE", f"/projects/{_ID}", None),
    ("POST", f"/projects/{_ID}/members", {"email": "a@b.com"}),
    ("GET", f"/projects/{_ID}/members", None),
    ("PATCH", f"/projects/{_ID}/members/{_ID}", {"role": "ADMIN"}),
    ("DELETE", f"/projects/{_ID}/members/{_ID}", None),
    ("POST", f"/projects/{_ID}/tasks", {"title": "x"}),
    ("GET", f"/projects/{_ID}/tasks", None),
    ("GET", f"/tasks/{_ID}", None),
    ("PATCH", f"/tasks/{_ID}", {"version": 1, "title": "x"}),
    ("DELETE", f"/tasks/{_ID}", None),
    ("PATCH", f"/tasks/{_ID}/assign", {"assigned_to": None, "version": 1}),
    ("PATCH", f"/tasks/{_ID}/status", {"status": "DONE", "version": 1}),
]


@pytest.mark.parametrize("method, path, body", PROTECTED)
def test_every_protected_endpoint_rejects_anonymous_requests(client, method, path, body):
    r = client.request(method, path, json=body)
    assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
    assert r.json()["error"]["code"] == "unauthorized"


# ------------------------------------------------------------ outsiders see nothing
def test_outsider_gets_404_on_every_project_scoped_endpoint(client, team):
    t = create_task(client, team.owner, team.project_id)
    h = team.outsider.headers
    p = team.project_id
    assert client.get(f"/projects/{p}", headers=h).status_code == 404
    assert client.patch(f"/projects/{p}", json={"name": "x"}, headers=h).status_code == 404
    assert client.delete(f"/projects/{p}", headers=h).status_code == 404
    assert client.get(f"/projects/{p}/members", headers=h).status_code == 404
    assert client.post(f"/projects/{p}/members", json={"email": "a@b.com"}, headers=h).status_code == 404
    assert client.get(f"/projects/{p}/tasks", headers=h).status_code == 404
    assert client.get(f"/tasks/{t['id']}", headers=h).status_code == 404
    assert client.patch(f"/tasks/{t['id']}", json={"version": 1, "title": "x"}, headers=h).status_code == 404
    assert client.patch(f"/tasks/{t['id']}/status", json={"status": "IN_PROGRESS", "version": 1}, headers=h).status_code == 404
    assert client.delete(f"/tasks/{t['id']}", headers=h).status_code == 404


# ------------------------------------------------------------ task-level rules for plain members
def test_member_can_only_modify_tasks_they_created_or_are_assigned_to(client, team):
    others = create_task(client, team.owner, team.project_id, title="Owner's task")
    mine = create_task(client, team.member, team.project_id, title="My task")
    assigned = create_task(client, team.owner, team.project_id, title="Assigned to me", assigned_to=team.member.id)
    h = team.member.headers

    assert client.patch(f"/tasks/{others['id']}", json={"version": 1, "title": "x"}, headers=h).status_code == 403
    assert client.patch(f"/tasks/{others['id']}/status", json={"status": "IN_PROGRESS", "version": 1}, headers=h).status_code == 403
    assert client.patch(f"/tasks/{others['id']}/assign", json={"assigned_to": team.member.id, "version": 1}, headers=h).status_code == 403
    assert client.delete(f"/tasks/{others['id']}", headers=h).status_code == 403

    assert client.patch(f"/tasks/{mine['id']}", json={"version": 1, "title": "x"}, headers=h).status_code == 200
    assert client.patch(f"/tasks/{assigned['id']}/status", json={"status": "IN_PROGRESS", "version": 1}, headers=h).status_code == 200
    assert client.delete(f"/tasks/{mine['id']}", headers=h).status_code == 204


def test_member_can_still_read_everyones_tasks(client, team):
    t = create_task(client, team.owner, team.project_id)
    assert client.get(f"/tasks/{t['id']}", headers=team.member.headers).status_code == 200
    assert client.get(f"/projects/{team.project_id}/tasks", headers=team.member.headers).json()["total"] == 1


def test_admin_and_owner_can_modify_any_task(client, team):
    t = create_task(client, team.member, team.project_id)
    assert client.patch(f"/tasks/{t['id']}", json={"version": 1, "title": "by admin"}, headers=team.admin.headers).status_code == 200
    assert client.patch(f"/tasks/{t['id']}", json={"version": 2, "title": "by owner"}, headers=team.owner.headers).status_code == 200
    assert client.delete(f"/tasks/{t['id']}", headers=team.admin.headers).status_code == 204


def test_demoted_admin_loses_privileges_immediately(client, team):
    t = create_task(client, team.owner, team.project_id)
    assert client.patch(f"/tasks/{t['id']}", json={"version": 1, "title": "ok"}, headers=team.admin.headers).status_code == 200
    client.patch(f"/projects/{team.project_id}/members/{team.admin.id}", json={"role": "MEMBER"}, headers=team.owner.headers)
    assert client.patch(f"/tasks/{t['id']}", json={"version": 2, "title": "no"}, headers=team.admin.headers).status_code == 403


def test_removed_member_loses_access_immediately_even_with_a_valid_token(client, team):
    t = create_task(client, team.owner, team.project_id)
    assert client.get(f"/tasks/{t['id']}", headers=team.member.headers).status_code == 200
    client.delete(f"/projects/{team.project_id}/members/{team.member.id}", headers=team.owner.headers)
    assert client.get(f"/tasks/{t['id']}", headers=team.member.headers).status_code == 404


def test_roles_are_scoped_per_project(client, team, new_account):
    """Being OWNER of project A gives no power in project B."""
    other_owner = new_account("Other")
    from tests.helpers import create_project
    b = create_project(client, other_owner, "B")
    add_member(client, other_owner, b["id"], team.owner, "MEMBER")  # Priya is only a MEMBER of B
    assert client.patch(f"/projects/{b['id']}", json={"name": "x"}, headers=team.owner.headers).status_code == 403
    assert client.delete(f"/projects/{b['id']}", headers=team.owner.headers).status_code == 403
