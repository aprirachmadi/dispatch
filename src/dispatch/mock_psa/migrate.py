"""Explicit, backed-up SQLite schema upgrade. Stop all Dispatch processes first."""

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, inspect
from sqlalchemy.exc import SQLAlchemyError

from dispatch.mock_psa.database import Base, create_db_engine

SCHEMA_VERSION = 6
LEGACY_COLUMNS = {"id", "ticket_id", "body", "status", "created_at"}
INSTRUCTIONS = "Stop Dispatch, then run: poetry run python -m dispatch.mock_psa.migrate"


def check_schema(engine: Engine) -> None:
    """Fail before serving incompatible/future databases; never silently rebuild data."""
    from dispatch.mock_psa import models  # noqa: F401

    with engine.connect() as connection:
        version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
    if version not in (0, SCHEMA_VERSION):
        raise RuntimeError("Unsupported Dispatch database version. " + INSTRUCTIONS)
    inspector = inspect(engine)
    if version == 0 and inspector.get_table_names():
        raise RuntimeError("Dispatch database needs an explicit migration. " + INSTRUCTIONS)
    if version == SCHEMA_VERSION:
        expected = Base.metadata.tables
        if set(inspector.get_table_names()) != set(expected) or any(
            {column["name"] for column in inspector.get_columns(name)} != set(table.columns.keys())
            for name, table in expected.items()
        ):
            raise RuntimeError("Unrecognized Dispatch schema; restore/investigate before serving.")


def migrate(database_url: str) -> Path | None:
    """Upgrade known draft-only schema atomically, preserving records; return backup path."""
    from dispatch.mock_psa import models  # noqa: F401

    engine = create_db_engine(database_url)
    try:
        database = engine.url.database
        if database in (None, "", ":memory:") or not Path(database).is_file():
            raise ValueError("Migration requires an existing file-backed SQLite database")
        inspector = inspect(engine)
        with engine.connect() as connection:
            version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
        if version == SCHEMA_VERSION:
            check_schema(engine)
            return None
        if version != 0:
            raise ValueError("Unsupported database schema version; no changes made")
        tables = set(inspector.get_table_names())
        baseline = {"companies", "contacts", "tickets", "notes"}
        if tables and (
            tables not in (baseline, baseline | {"draft_replies"})
            or any(
                {column["name"] for column in inspector.get_columns(name)}
                != set(Base.metadata.tables[name].columns.keys())
                for name in baseline
            )
        ):
            raise ValueError("Unrecognized existing PSA schema; no changes made")
        legacy = inspector.has_table("draft_replies")
        if legacy:
            columns = {column["name"] for column in inspector.get_columns("draft_replies")}
            constraints = inspector.get_check_constraints("draft_replies")
            if columns != LEGACY_COLUMNS or not any(
                item["name"] == "draft_only_status" for item in constraints
            ):
                raise ValueError("Unrecognized draft schema; no changes made")
        backup = Path(str(database) + f".backup-{uuid4().hex}.sqlite3")
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        # SQLite backup includes committed WAL data; a filesystem copy may not.
        with (
            closing(sqlite3.connect(database)) as source,
            closing(sqlite3.connect(backup)) as target,
        ):
            source.backup(target)
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                if legacy:
                    connection.exec_driver_sql("ALTER TABLE draft_replies RENAME TO old_drafts")
                    # Avoid retaining the old index name on the replacement table.
                    connection.exec_driver_sql("DROP INDEX IF EXISTS ix_draft_replies_ticket_id")
                Base.metadata.create_all(connection)
                if legacy:
                    connection.exec_driver_sql(
                        "INSERT INTO draft_replies "
                        "(id,ticket_id,body,status,created_at,revision,updated_at,approved_at) "
                        "SELECT id,ticket_id,body,status,created_at,1,created_at,NULL "
                        "FROM old_drafts"
                    )
                    connection.exec_driver_sql("DROP TABLE old_drafts")
                if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
                    raise ValueError("Foreign key check failed; upgrade rolled back")
                connection.exec_driver_sql(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return backup
    finally:
        engine.dispose()


def main() -> None:
    from dispatch.mock_psa.config import load_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Override DATABASE_URL for this upgrade only")
    args = parser.parse_args()
    try:
        backup = migrate(args.database_url or load_settings().database_url)
    except (ValueError, RuntimeError, OSError, sqlite3.Error, SQLAlchemyError):
        parser.exit(
            1, "Migration stopped; check database/schema. Never reset or reseed to fix it.\n"
        )
    print(
        f"Upgraded to schema {SCHEMA_VERSION}. Backup: {backup}" if backup else "Already current."
    )


if __name__ == "__main__":
    main()
