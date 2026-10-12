"""Explicit migration never resets synthetic records and keeps a recoverable backup."""

import sqlite3
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dispatch.mock_psa.database import Base, create_db_engine, create_schema
from dispatch.mock_psa.migrate import migrate
from dispatch.mock_psa.models import BackendAudit, Company, Contact, DraftReply, Note, Ticket


def legacy_db(tmp_path):
    path = tmp_path / "old.db"
    url = f"sqlite:///{path}"
    engine = create_db_engine(url)
    Base.metadata.create_all(
        engine, tables=[Company.__table__, Contact.__table__, Ticket.__table__, Note.__table__]
    )
    when = datetime(2026, 10, 1, tzinfo=UTC)
    with Session(engine) as session:
        company = Company(id=4, name="Synthetic preserved", sla_tier="silver")
        ticket = Ticket(
            id=17,
            company=company,
            subject="Preserved",
            description="Messy original",
            created_at=when,
            updated_at=when,
        )
        note = Note(
            id=9,
            ticket=ticket,
            body="Keep original",
            author_name="Demo Technician",
            created_at=when,
        )
        session.add(note)
        session.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE draft_replies (id INTEGER PRIMARY KEY, "
            "ticket_id INTEGER NOT NULL REFERENCES tickets(id), body TEXT NOT NULL, "
            "status VARCHAR(20) NOT NULL DEFAULT 'draft', created_at DATETIME NOT NULL, "
            "CONSTRAINT draft_only_status CHECK (status = 'draft'))"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_draft_replies_ticket_id ON draft_replies(ticket_id)"
        )
        connection.exec_driver_sql(
            "INSERT INTO draft_replies VALUES "
            "(5,17,'Original unsent text','draft','2026-10-01 00:00:00.000000')"
        )
    engine.dispose()
    return path, url


def test_backup_and_preserving_upgrade(tmp_path):
    path, url = legacy_db(tmp_path)
    with sqlite3.connect(path) as connection:
        original = {
            name: connection.execute(f"SELECT * FROM {name}").fetchall()
            for name in ("companies", "contacts", "tickets", "notes", "draft_replies")
        }
    backup = migrate(url)
    assert backup.is_file() and backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        for name, values in original.items():
            assert connection.execute(f"SELECT * FROM {name}").fetchall() == values
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        for name in ("companies", "contacts", "tickets", "notes"):
            assert connection.execute(f"SELECT * FROM {name}").fetchall() == original[name]
    engine = create_db_engine(url)
    create_schema(engine)
    with Session(engine) as session:
        draft = session.get(DraftReply, 5)
        assert draft.body == "Original unsent text" and draft.revision == 1
        assert draft.updated_at == draft.created_at
        draft.status = "approved"
        draft.approved_at = datetime.now(UTC)
        session.add(BackendAudit(action="draft_approved", draft_id=5, ticket_id=17, revision=1))
        session.commit()
    assert migrate(url) is None
    assert len(list(tmp_path.glob("*.backup-*.sqlite3"))) == 1
    engine.dispose()


def test_server_demands_migration_without_mutating(tmp_path):
    path, url = legacy_db(tmp_path)
    before = path.read_bytes()
    engine = create_db_engine(url)
    with pytest.raises(RuntimeError, match="dispatch.mock_psa.migrate"):
        create_schema(engine)
    assert path.read_bytes() == before
    assert "triage_runs" not in inspect(engine).get_table_names()
    engine.dispose()


def test_upgrade_rolls_back_integrity_failure(tmp_path):
    path, url = legacy_db(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE draft_replies SET ticket_id=999")
    with pytest.raises(
        IntegrityError
    ):  # SQLite insert FK enforcement prevents copying the corrupt row.
        migrate(url)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert {row[1] for row in connection.execute("PRAGMA table_info(draft_replies)")} == {
            "id",
            "ticket_id",
            "body",
            "status",
            "created_at",
        }
        assert connection.execute("SELECT ticket_id FROM draft_replies").fetchone()[0] == 999
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name='old_drafts'").fetchall()
            == []
        )
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name='triage_runs'").fetchall()
            == []
        )
    assert len(list(tmp_path.glob("*.backup-*.sqlite3"))) == 1


def test_future_version_refused(tmp_path):
    path, url = legacy_db(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="Unsupported"):
        migrate(url)
    engine = create_db_engine(url)
    with pytest.raises(RuntimeError, match="Unsupported"):
        create_schema(engine)
    engine.dispose()
    assert list(tmp_path.glob("*.backup-*.sqlite3")) == []


def test_unrecognized_schema_not_backed_up_or_changed(tmp_path):
    path, url = legacy_db(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE draft_replies ADD COLUMN surprise TEXT")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unrecognized"):
        migrate(url)
    assert before == path.read_bytes()
    assert list(tmp_path.glob("*.backup-*.sqlite3")) == []


def test_unrecognized_psa_columns_refused(tmp_path):
    path, url = legacy_db(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE tickets ADD COLUMN surprise TEXT")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unrecognized"):
        migrate(url)
    assert before == path.read_bytes()
    assert list(tmp_path.glob("*.backup-*.sqlite3")) == []


def test_non_dispatch_database_not_upgraded(tmp_path):
    path = tmp_path / "other.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unrecognized"):
        migrate(f"sqlite:///{path}")
    assert before == path.read_bytes()


def test_current_schema_missing_table_fails_before_serving(tmp_path):
    url = f"sqlite:///{tmp_path / 'current.db'}"
    engine = create_db_engine(url)
    create_schema(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE triage_runs")
    with pytest.raises(RuntimeError, match="Unrecognized"):
        create_schema(engine)
    assert "triage_runs" not in inspect(engine).get_table_names()
    engine.dispose()
