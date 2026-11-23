"""Small HTTP helpers shared by the tests."""

from dataclasses import dataclass

PASSWORD = "Passw0rd-secret"


@dataclass
class Account:
    id: str
    name: str
    email: str
    password: str
    headers: dict[str, str]


def make_account(client, name: str, *, password: str = PASSWORD, email: str | None = None) -> Account:
    email = email or f"{name.lower().replace(' ', '.')}@example.com"
    r = client.post("/auth/register", json={"name": name, "email": email, "password": password})
    assert r.status_code == 201, r.text
    user = r.json()
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return Account(
        id=user["id"], name=name, email=email, password=password,
        headers={"Authorization": f"Bearer {r.json()['access_token']}"},
    )


def create_project(client, owner: Account, name: str = "Project", description: str | None = None) -> dict:
    r = client.post("/projects", json={"name": name, "description": description}, headers=owner.headers)
    assert r.status_code == 201, r.text
    return r.json()


def add_member(client, actor: Account, project_id: str, member: Account, role: str = "MEMBER") -> dict:
    r = client.post(
        f"/projects/{project_id}/members", json={"email": member.email, "role": role}, headers=actor.headers
    )
    assert r.status_code == 201, r.text
    return r.json()


def create_task(client, actor: Account, project_id: str, **fields) -> dict:
    body = {"title": "Task", **fields}
    r = client.post(f"/projects/{project_id}/tasks", json=body, headers=actor.headers)
    assert r.status_code == 201, r.text
    return r.json()


def error_code(response) -> str:
    return response.json()["error"]["code"]
