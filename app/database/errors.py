"""Helpers for translating PostgreSQL integrity errors into domain errors."""

from sqlalchemy.exc import IntegrityError


def violated_constraint(exc: IntegrityError) -> str | None:
    """Name of the constraint that caused the error (psycopg3 diagnostics)."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)
