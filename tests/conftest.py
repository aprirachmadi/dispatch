"""Each test gets an isolated SQLite database; never use the demo database."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from dispatch.mock_psa.config import Settings
from dispatch.mock_psa.database import create_db_engine, create_schema
from dispatch.mock_psa.main import create_app
from dispatch.mock_psa.models import Company, Contact, Note, SLATier, Ticket, TicketCategory


@pytest.fixture
def anyio_backend():
    # Async clients and MCP subprocess tests use asyncio; no Trio dependency needed.
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_mcp_environment(monkeypatch, tmp_path):
    """Tests never append to the user's audit file or enable writes via their .env."""
    monkeypatch.setenv("DISPATCH_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("DISPATCH_READ_ONLY", "true")


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'test.db'}")
    create_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    engine = create_db_engine(database_url)
    create_schema(engine)
    with Session(engine) as session:
        first = Company(id=1, name="Synthetic Maple", sla_tier=SLATier.GOLD)
        second = Company(id=2, name="Synthetic Brook", sla_tier=SLATier.BRONZE)
        contact = Contact(id=1, company=first, name="Demo Alex", email="alex@maple.example.test")
        other = Contact(id=2, company=second, name="Demo Sam", email="sam@brook.example.test")
        ticket = Ticket(
            id=1,
            company=first,
            contact=contact,
            subject="Cannot open email",
            description="keeps asking for my password??",
            category=TicketCategory.EMAIL,
        )
        note = Note(
            ticket=ticket, author_name="Demo Technician", body="Investigating sign-in logs."
        )
        session.add_all([other, note])
        session.commit()
    engine.dispose()
    with TestClient(create_app(Settings(database_url=database_url))) as test_client:
        yield test_client


@pytest.fixture
def write_client(client):
    """Reuse isolated fixture records through an app explicitly allowing tool writes."""
    url = client.app.state.session_factory.kw["bind"].url.render_as_string()
    with TestClient(create_app(Settings(database_url=url, read_only=False))) as writable:
        yield writable
