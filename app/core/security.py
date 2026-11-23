"""Password hashing (Argon2id) and JWT access tokens."""

from datetime import datetime, timedelta, timezone
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError


@lru_cache
def _hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_cost=s.argon2_memory_cost_kib,
        parallelism=s.argon2_parallelism,
    )


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher().verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@lru_cache
def dummy_password_hash() -> str:
    """Hash verified when the e-mail is unknown, so login takes about the same
    time whether or not the account exists (mitigates user enumeration by timing)."""
    return _hasher().hash("taskflow-dummy-password")


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> tuple[str, int]:
    """Return (token, lifetime_in_seconds)."""
    settings = get_settings()
    lifetime = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    now = datetime.now(timezone.utc)
    payload = {"sub": subject, "iat": now, "exp": now + lifetime}
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, int(lifetime.total_seconds())


def decode_access_token(token: str) -> str:
    """Validate signature + expiry and return the subject (user id)."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],  # pin the algorithm: blocks alg=none / confusion attacks
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise UnauthorizedError("Token has expired") from None
    except jwt.InvalidTokenError:
        raise UnauthorizedError("Invalid authentication token") from None
    return str(payload["sub"])
