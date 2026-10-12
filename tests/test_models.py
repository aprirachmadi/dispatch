from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from dispatch.mock_psa.config import load_settings
from dispatch.mock_psa.database import create_db_engine
from dispatch.mock_psa.models import Company, Contact, Note, SLATier, Ticket, TicketStatus


def test_schema_contains_phase_one_tables(engine):
    assert set(inspect(engine).get_table_names()) == {"companies", "contacts", "tickets", "notes"}


def test_relationships_and_utc_round_trip(engine):
    with Session(engine) as session:
        company = Company(name="Synthetic Example", sla_tier=SLATier.GOLD)
        contact = Contact(company=company, name="Demo User", email="demo@example.test")
        ticket = Ticket(
            company=company, contact=contact, subject="Email issue", description="Help!"
        )
        note = Note(ticket=ticket, author_name="Demo Technician", body="Checking mailbox settings.")
        session.add(note)
        session.commit()
        session.expire_all()
        assert ticket.company == company
        assert ticket.contact == contact
        assert ticket.notes == [note]
        assert ticket.status == TicketStatus.NEW
        assert ticket.created_at.tzinfo == UTC
        assert note.created_at.tzinfo == UTC


def test_foreign_keys_are_enforced(engine):
    with Session(engine) as session:
        session.add(Contact(company_id=999, name="Orphan", email="orphan@example.test"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_invalid_enum_is_rejected(engine):
    with Session(engine) as session:
        session.add(Company(name="Invalid tier", sla_tier="platinum"))
        with pytest.raises(StatementError):
            session.commit()


def test_naive_timestamp_is_rejected(engine):
    with Session(engine) as session:
        session.add(
            Ticket(
                company=Company(name="Demo", sla_tier=SLATier.BRONZE),
                subject="Test",
                description="Test",
                created_at=datetime(2026, 1, 1),
            )
        )
        with pytest.raises(StatementError, match="timezone-aware"):
            session.commit()


def test_only_sqlite_is_supported():
    with pytest.raises(ValueError, match="SQLite"):
        create_db_engine("postgresql://localhost/dispatch")


def test_environment_overrides_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DISPATCH_DATABASE_URL=sqlite:///from-file.db\n")
    monkeypatch.setenv("DISPATCH_DATABASE_URL", "sqlite:///from-env.db")
    assert load_settings().database_url == "sqlite:///from-env.db"


def test_dotenv_and_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DISPATCH_DATABASE_URL", raising=False)
    assert load_settings().database_url == "sqlite:///./data/dispatch.db"
    (tmp_path / ".env").write_text("DISPATCH_DATABASE_URL=sqlite:///from-file.db\n")
    assert load_settings().database_url == "sqlite:///from-file.db"
