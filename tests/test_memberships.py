import uuid

import pytest

from tests.helpers import add_member, create_task, error_code


def url(team, user=None) -> str:
    base = f"/projects/{team.project_id}/members"
    return f"{base}/{user.id}" if user else base


def roles(client, team) -> dict[str, str]:
    members = client.get(url(team), headers=team.owner.headers).json()
    return {m["user"]["name"]: m["role"] for m in members}


def test_owner_invites_member_by_email(client, new_account, team):
    dev = new_account("Dev")
    r = client.post(url(team), json={"email": dev.email}, headers=team.owner.headers)
    assert r.status_code == 201
    body = r.json()
    assert body["role"] == "MEMBER" and body["user"] == {"id": dev.id, "name": "Dev", "email": dev.email}


def test_invited_user_gains_access(client, new_account, team):
    dev = new_account("Dev")
    assert client.get(f"/projects/{team.project_id}", headers=dev.headers).status_code == 404
    add_member(client, team.owner, team.project_id, dev)
    assert client.get(f"/projects/{team.project_id}", headers=dev.headers).status_code == 200


def test_duplicate_membership_is_a_conflict(client, team):
    r = client.post(url(team), json={"email": team.member.email}, headers=team.owner.headers)
    assert r.status_code == 409 and error_code(r) == "conflict"
    assert len(client.get(url(team), headers=team.owner.headers).json()) == 3


def test_invite_unregistered_email_is_404(client, team):
    r = client.post(url(team), json={"email": "ghost@example.com"}, headers=team.owner.headers)
    assert r.status_code == 404


@pytest.mark.parametrize("body", [{"email": "bad"}, {"email": "a@b.com", "role": "SUPERUSER"}, {}])
def test_invite_validation(client, team, body):
    assert client.post(url(team), json=body, headers=team.owner.headers).status_code == 422


def test_owner_can_invite_with_any_role(client, new_account, team):
    for name, role in [("A", "OWNER"), ("B", "ADMIN"), ("C", "MEMBER")]:
        add_member(client, team.owner, team.project_id, new_account(name), role)
    assert roles(client, team)["A"] == "OWNER"


def test_admin_can_only_invite_plain_members(client, new_account, team):
    dev = new_account("Dev")
    for role in ("ADMIN", "OWNER"):
        r = client.post(url(team), json={"email": dev.email, "role": role}, headers=team.admin.headers)
        assert r.status_code == 403  # privilege escalation blocked
    assert client.post(url(team), json={"email": dev.email, "role": "MEMBER"}, headers=team.admin.headers).status_code == 201


def test_member_and_outsider_cannot_invite(client, new_account, team):
    dev = new_account("Dev")
    assert client.post(url(team), json={"email": dev.email}, headers=team.member.headers).status_code == 403
    assert client.post(url(team), json={"email": dev.email}, headers=team.outsider.headers).status_code == 404


def test_any_member_can_list_members_outsider_cannot(client, team):
    assert len(client.get(url(team), headers=team.member.headers).json()) == 3
    assert client.get(url(team), headers=team.outsider.headers).status_code == 404


def test_owner_changes_roles(client, team):
    r = client.patch(url(team, team.member), json={"role": "ADMIN"}, headers=team.owner.headers)
    assert r.status_code == 200 and r.json()["role"] == "ADMIN"
    assert roles(client, team)["Meera"] == "ADMIN"


@pytest.mark.parametrize("actor", ["admin", "member"])
def test_non_owners_cannot_change_roles(client, team, actor):
    actor_acct = getattr(team, actor)
    r = client.patch(url(team, team.member), json={"role": "ADMIN"}, headers=actor_acct.headers)
    assert r.status_code == 403


def test_admin_cannot_promote_themselves_to_owner(client, team):
    r = client.patch(url(team, team.admin), json={"role": "OWNER"}, headers=team.admin.headers)
    assert r.status_code == 403
    assert roles(client, team)["Rahul"] == "ADMIN"


def test_member_cannot_promote_themselves(client, team):
    assert client.patch(url(team, team.member), json={"role": "OWNER"}, headers=team.member.headers).status_code == 403


def test_change_role_of_unknown_member_is_404(client, team, new_account):
    r = client.patch(url(team, new_account("Nobody")), json={"role": "ADMIN"}, headers=team.owner.headers)
    assert r.status_code == 404


def test_last_owner_cannot_be_demoted(client, team):
    r = client.patch(url(team, team.owner), json={"role": "ADMIN"}, headers=team.owner.headers)
    assert r.status_code == 409
    assert roles(client, team)["Priya"] == "OWNER"


def test_last_owner_cannot_be_removed_or_leave(client, team):
    assert client.delete(url(team, team.owner), headers=team.owner.headers).status_code == 409
    assert roles(client, team)["Priya"] == "OWNER"


def test_owner_can_step_down_once_another_owner_exists(client, team):
    client.patch(url(team, team.admin), json={"role": "OWNER"}, headers=team.owner.headers)
    r = client.patch(url(team, team.owner), json={"role": "MEMBER"}, headers=team.owner.headers)
    assert r.status_code == 200
    # ...and now Rahul is the last owner and is protected.
    assert client.patch(url(team, team.admin), json={"role": "ADMIN"}, headers=team.admin.headers).status_code == 409


def test_admin_cannot_remove_owner_or_other_admin(client, new_account, team):
    other_admin = new_account("Ananya")
    add_member(client, team.owner, team.project_id, other_admin, "ADMIN")
    assert client.delete(url(team, team.owner), headers=team.admin.headers).status_code == 403
    assert client.delete(url(team, other_admin), headers=team.admin.headers).status_code == 403
    assert "Priya" in roles(client, team)


def test_admin_can_remove_plain_member(client, team):
    assert client.delete(url(team, team.member), headers=team.admin.headers).status_code == 204
    assert "Meera" not in roles(client, team)
    assert client.get(f"/projects/{team.project_id}", headers=team.member.headers).status_code == 404


def test_owner_can_remove_admin(client, team):
    assert client.delete(url(team, team.admin), headers=team.owner.headers).status_code == 204


def test_member_can_leave_but_cannot_remove_others(client, team):
    assert client.delete(url(team, team.admin), headers=team.member.headers).status_code == 403
    assert client.delete(url(team, team.member), headers=team.member.headers).status_code == 204


def test_removing_member_unassigns_their_tasks_but_keeps_the_tasks(client, team):
    task = create_task(client, team.owner, team.project_id, title="Fix bug", assigned_to=team.member.id)
    assert task["assigned_to"] == team.member.id
    assert client.delete(url(team, team.member), headers=team.owner.headers).status_code == 204
    after = client.get(f"/tasks/{task['id']}", headers=team.owner.headers).json()
    assert after["assigned_to"] is None and after["project_id"] == team.project_id


def test_remove_unknown_member_is_404(client, team):
    assert client.delete(f"/projects/{team.project_id}/members/{uuid.uuid4()}", headers=team.owner.headers).status_code == 404
