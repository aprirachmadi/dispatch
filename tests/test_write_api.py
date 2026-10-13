import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dispatch.mcp_server.providers.base import ProviderError, ReadOnlyError
from dispatch.mcp_server.providers.mock_psa import MockPSAProvider
from dispatch.mcp_server.providers.models import TicketChanges
from dispatch.mock_psa.database import Base, create_db_engine, create_schema
from dispatch.mock_psa.migrate import migrate
from dispatch.mock_psa.models import Company, DraftReply, Note, Ticket
from dispatch.settings import parse_read_only


def test_read_only_default_blocks_tool_writes_but_not_intake(client):
    for method, path, payload in (
        ("POST", "/tickets/1/drafts", {"body": "Reply"}),
        ("POST", "/tickets/1/notes", {"body": "Note"}),
        ("PATCH", "/tickets/1", {"priority": "high"}),
    ):
        assert client.request(method, path, json=payload).status_code == 403
    assert len(client.get("/tickets/1").json()["notes"]) == 1
    assert (
        client.post(
            "/tickets",
            json={"company_id": 1, "subject": "Synthetic intake", "description": "Still allowed"},
        ).status_code
        == 201
    )


def test_draft_is_persisted_as_draft_and_note_is_internal(write_client):
    draft = write_client.post("/tickets/1/drafts", json={"body": "  Please call IT.  "})
    assert draft.status_code == 201
    assert draft.json()["body"] == "Please call IT."
    assert draft.json()["status"] == "draft"
    note = write_client.post("/tickets/1/notes", json={"body": "Suspected compromised password."})
    assert note.status_code == 201
    assert note.json()["author_name"] == "Dispatch MCP"
    assert write_client.get("/tickets/1").json()["notes"][-1]["body"] == note.json()["body"]
    engine = write_client.app.state.session_factory.kw["bind"]
    with Session(engine) as session:
        saved = session.get(DraftReply, draft.json()["id"])
        assert saved.status == "draft"
        assert saved.ticket_id == 1


def test_updates_only_supplied_classification_fields(write_client):
    before = write_client.get("/tickets/1").json()
    response = write_client.patch("/tickets/1", json={"category": "security", "priority": "urgent"})
    assert response.status_code == 200
    assert response.json()["category"] == "security"
    assert response.json()["priority"] == "urgent"
    write_client.patch("/tickets/1", json={"priority": "high"})
    after = write_client.get("/tickets/1").json()
    assert after["category"] == "security"
    for field in ("status", "subject", "description", "company_id", "contact_id", "resolved_at"):
        assert after[field] == before[field]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"priority": None},
        {"category": None},
        {"priority": "critical"},
        {"category": "billing"},
        {"status": "closed"},
        {"subject": "Edited"},
        {"priority": "high", "company_id": 2},
    ],
)
def test_invalid_update_never_changes_ticket(write_client, payload):
    before = write_client.get("/tickets/1").json()
    assert write_client.patch("/tickets/1", json=payload).status_code == 422
    assert write_client.get("/tickets/1").json() == before


@pytest.mark.parametrize("resource", ["notes", "drafts"])
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"body": ""},
        {"body": "   "},
        {"body": "x" * 10001},
        {"body": "OK", "status": "approved"},
        {"body": "OK", "author_name": "Impersonation"},
    ],
)
def test_invalid_body_write(write_client, resource, payload):
    assert write_client.post(f"/tickets/1/{resource}", json=payload).status_code == 422


def test_missing_ticket_and_no_approval_route(write_client):
    assert write_client.post("/tickets/999/drafts", json={"body": "Hi"}).status_code == 404
    assert write_client.post("/tickets/999/notes", json={"body": "Hi"}).status_code == 404
    assert write_client.patch("/tickets/999", json={"priority": "high"}).status_code == 404
    assert write_client.post("/drafts/1/approve").status_code == 404


def test_database_rejects_approval_without_timestamp(write_client):
    engine = write_client.app.state.session_factory.kw["bind"]
    with Session(engine) as session:
        session.add(DraftReply(ticket_id=1, body="Test", status="approved"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_phase_two_database_upgrade_preserves_ticket(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = create_db_engine(url)
    try:
        Base.metadata.create_all(
            engine,
            tables=[
                Company.__table__,
                Ticket.__table__,
                Note.__table__,
                Base.metadata.tables["contacts"],
            ],
        )
        with Session(engine) as session:
            company = Company(name="Preserved synthetic company", sla_tier="gold")
            ticket = Ticket(company=company, subject="Preserved", description="Original")
            session.add(ticket)
            session.commit()
        with pytest.raises(RuntimeError, match="explicit migration"):
            create_schema(engine)
        assert migrate(url).is_file()
        create_schema(engine)
        with Session(engine) as session:
            assert session.get(Ticket, 1).subject == "Preserved"
            assert session.scalars(select(DraftReply)).all() == []
    finally:
        engine.dispose()


@pytest.mark.parametrize("value", ["", "1", "0", "yes", "no", "typo"])
def test_read_only_invalid_configuration_fails_closed(value):
    with pytest.raises(ValueError, match="true or false"):
        parse_read_only(value)


def test_read_only_explicit_values():
    assert parse_read_only("true") is True
    assert parse_read_only("FALSE") is False


@pytest.mark.anyio
async def test_provider_writes_only_when_both_layers_allow(write_client, client):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=write_client.app), base_url="http://psa.test/"
    ) as http:
        denied = MockPSAProvider(http)
        with pytest.raises(ReadOnlyError):
            await denied.draft_reply(1, "Reply")
        enabled = MockPSAProvider(http, read_only=False)
        draft = await enabled.draft_reply(1, "Reply")
        assert draft.status == "draft"
        note = await enabled.add_internal_note(1, "Internal")
        assert note.author_name == "Dispatch MCP"
        ticket = await enabled.update_ticket(1, TicketChanges(priority="urgent"))
        assert ticket.priority == "urgent" and ticket.status == "new"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=client.app), base_url="http://psa.test/"
    ) as http:
        with pytest.raises(ReadOnlyError):
            await MockPSAProvider(http, read_only=False).draft_reply(1, "Reply")


@pytest.mark.anyio
async def test_timed_out_write_not_retried():
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx2.ReadTimeout("SECRET", request=request)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), base_url="http://psa.test/"
    ) as http:
        with pytest.raises(ProviderError, match="outcome is unknown"):
            await MockPSAProvider(http, read_only=False).draft_reply(1, "Reply")
    assert len(requests) == 1
