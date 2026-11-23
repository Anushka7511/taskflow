"""Test fixtures.

The suite runs against a REAL PostgreSQL database (never SQLite): the behaviour under
test - composite FKs, native enums, ON DELETE SET NULL (col), row locks, concurrent
UPDATEs - is PostgreSQL behaviour.

The test database is dropped and rebuilt from the Alembic migrations once per session
(so the migrations themselves are tested) and all tables are truncated after each test.
"""

import os
from pathlib import Path
from types import SimpleNamespace

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://taskflow@127.0.0.1:5432/taskflow_test"
)

# Must be set BEFORE the app modules are imported (settings are read at import time).
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["SECRET_KEY"] = "test-secret-key-that-is-definitely-long-enough-0123456789"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["ARGON2_TIME_COST"] = "1"  # cheap hashing, tests only
os.environ["ARGON2_MEMORY_COST_KIB"] = "8"
os.environ["ARGON2_PARALLELISM"] = "1"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.database.session import SessionLocal, engine
from app.main import app
from tests import helpers

ROOT = Path(__file__).resolve().parent.parent
_url = make_url(TEST_DATABASE_URL)
if not (_url.database or "").endswith("_test"):
    raise RuntimeError("Refusing to run: TEST_DATABASE_URL must point to a database whose name ends with '_test'")

TABLES = "tasks, project_memberships, projects, users"


@pytest.fixture(scope="session", autouse=True)
def _database():
    admin = create_engine(_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{_url.database}"'))
    admin.dispose()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(_database):
    yield
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def new_account(client):
    def factory(name: str, password: str = helpers.PASSWORD) -> helpers.Account:
        return helpers.make_account(client, name, password=password)

    return factory


@pytest.fixture
def team(client, new_account):
    """A project with one account per role plus an outsider (member of nothing)."""
    owner = new_account("Priya")
    admin = new_account("Rahul")
    member = new_account("Meera")
    outsider = new_account("Oscar")
    project = helpers.create_project(client, owner, "Alpha")
    helpers.add_member(client, owner, project["id"], admin, "ADMIN")
    helpers.add_member(client, owner, project["id"], member, "MEMBER")
    return SimpleNamespace(
        owner=owner, admin=admin, member=member, outsider=outsider, project_id=project["id"]
    )
