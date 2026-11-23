import uuid

import pytest

from tests.helpers import add_member, create_task, error_code


def patch(client, actor, task, path="", **body):
    return client.patch(f"/tasks/{task['id']}{path}", json=body, headers=actor.headers)


def test_create_task_defaults(client, team):
    t = create_task(client, team.member, team.project_id, title="  Write docs  ")
    assert t["title"] == "Write docs"  # trimmed
    assert (t["status"], t["priority"], t["version"]) == ("TODO", "MEDIUM", 1)
    assert t["created_by"] == team.member.id and t["assigned_to"] is None and t["due_date"] is None


def test_create_task_with_all_fields_and_assignee(client, team):
    t = create_task(
        client, team.owner, team.project_id, title="Ship", description="v1", priority="HIGH",
        due_date="2026-12-31", assigned_to=team.member.id,
    )
    assert (t["priority"], t["due_date"], t["assigned_to"]) == ("HIGH", "2026-12-31", team.member.id)


@pytest.mark.parametrize(
    "body",
    [{}, {"title": ""}, {"title": "  "}, {"title": "x" * 201}, {"title": "t", "priority": "URGENT"},
     {"title": "t", "due_date": "31-12-2026"}, {"title": "t", "assigned_to": "nope"}],
)
def test_create_task_validation(client, team, body):
    r = client.post(f"/projects/{team.project_id}/tasks", json=body, headers=team.owner.headers)
    assert r.status_code == 422


def test_create_task_in_unknown_project_is_404(client, team):
    r = client.post(f"/projects/{uuid.uuid4()}/tasks", json={"title": "t"}, headers=team.owner.headers)
    assert r.status_code == 404


def test_outsider_cannot_create_task(client, team):
    r = client.post(f"/projects/{team.project_id}/tasks", json={"title": "t"}, headers=team.outsider.headers)
    assert r.status_code == 404


def test_cannot_assign_to_non_member_on_create(client, team):
    r = client.post(
        f"/projects/{team.project_id}/tasks",
        json={"title": "t", "assigned_to": team.outsider.id}, headers=team.owner.headers,
    )
    assert r.status_code == 400
    assert "member" in r.json()["error"]["message"].lower()


def test_cannot_assign_to_nonexistent_user(client, team):
    r = client.post(
        f"/projects/{team.project_id}/tasks",
        json={"title": "t", "assigned_to": str(uuid.uuid4())}, headers=team.owner.headers,
    )
    assert r.status_code == 400


def test_get_task(client, team):
    t = create_task(client, team.owner, team.project_id)
    assert client.get(f"/tasks/{t['id']}", headers=team.member.headers).json() == t
    assert client.get(f"/tasks/{t['id']}", headers=team.outsider.headers).status_code == 404
    assert client.get(f"/tasks/{uuid.uuid4()}", headers=team.owner.headers).status_code == 404


def test_member_of_a_different_project_cannot_read_the_task(client, team, new_account):
    t = create_task(client, team.owner, team.project_id)
    other = new_account("Other")
    from tests.helpers import create_project
    create_project(client, other, "Their project")
    assert client.get(f"/tasks/{t['id']}", headers=other.headers).status_code == 404


def test_update_increments_version_and_changes_fields(client, team):
    t = create_task(client, team.owner, team.project_id, title="Old")
    r = patch(client, team.owner, t, title="New", priority="HIGH", version=1)
    assert r.status_code == 200
    body = r.json()
    assert (body["title"], body["priority"], body["version"]) == ("New", "HIGH", 2)
    assert body["updated_at"] >= t["updated_at"]


def test_update_can_clear_nullable_fields(client, team):
    t = create_task(client, team.owner, team.project_id, description="d", due_date="2026-12-01")
    r = patch(client, team.owner, t, description=None, due_date=None, version=1)
    assert r.json()["description"] is None and r.json()["due_date"] is None


@pytest.mark.parametrize(
    "body",
    [{"title": "x"}, {"version": 1}, {"version": 0, "title": "x"}, {"version": 1, "title": None},
     {"version": 1, "priority": None}, {"version": 1, "title": " "}, {"version": "abc", "title": "x"}],
)
def test_update_validation(client, team, body):
    t = create_task(client, team.owner, team.project_id)
    assert client.patch(f"/tasks/{t['id']}", json=body, headers=team.owner.headers).status_code == 422


