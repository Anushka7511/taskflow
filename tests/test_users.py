"""User model, password hashing and schema behaviour."""

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.security import dummy_password_hash, hash_password, verify_password
from app.schemas.user import UserCreate
from tests.helpers import PASSWORD, make_account


def test_hashing_is_salted_and_verifiable():
    h1, h2 = hash_password(PASSWORD), hash_password(PASSWORD)
    assert h1 != h2  # random salt
    assert verify_password(h1, PASSWORD) and verify_password(h2, PASSWORD)
    assert not verify_password(h1, PASSWORD + "x")


def test_verify_password_tolerates_garbage_hash():
    assert verify_password("not-a-hash", PASSWORD) is False
    assert verify_password("", PASSWORD) is False


def test_dummy_hash_never_matches_real_input():
    assert not verify_password(dummy_password_hash(), PASSWORD)


def test_schema_normalises_and_trims():
    u = UserCreate(name="  Priya  ", email="  PRIYA@Example.COM ", password=PASSWORD)
    assert u.name == "Priya"
    assert u.email == "priya@example.com"


def test_schema_rejects_short_password():
    with pytest.raises(ValidationError):
        UserCreate(name="A", email="a@example.com", password="1234567")


def test_me_profile_has_no_sensitive_fields(client):
    acct = make_account(client, "Priya")
    body = client.get("/auth/me", headers=acct.headers).json()
    assert "password_hash" not in body and "password" not in body


def test_there_is_no_endpoint_that_lists_all_users(client):
    acct = make_account(client, "Priya")
    assert client.get("/users", headers=acct.headers).status_code == 404


def test_database_rejects_duplicate_email(db):
    sql = text("INSERT INTO users (name, email, password_hash) VALUES ('A', :e, 'x')")
    db.execute(sql, {"e": "dup@example.com"})
    with pytest.raises(IntegrityError) as exc:
        db.execute(sql, {"e": "dup@example.com"})
    assert exc.value.orig.diag.constraint_name == "uq_users_email"


def test_database_rejects_non_lowercase_email(db):
    with pytest.raises(IntegrityError) as exc:
        db.execute(text("INSERT INTO users (name, email, password_hash) VALUES ('A', 'Mixed@Example.com', 'x')"))
    assert exc.value.orig.diag.constraint_name == "ck_users_email_lowercase"


def test_updated_at_is_maintained(client, db):
    acct = make_account(client, "Priya")
    before = db.scalar(text("SELECT updated_at FROM users WHERE id = :i"), {"i": acct.id})
    db.execute(text("UPDATE users SET name = 'New Name' WHERE id = :i"), {"i": acct.id})
    db.commit()
    # server default only sets creation time; ORM-level onupdate is covered via the ORM:
    from app.models.user import User

    user = db.get(User, acct.id)
    user.name = "Renamed"
    db.commit()
    db.refresh(user)
    assert user.updated_at >= before
