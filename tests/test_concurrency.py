"""Transactions and concurrency control, tested with REAL separate database sessions.

Scenario from the specification (Rahul and Priya) is test_rahul_and_priya_scenario.
The thread-based tests force both workers to read the same row version *before* either
writes, so they exercise the race window between SELECT and UPDATE - not just the
easy "stale version in the request body" case.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, ForbiddenError
from app.database.session import SessionLocal, unit_of_work
from app.models.enums import ProjectRole, TaskStatus
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.repositories.membership_repository import MembershipRepository
from app.repositories.task_repository import TaskRepository
from app.schemas.membership import MemberRoleUpdate
from app.schemas.project import ProjectCreate
from app.schemas.task import TaskStatusUpdate, TaskUpdate
from app.services.membership_service import MembershipService
from app.services.project_service import ProjectService
from app.services.task_service import TaskService
from tests.helpers import add_member, create_task


def run_in_session(fn):
    """Run fn(session) in its own session/transaction (as a separate request would)."""
    session = SessionLocal()
    try:
        return fn(session)
    finally:
        session.close()


# ------------------------------------------------------------------ the spec's scenario
def test_rahul_and_priya_scenario(client, team):
    priya, rahul = team.owner, team.admin
    task = create_task(client, priya, team.project_id, title="Original")

    # 1-2. Both retrieve the same task and both see version 1.
    priya_view = client.get(f"/tasks/{task['id']}", headers=priya.headers).json()
    rahul_view = client.get(f"/tasks/{task['id']}", headers=rahul.headers).json()
    assert priya_view["version"] == rahul_view["version"] == 1

    # 3. Rahul updates -> version 2.
    r = client.patch(f"/tasks/{task['id']}", json={"version": 1, "title": "Rahul's edit"}, headers=rahul.headers)
    assert r.status_code == 200 and r.json()["version"] == 2

    # 4-6. Priya tries with version 1 -> 409 Conflict, her change is NOT applied.
    r = client.patch(f"/tasks/{task['id']}", json={"version": 1, "title": "Priya's edit"}, headers=priya.headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"

    final = client.get(f"/tasks/{task['id']}", headers=priya.headers).json()
    assert (final["title"], final["version"]) == ("Rahul's edit", 2)

    # Priya reloads, re-applies on top of the new version, and succeeds.
    r = client.patch(f"/tasks/{task['id']}", json={"version": 2, "title": "Priya's edit"}, headers=priya.headers)
    assert r.status_code == 200 and r.json()["version"] == 3


# ------------------------------------------------------------------ true read-read-write-write races
@pytest.fixture
def force_same_read(monkeypatch):
    """Make N concurrent TaskService calls all finish their SELECT before any of them writes."""

    def install(parties: int):
        barrier = threading.Barrier(parties, timeout=20)
        original = TaskRepository.get

        def get_then_wait(self, task_id):
            task = original(self, task_id)
            barrier.wait()
            return task

        monkeypatch.setattr(TaskRepository, "get", get_then_wait)
        return monkeypatch.undo  # call before doing any verification reads

    return install


@pytest.mark.parametrize("workers", [2, 6])
def test_concurrent_updates_from_same_version_exactly_one_wins(client, team, force_same_read, workers):
    task = create_task(client, team.owner, team.project_id, title="Original")
    release = force_same_read(workers)

    def attempt(i: int) -> str:
        def work(session):
            actor = session.get(User, uuid.UUID(team.owner.id))
            try:
                TaskService(session).update(uuid.UUID(task["id"]), actor, TaskUpdate(version=1, title=f"writer-{i}"))
                return f"ok:{i}"
            except ConflictError:
                return "conflict"

        return run_in_session(work)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(attempt, range(workers)))

    release()  # verification reads below must not wait on the barrier
    winners = [r for r in results if r.startswith("ok")]
    assert len(winners) == 1, results
    assert results.count("conflict") == workers - 1, results

    final = client.get(f"/tasks/{task['id']}", headers=team.owner.headers).json()
    assert final["version"] == 2  # exactly one increment: no lost or double updates
    assert final["title"] == f"writer-{winners[0].split(':')[1]}"  # the winner's data, not a blend


def test_concurrent_status_change_and_edit_do_not_overwrite_each_other(client, team, force_same_read):
    task = create_task(client, team.owner, team.project_id)
    release = force_same_read(2)

    def status_change(session):
        actor = session.get(User, uuid.UUID(team.owner.id))
        TaskService(session).update_status(uuid.UUID(task["id"]), actor, TaskStatusUpdate(status=TaskStatus.IN_PROGRESS, version=1))

    def edit(session):
        actor = session.get(User, uuid.UUID(team.admin.id))
        TaskService(session).update(uuid.UUID(task["id"]), actor, TaskUpdate(version=1, title="edited"))

    outcomes = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_in_session, status_change), pool.submit(run_in_session, edit)]
        for f in futures:
            try:
                f.result()
                outcomes.append("ok")
            except ConflictError:
                outcomes.append("conflict")
    release()
    assert sorted(outcomes) == ["conflict", "ok"]
    assert client.get(f"/tasks/{task['id']}", headers=team.owner.headers).json()["version"] == 2


def test_read_modify_write_with_retry_loses_no_updates(client, team):
    """The correct client behaviour: on 409, reload and retry. Every edit must survive."""
    task = create_task(client, team.owner, team.project_id, title="Counter")
    workers = 5

    def append_tag(i: int) -> int:
        tag, retries = f"[{i}]", 0
        for _ in range(50):
            def attempt(session):
                actor = session.get(User, uuid.UUID(team.owner.id))
                svc = TaskService(session)
                current = svc.get(uuid.UUID(task["id"]), actor)
                svc.update(
                    current.id, actor,
                    TaskUpdate(version=current.version, description=(current.description or "") + tag),
                )

            try:
                run_in_session(attempt)
                return retries
            except ConflictError:
                retries += 1
        raise AssertionError("gave up after 50 retries")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(append_tag, range(workers)))

    final = client.get(f"/tasks/{task['id']}", headers=team.owner.headers).json()
    assert all(f"[{i}]" in final["description"] for i in range(workers)), final["description"]
    assert final["version"] == 1 + workers


def test_stale_in_memory_object_cannot_overwrite_newer_row(client, team, db):
    """Pure ORM-level check of version_id_col, independent of the service layer."""
    task = create_task(client, team.owner, team.project_id, title="v1")
    tid = uuid.UUID(task["id"])

    stale = db.get(Task, tid)  # session A holds version 1
    assert stale.version == 1

    def other_session(session):  # session B commits version 2
        t = session.get(Task, tid)
        t.title = "v2 from B"
        session.commit()

    run_in_session(other_session)

    from sqlalchemy.orm.exc import StaleDataError

    stale.title = "v2 from A (stale)"
    with pytest.raises(StaleDataError):
        db.commit()
    db.rollback()
    assert db.scalar(text("SELECT title FROM tasks WHERE id = :i"), {"i": tid}) == "v2 from B"


def test_delete_after_concurrent_modification_is_a_conflict_not_a_silent_delete(client, team, db):
    task = create_task(client, team.owner, team.project_id)
    tid = uuid.UUID(task["id"])
    stale = db.get(Task, tid)

    def modify(session):
        session.get(Task, tid).title = "changed"
        session.commit()

    run_in_session(modify)
    from sqlalchemy.orm.exc import StaleDataError

    db.delete(stale)
    with pytest.raises(StaleDataError):
        db.commit()


# ------------------------------------------------------------------ membership races (row lock)
def test_two_owners_demoting_each_other_concurrently_never_leaves_zero_owners(client, team, db):
    add_member_resp = client.patch(
        f"/projects/{team.project_id}/members/{team.admin.id}", json={"role": "OWNER"}, headers=team.owner.headers
    )
    assert add_member_resp.status_code == 200  # Priya and Rahul are both OWNERs now
    pid = uuid.UUID(team.project_id)
    barrier = threading.Barrier(2, timeout=20)

    def demote(actor_id: str, target_id: str) -> str:
        def work(session):
            actor = session.get(User, uuid.UUID(actor_id))
            barrier.wait()
            try:
                MembershipService(session).change_role(pid, actor, uuid.UUID(target_id), MemberRoleUpdate(role=ProjectRole.MEMBER))
                return "ok"
            except (ConflictError, ForbiddenError):
                return "rejected"

        return run_in_session(work)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(demote, team.owner.id, team.admin.id)
        b = pool.submit(demote, team.admin.id, team.owner.id)
        results = sorted([a.result(), b.result()])

    assert results == ["ok", "rejected"], results
    owners = db.scalar(text("SELECT count(*) FROM project_memberships WHERE project_id = :p AND role = 'OWNER'"), {"p": pid})
    assert owners == 1


def test_two_owners_removing_each_other_concurrently_leaves_one_owner(client, team, db):
    client.patch(f"/projects/{team.project_id}/members/{team.admin.id}", json={"role": "OWNER"}, headers=team.owner.headers)
    pid = uuid.UUID(team.project_id)
    barrier = threading.Barrier(2, timeout=20)

    def remove(actor_id: str, target_id: str) -> str:
        def work(session):
            actor = session.get(User, uuid.UUID(actor_id))
            barrier.wait()
            try:
                MembershipService(session).remove_member(pid, actor, uuid.UUID(target_id))
                return "ok"
            except (ConflictError, ForbiddenError):
                return "rejected"
            except Exception as exc:  # noqa: BLE001 - e.g. actor's own row vanished => NotFound
                return type(exc).__name__

        return run_in_session(work)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result() for f in [pool.submit(remove, team.owner.id, team.admin.id), pool.submit(remove, team.admin.id, team.owner.id)]]

    assert results.count("ok") == 1, results
    owners = db.scalar(text("SELECT count(*) FROM project_memberships WHERE project_id = :p AND role = 'OWNER'"), {"p": pid})
    assert owners == 1


def test_concurrent_duplicate_invites_create_one_membership(client, team, new_account, db):
    dev = new_account("Dev")
    barrier = threading.Barrier(2, timeout=20)
    from app.schemas.membership import MemberInvite

    def invite(_):
        def work(session):
            actor = session.get(User, uuid.UUID(team.owner.id))
            barrier.wait()
            try:
                MembershipService(session).add_member(uuid.UUID(team.project_id), actor, MemberInvite(email=dev.email))
                return "ok"
            except ConflictError:
                return "conflict"

        return run_in_session(work)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(invite, range(2)))
    assert results == ["conflict", "ok"]
    assert db.scalar(text("SELECT count(*) FROM project_memberships WHERE user_id = :u"), {"u": dev.id}) == 1


# ------------------------------------------------------------------ transactions: commit / rollback / atomicity
def test_unit_of_work_commits_on_success(db):
    with unit_of_work(db):
        db.add(User(name="A", email="a@example.com", password_hash="x"))
    assert run_in_session(lambda s: s.scalar(text("SELECT count(*) FROM users"))) == 1  # visible to OTHER sessions


def test_unit_of_work_rolls_back_everything_on_error(db):
    with pytest.raises(RuntimeError):
        with unit_of_work(db):
            db.add(User(name="A", email="a@example.com", password_hash="x"))
            db.flush()  # row is written inside the transaction...
            raise RuntimeError("boom")
    assert run_in_session(lambda s: s.scalar(text("SELECT count(*) FROM users"))) == 0  # ...but never committed


def test_uncommitted_changes_are_invisible_to_other_sessions(db):
    db.add(User(name="A", email="a@example.com", password_hash="x"))
    db.flush()
    assert db.scalar(text("SELECT count(*) FROM users")) == 1
    assert run_in_session(lambda s: s.scalar(text("SELECT count(*) FROM users"))) == 0
    db.rollback()


def test_project_creation_is_atomic_if_the_membership_insert_fails(client, new_account, db, monkeypatch):
    priya = new_account("Priya")
    monkeypatch.setattr(MembershipRepository, "add", lambda self, m: (_ for _ in ()).throw(RuntimeError("boom")))

    def work(session):
        owner = session.get(User, uuid.UUID(priya.id))
        with pytest.raises(RuntimeError):
            ProjectService(session).create(owner, ProjectCreate(name="Ghost"))

    run_in_session(work)
    assert db.scalar(text("SELECT count(*) FROM projects")) == 0  # no orphan project without an owner


def test_integrity_error_inside_unit_of_work_rolls_back_earlier_writes(client, team, db):
    pid = uuid.UUID(team.project_id)
    with pytest.raises(IntegrityError):
        with unit_of_work(db):
            db.add(Project(name="should vanish", owner_id=uuid.UUID(team.owner.id)))
            db.flush()
            db.add(Task(project_id=pid, title="bad", assigned_to=uuid.UUID(team.outsider.id)))  # non-member
            db.flush()
    assert db.scalar(text("SELECT count(*) FROM projects WHERE name = 'should vanish'")) == 0


def test_unexpected_error_returns_generic_500_and_leaks_nothing(team, monkeypatch, caplog):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.project_service import ProjectService as PS

    def explode(*a, **k):
        raise RuntimeError("secret-db-password=hunter2")

    monkeypatch.setattr(PS, "list_for_user", explode)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/projects", headers=team.owner.headers)
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Internal server error", "details": None}}
    assert "hunter2" not in r.text and "Traceback" not in r.text
    assert "hunter2" in caplog.text  # ...but it IS logged server-side for operators
