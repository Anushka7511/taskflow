"""Invalid relational states must be impossible at the DATABASE level, whatever the app does."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from tests.helpers import add_member, create_project, create_task


def constraint(exc_info) -> str:
    return exc_info.value.orig.diag.constraint_name


def insert_task(db, project_id, assigned_to=None, title="t", version=1):
    db.execute(
        text("INSERT INTO tasks (project_id, assigned_to, title, version) VALUES (:p, :a, :t, :v)"),
        {"p": project_id, "a": assigned_to, "t": title, "v": version},
    )


def test_duplicate_membership_is_impossible(team, db):
    with pytest.raises(IntegrityError) as exc:
        db.execute(
            text("INSERT INTO project_memberships (project_id, user_id, role) VALUES (:p, :u, 'MEMBER')"),
            {"p": team.project_id, "u": team.member.id},
        )
    assert constraint(exc) == "uq_project_memberships_project_user"


def test_task_cannot_be_assigned_to_a_non_member(team, db):
    with pytest.raises(IntegrityError) as exc:
        insert_task(db, team.project_id, assigned_to=team.outsider.id)
    assert constraint(exc) == "fk_tasks_assignee_is_project_member"


def test_membership_in_a_different_project_does_not_count(client, team, db):
    """Oscar is a member of project B, but that must not let him be assigned in project A."""
    b = create_project(client, team.outsider, "Oscar's project")
    with pytest.raises(IntegrityError) as exc:
        insert_task(db, team.project_id, assigned_to=team.outsider.id)
    assert constraint(exc) == "fk_tasks_assignee_is_project_member"
    db.rollback()  # PostgreSQL aborts the whole transaction after an error
    insert_task(db, b["id"], assigned_to=team.outsider.id)  # ...but is fine in his own project
    db.commit()


def test_unassigned_task_is_valid(team, db):
    insert_task(db, team.project_id, assigned_to=None)
    db.commit()


def test_removing_membership_nulls_only_assigned_to(client, team, db):
    t = create_task(client, team.owner, team.project_id, assigned_to=team.member.id)
    db.execute(
        text("DELETE FROM project_memberships WHERE project_id = :p AND user_id = :u"),
        {"p": team.project_id, "u": team.member.id},
    )
    db.commit()
    row = db.execute(text("SELECT project_id::text, assigned_to FROM tasks WHERE id = :i"), {"i": t["id"]}).one()
    assert row.assigned_to is None and row.project_id == team.project_id


def test_task_requires_an_existing_project(db):
    with pytest.raises(IntegrityError) as exc:
        insert_task(db, str(uuid.uuid4()))
    assert constraint(exc) == "fk_tasks_project_id_projects"


def test_membership_requires_existing_user_and_project(team, db):
    with pytest.raises(IntegrityError):
        db.execute(
            text("INSERT INTO project_memberships (project_id, user_id, role) VALUES (:p, :u, 'MEMBER')"),
            {"p": team.project_id, "u": str(uuid.uuid4())},
        )


def test_deleting_a_user_who_created_a_project_is_restricted(team, db):
    with pytest.raises(IntegrityError) as exc:
        db.execute(text("DELETE FROM users WHERE id = :u"), {"u": team.owner.id})
    assert constraint(exc) == "fk_projects_owner_id_users"


def test_deleting_a_task_creator_keeps_the_task(client, team, db):
    t = create_task(client, team.member, team.project_id)
    db.execute(text("DELETE FROM users WHERE id = :u"), {"u": team.member.id})
    db.commit()
    row = db.execute(text("SELECT created_by FROM tasks WHERE id = :i"), {"i": t["id"]}).one()
    assert row.created_by is None


@pytest.mark.parametrize("title", ["", "   "])
def test_blank_task_title_rejected(team, db, title):
    with pytest.raises(IntegrityError) as exc:
        insert_task(db, team.project_id, title=title)
    assert constraint(exc) == "ck_tasks_title_not_blank"


@pytest.mark.parametrize("version", [0, -1])
def test_task_version_must_be_positive(team, db, version):
    with pytest.raises(IntegrityError) as exc:
        insert_task(db, team.project_id, version=version)
    assert constraint(exc) == "ck_tasks_version_positive"


def test_blank_project_name_rejected(team, db):
    with pytest.raises(IntegrityError) as exc:
        db.execute(text("INSERT INTO projects (name, owner_id) VALUES ('  ', :o)"), {"o": team.owner.id})
    assert constraint(exc) == "ck_projects_name_not_blank"


def test_invalid_enum_values_rejected_by_postgres(team, db):
    with pytest.raises(DataError):
        db.execute(
            text("INSERT INTO tasks (project_id, title, status) VALUES (:p, 't', 'BLOCKED')"), {"p": team.project_id}
        )
    db.rollback()
    with pytest.raises(DataError):
        db.execute(
            text("INSERT INTO project_memberships (project_id, user_id, role) VALUES (:p, :u, 'GOD')"),
            {"p": team.project_id, "u": team.outsider.id},
        )


def test_expected_indexes_exist(db):
    names = set(db.scalars(text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")))
    assert {
        "uq_users_email", "uq_project_memberships_project_user", "ix_project_memberships_user_id",
        "ix_tasks_project_id_status", "ix_tasks_assigned_to",
        "ix_tasks_project_id_created_at", "ix_tasks_project_id_due_date",
    } <= names
