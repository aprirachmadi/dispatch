"""SQLite connections, schema creation, and UTC timestamp handling."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC in SQLite and restore timezone-aware values on reads."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamps must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


def utc_now() -> datetime:
    return datetime.now(UTC)


def create_db_engine(database_url: str) -> Engine:
    """Create a SQLite engine with foreign keys enforced on every connection."""
    url = make_url(database_url)
    if url.drivername != "sqlite":
        raise ValueError("Phase 1 supports SQLite URLs only (sqlite:///...)")
    memory = url.database in (None, "", ":memory:")
    if not memory:
        Path(url.database).resolve().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False},
        **({"poolclass": StaticPool} if memory else {}),
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Any, _: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def create_schema(engine: Engine) -> None:
    """Create missing tables; this does not migrate existing schemas."""
    from dispatch.mock_psa import models  # noqa: F401

    Base.metadata.create_all(engine)
