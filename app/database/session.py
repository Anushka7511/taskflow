"""Engine, session factory and the unit-of-work (transaction) helper."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
)

# expire_on_commit=False keeps objects readable after commit (no surprise lazy
# loads while serialising responses). Server-generated values are fetched with
# RETURNING because the models set eager_defaults=True.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed.
    close() rolls back anything that was not explicitly committed."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def unit_of_work(db: Session) -> Iterator[Session]:
    """Atomic block: commit if the body succeeds, roll back on *any* exception.

        with unit_of_work(db):
            db.add(project)
            db.add(owner_membership)   # both rows are stored, or neither
    """
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
