"""Engine and session management.

The database is optional. Every CLI command in this project works without one
and always has, so a missing `RXAUTH_DATABASE_URL` is a normal state that says
"you are running the CLI", not a misconfiguration. Only the service layer
requires a database, and it says so by name.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator, Optional

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from .tables import Base


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when something needs a database and none is configured."""


def _require_url(settings: Settings) -> str:
    if not settings.database_url:
        raise DatabaseNotConfiguredError(
            "No RXAUTH_DATABASE_URL is set. The CLI does not need one; the API does. "
            "For local work:\n"
            "    docker compose up -d postgres\n"
            "    export RXAUTH_DATABASE_URL=postgresql+psycopg://rxauth:rxauth@localhost:5432/rxauth"
        )
    return settings.database_url


def engine_for(settings: Optional[Settings] = None) -> Engine:
    """Build an engine for these settings.

    `pool_pre_ping` because a pooled connection that a database restart or an
    idle timeout has already closed otherwise surfaces as a failed case run
    rather than as a reconnect.
    """
    active = settings or get_settings()
    return create_engine(
        _require_url(active),
        echo=active.database_echo,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def _default_engine() -> Engine:
    return engine_for()


def sessionmaker_for(engine: Optional[Engine] = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or _default_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope(engine: Optional[Engine] = None) -> Iterator[Session]:
    """A transaction that commits on success and rolls back on anything else.

    `expire_on_commit=False` on the sessionmaker means objects stay readable
    after the commit, so a caller does not have to choose between a live
    session and a usable result.
    """
    factory = sessionmaker_for(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all(engine: Optional[Engine] = None) -> None:
    """Create every table directly, bypassing migrations.

    For tests and for a throwaway local database. Deployed environments run
    `alembic upgrade head`, because a schema that appears by side effect is a
    schema nobody can roll back.
    """
    Base.metadata.create_all(engine or _default_engine())


def reset_engine_cache() -> None:
    _default_engine.cache_clear()
