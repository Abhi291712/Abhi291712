"""Database engine, session factory and the shared declarative base for ORM models.

The engine is created from ``DATABASE_URL`` so the same code runs on SQLite (default, zero
setup) and PostgreSQL (production). The session factory is stored on ``app.state`` rather than
in a module-level global, which lets each test create an app bound to its own test database.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import DateTime, Engine, MetaData, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    """Base class for all ORM models; collects table metadata for migrations and create_all()."""

    # Predictable constraint names ("uq_calls_idempotency_key" instead of a random name chosen
    # by the database) let Alembic migrations refer to, alter and drop constraints reliably.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime (used for defaults and comparisons)."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Stores datetimes as naive UTC and always returns timezone-aware UTC datetimes.

    SQLite has no native timezone support, so without this type a value written as
    "10:00+00:00" would be read back as a naive "10:00" and comparisons would break.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:  # Treat naive input as UTC rather than guessing a local zone.
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def build_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine with settings appropriate for the database backend."""
    connect_args = {}
    if database_url.startswith("sqlite"):
        # SQLite connections are bound to one thread by default; FastAPI runs sync endpoints
        # and background tasks in a thread pool, so this check must be disabled.
        connect_args["check_same_thread"] = False
    # pool_pre_ping transparently replaces connections the database server has dropped.
    return create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory. expire_on_commit=False keeps objects readable after commit."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all tables that do not exist yet. Used in development and tests only;
    production applies the Alembic migrations in `migrations/` instead."""
    # Import models so their tables are registered on Base.metadata before create_all runs.
    from app.models import appointment, call, event  # noqa: F401

    Base.metadata.create_all(engine)


def ping_database(session: Session) -> bool:
    """Run a trivial query to confirm the database is reachable."""
    try:
        session.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False


def get_session_factory(request: Request) -> sessionmaker[Session]:
    """FastAPI dependency: the session factory belonging to the running app."""
    return request.app.state.session_factory


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one database session per request, always closed afterwards."""
    session = get_session_factory(request)()
    try:
        yield session
    finally:
        session.close()
