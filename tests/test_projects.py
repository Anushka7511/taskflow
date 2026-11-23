import uuid

import pytest
from sqlalchemy import text

from tests.helpers import add_member, create_project, create_task, error_code


def test_create_project_makes_creator_the_owner(client, new_account):
    priya = new_account("Priya")
    project = create_project(client, priya, "Alpha", "First project")
    assert project["name"] == "Alpha" and project["owner_id"] == priya.id
    members = client.get(f"/projects/{project['id']}/members", headers=priya.headers).json()
    assert [(m["user_id"], m["role"]) for m in members] == [(priya.id, "OWNER")]


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "   "}, {"name": "x" * 201}, {"name": "ok", "description": "d" * 5001}])
def test_create_project_validation(client, new_account, body):
    r = client.post("/projects", json=body, headers=new_account("Priya").headers)
    assert r.status_code == 422


def test_create_project_requires_authentication(client):
    assert client.post("/projects", json={"name": "x"}).status_code == 401


def test_list_returns_only_my_projects(client, new_account):
    priya, rahul = new_account("Priya"), new_account("Rahul")
    mine = create_project(client, priya, "Mine")
    shared = create_project(client, rahul, "Shared")
    create_project(client, rahul, "Private to Rahul")
    add_member(client, rahul, shared["id"], priya, "MEMBER")

    page = client.get("/projects", headers=priya.headers).json()
    assert {p["id"] for p in page["items"]} == {mine["id"], shared["id"]}
    assert page["total"] == 2


def test_list_projects_is_paginated(client, new_account):
    priya = new_account("Priya")
    for i in range(5):
        create_project(client, priya, f"P{i}")
    page = client.get("/projects?page=2&limit=2", headers=priya.headers).json()
    assert (page["total"], page["pages"], page["page"], len(page["items"])) == (5, 3, 2, 2)


def test_get_project_as_member_and_non_member(team, client):
    assert client.get(f"/projects/{team.project_id}", headers=team.member.headers).status_code == 200
    r = client.get(f"/projects/{team.project_id}", headers=team.outsider.headers)
    assert r.status_code == 404  # existence is not leaked to non-members
    assert error_code(r) == "not_found"


def test_nonexistent_project_looks_the_same_as_forbidden_one(team, client):
    unknown = client.get(f"/projects/{uuid.uuid4()}", headers=team.owner.headers)
    hidden = client.get(f"/projects/{team.project_id}", headers=team.outsider.headers)
    assert unknown.status_code == hidden.status_code == 404
    assert unknown.json() == hidden.json()


def test_invalid_project_id_is_422(client, new_account):
    assert client.get("/projects/not-a-uuid", headers=new_account("Priya").headers).status_code == 422


def test_owner_and_admin_can_update_member_cannot(team, client):
    url = f"/projects/{team.project_id}"
    r = client.patch(url, json={"name": "Renamed"}, headers=team.owner.headers)
    assert r.status_code == 200 and r.json()["name"] == "Renamed"
    r = client.patch(url, json={"description": "Updated by admin"}, headers=team.admin.headers)
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"  # untouched field is preserved
    assert client.patch(url, json={"name": "Hacked"}, headers=team.member.headers).status_code == 403
    assert client.patch(url, json={"name": "Hacked"}, headers=team.outsider.headers).status_code == 404


@pytest.mark.parametrize("body", [{}, {"name": None}, {"name": ""}])
def test_update_project_validation(team, client, body):
    assert client.patch(f"/projects/{team.project_id}", json=body, headers=team.owner.headers).status_code == 422


def test_update_can_clear_description(team, client):
    client.patch(f"/projects/{team.project_id}", json={"description": "x"}, headers=team.owner.headers)
    r = client.patch(f"/projects/{team.project_id}", json={"description": None}, headers=team.owner.headers)
    assert r.json()["description"] is None


def test_only_owner_can_delete_project(team, client):
    url = f"/projects/{team.project_id}"
    assert client.delete(url, headers=team.member.headers).status_code == 403
    assert client.delete(url, headers=team.admin.headers).status_code == 403
    assert client.delete(url, headers=team.outsider.headers).status_code == 404
    assert client.delete(url, headers=team.owner.headers).status_code == 204
    assert client.get(url, headers=team.owner.headers).status_code == 404


def test_deleting_project_cascades_to_tasks_and_memberships(team, client, db):
    create_task(client, team.owner, team.project_id, title="T1", assigned_to=team.member.id)
    create_task(client, team.member, team.project_id, title="T2")
    assert client.delete(f"/projects/{team.project_id}", headers=team.owner.headers).status_code == 204
    assert db.scalar(text("SELECT count(*) FROM tasks")) == 0
    assert db.scalar(text("SELECT count(*) FROM project_memberships")) == 0
    assert db.scalar(text("SELECT count(*) FROM users")) == 4  # users are untouched
