import threading
import uuid
from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.core.security import create_access_token
from app.main import app
from tests.helpers import PASSWORD, error_code, make_account

REGISTER = {"name": "Priya Sharma", "email": "priya@example.com", "password": PASSWORD}


def test_register_success_never_exposes_secrets(client):
    r = client.post("/auth/register", json=REGISTER)
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"id", "name", "email", "created_at"}
    assert body["email"] == "priya@example.com"
    assert "password" not in r.text and "hash" not in r.text.lower()


def test_password_is_stored_as_argon2_hash_not_plaintext(client, db):
    client.post("/auth/register", json=REGISTER)
    stored = db.scalar(text("SELECT password_hash FROM users WHERE email = 'priya@example.com'"))
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


@pytest.mark.parametrize("email", ["priya@example.com", "PRIYA@EXAMPLE.COM", "  Priya@Example.com "])
def test_duplicate_email_rejected_case_insensitively(client, email):
    assert client.post("/auth/register", json=REGISTER).status_code == 201
    r = client.post("/auth/register", json={**REGISTER, "email": email})
    assert r.status_code == 409
    assert error_code(r) == "conflict"


def test_email_is_normalised_to_lowercase(client):
    r = client.post("/auth/register", json={**REGISTER, "email": "Priya@Example.COM"})
    assert r.json()["email"] == "priya@example.com"


@pytest.mark.parametrize(
    "patch",
    [
        {"password": "short"},
        {"password": "x" * 129},
        {"email": "not-an-email"},
        {"name": "   "},
        {"name": "n" * 101},
    ],
)
def test_register_validation_errors(client, patch):
    r = client.post("/auth/register", json={**REGISTER, **patch})
    assert r.status_code == 422
    assert error_code(r) == "validation_error"


def test_validation_error_does_not_echo_the_submitted_password(client):
    secret = "Zq9#kX"  # too short (6 chars) and unlikely to collide with any error wording
    r = client.post("/auth/register", json={**REGISTER, "password": secret})
    assert r.status_code == 422
    assert secret not in r.text


def test_register_missing_fields(client):
    assert client.post("/auth/register", json={}).status_code == 422


def test_login_success_returns_valid_jwt(client):
    user = client.post("/auth/register", json=REGISTER).json()
    r = client.post("/auth/login", json={"email": REGISTER["email"], "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == get_settings().access_token_expire_minutes * 60
    claims = jwt.decode(body["access_token"], get_settings().secret_key, algorithms=["HS256"])
    assert claims["sub"] == user["id"]


def test_login_is_case_insensitive_on_email(client):
    client.post("/auth/register", json=REGISTER)
    r = client.post("/auth/login", json={"email": "PRIYA@example.com", "password": PASSWORD})
    assert r.status_code == 200


def test_login_wrong_password_and_unknown_email_look_identical(client):
    client.post("/auth/register", json=REGISTER)
    wrong_pw = client.post("/auth/login", json={"email": REGISTER["email"], "password": "wrong-password"})
    unknown = client.post("/auth/login", json={"email": "ghost@example.com", "password": PASSWORD})
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()  # no user enumeration


def test_me_returns_current_user(client):
    acct = make_account(client, "Priya")
    r = client.get("/auth/me", headers=acct.headers)
    assert r.status_code == 200
    assert r.json()["id"] == acct.id


def test_missing_token_gives_401_with_challenge_header(client):
    r = client.get("/auth/me")
    assert r.status_code == 401
    assert error_code(r) == "unauthorized"
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Bearer", "Bearer not-a-jwt", "Basic abc", "garbage"])
def test_malformed_authorization_headers_rejected(client, header):
    assert client.get("/auth/me", headers={"Authorization": header}).status_code == 401


def test_expired_token_rejected(client):
    acct = make_account(client, "Priya")
    token, _ = create_access_token(acct.id, expires_delta=timedelta(seconds=-5))
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert "expired" in r.json()["error"]["message"].lower()


def test_token_with_tampered_payload_rejected(client):
    acct = make_account(client, "Priya")
    token = acct.headers["Authorization"].split()[1]
    header, payload, sig = token.split(".")
    forged_payload = jwt.utils.base64url_encode(
        b'{"sub":"%s","exp":9999999999}' % str(uuid.uuid4()).encode()
    ).decode()
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {header}.{forged_payload}.{sig}"})
    assert r.status_code == 401


def test_token_signed_with_other_secret_rejected(client):
    acct = make_account(client, "Priya")
    forged = jwt.encode({"sub": acct.id, "exp": 9999999999}, "another-secret-" * 4, algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_alg_none_token_rejected(client):
    acct = make_account(client, "Priya")
    forged = jwt.encode({"sub": acct.id, "exp": 9999999999}, key=None, algorithm="none")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_token_without_expiry_rejected(client):
    acct = make_account(client, "Priya")
    forged = jwt.encode({"sub": acct.id}, get_settings().secret_key, algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_token_with_non_uuid_subject_rejected(client):
    token, _ = create_access_token("not-a-uuid")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_valid_token_for_deleted_user_rejected(client, db):
    acct = make_account(client, "Priya")
    db.execute(text("DELETE FROM users WHERE id = :id"), {"id": acct.id})
    db.commit()
    assert client.get("/auth/me", headers=acct.headers).status_code == 401


def test_simultaneous_registration_with_same_email_creates_exactly_one_user(db):
    """Both requests can pass the 'email free?' pre-check; the unique constraint decides."""
    barrier = threading.Barrier(2, timeout=15)
    results: list[int] = []

    def register():
        with TestClient(app) as c:
            barrier.wait()
            results.append(c.post("/auth/register", json=REGISTER).status_code)

    threads = [threading.Thread(target=register) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [201, 409]
    assert db.scalar(text("SELECT count(*) FROM users")) == 1
