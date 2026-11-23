import pytest
from pydantic import ValidationError

from app.core.config import Settings

DEV_SECRET = "dev-only-" + "x" * 30


def make(**kw):
    return Settings(database_url="postgresql+psycopg://u@h/d", _env_file=None, **kw)


def test_secret_key_is_required_and_must_be_long():
    with pytest.raises(ValidationError):
        make(secret_key="too-short")


def test_dev_placeholder_secret_is_rejected_in_production():
    with pytest.raises(ValidationError):
        make(secret_key=DEV_SECRET, environment="production")


def test_dev_placeholder_secret_is_fine_in_development():
    assert make(secret_key=DEV_SECRET, environment="development").environment == "development"


def test_real_secret_is_fine_in_production():
    assert make(secret_key="a" * 48, environment="production")