def test_stale_version_gets_409_with_current_version(client, team):
    t = create_task(client, team.owner, team.project_id, title="Original")
    assert patch(client, team.owner, t, title="First writer", version=1).status_code == 200
    r = patch(client, team.admin, t, title="Second writer", version=1)
    assert r.status_code == 409 and error_code(r) == "conflict"
    assert r.json()["error"]["details"] == {"submitted_version": 1, "current_version": 2}
    assert client.get(f"/tasks/{t['id']}", headers=team.owner.headers).json()["title"] == "First writer"


def test_future_version_is_rejected_too(client, team):
    t = create_task(client, team.owner, team.project_id)
    assert patch(client, team.owner, t, title="x", version=99).status_code == 409


def test_status_follows_the_workflow(client, team):
    t = create_task(client, team.owner, team.project_id)
    path = [("IN_PROGRESS", 1), ("DONE", 2), ("IN_PROGRESS", 3), ("TODO", 4)]
    for status, version in path:
        r = patch(client, team.owner, t, "/status", status=status, version=version)
        assert r.status_code == 200 and r.json()["status"] == status and r.json()["version"] == version + 1


def test_todo_cannot_jump_straight_to_done(client, team):
    t = create_task(client, team.owner, team.project_id)
    r = patch(client, team.owner, t, "/status", status="DONE", version=1)
    assert r.status_code == 400
    assert r.json()["error"]["details"] == {"allowed": ["IN_PROGRESS"]}
    assert client.get(f"/tasks/{t['id']}", headers=team.owner.headers).json()["status"] == "TODO"


def test_setting_the_same_status_is_rejected(client, team):
    t = create_task(client, team.owner, team.project_id)
    assert patch(client, team.owner, t, "/status", status="TODO", version=1).status_code == 400


def test_invalid_status_value_is_422(client, team):
    t = create_task(client, team.owner, team.project_id)
    assert patch(client, team.owner, t, "/status", status="BLOCKED", version=1).status_code == 422


def test_status_change_with_stale_version_is_409_even_if_transition_is_invalid(client, team):
    t = create_task(client, team.owner, team.project_id)
    patch(client, team.owner, t, title="bump", version=1)
    assert patch(client, team.owner, t, "/status", status="DONE", version=1).status_code == 409


def test_assign_and_unassign(client, team):
    t = create_task(client, team.owner, team.project_id)
    r = patch(client, team.owner, t, "/assign", assigned_to=team.member.id, version=1)
    assert r.status_code == 200 and r.json()["assigned_to"] == team.member.id and r.json()["version"] == 2
    r = patch(client, team.owner, t, "/assign", assigned_to=None, version=2)
    assert r.json()["assigned_to"] is None


def test_assign_to_non_member_is_rejected_and_task_unchanged(client, team):
    t = create_task(client, team.owner, team.project_id)
    r = patch(client, team.owner, t, "/assign", assigned_to=team.outsider.id, version=1)
    assert r.status_code == 400
    after = client.get(f"/tasks/{t['id']}", headers=team.owner.headers).json()
    assert after["assigned_to"] is None and after["version"] == 1


def test_assign_requires_explicit_assigned_to_field(client, team):
    t = create_task(client, team.owner, team.project_id)
    assert patch(client, team.owner, t, "/assign", version=1).status_code == 422


def test_assignee_who_is_later_invited_can_be_assigned(client, team, new_account):
    dev = new_account("Dev")
    t = create_task(client, team.owner, team.project_id)
    assert patch(client, team.owner, t, "/assign", assigned_to=dev.id, version=1).status_code == 400
    add_member(client, team.owner, team.project_id, dev)
    assert patch(client, team.owner, t, "/assign", assigned_to=dev.id, version=1).status_code == 200


def test_delete_task(client, team):
    t = create_task(client, team.owner, team.project_id)
    assert client.delete(f"/tasks/{t['id']}", headers=team.owner.headers).status_code == 204
    assert client.get(f"/tasks/{t['id']}", headers=team.owner.headers).status_code == 404
    assert client.delete(f"/tasks/{t['id']}", headers=team.owner.headers).status_code == 404


def test_outsider_cannot_delete_task(client, team):
    t = create_task(client, team.owner, team.project_id)
    assert client.delete(f"/tasks/{t['id']}", headers=team.outsider.headers).status_code == 404
    assert client.get(f"/tasks/{t['id']}", headers=team.owner.headers).status_code == 200
