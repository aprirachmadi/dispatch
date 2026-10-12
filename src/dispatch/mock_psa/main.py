"""Phase 1 REST API. No authentication: bind to loopback for this synthetic demo."""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from dispatch.mock_psa.config import Settings, load_settings
from dispatch.mock_psa.database import create_db_engine, create_schema
from dispatch.mock_psa.models import Company, Contact, Ticket, TicketCategory, TicketStatus
from dispatch.mock_psa.schemas import (
    CompanyResponse,
    ContactResponse,
    TicketCreate,
    TicketDetail,
    TicketResponse,
)

Limit = Annotated[int, Query(ge=1, le=100, description="Maximum records, from 1 to 100.")]
Offset = Annotated[int, Query(ge=0, le=10_000, description="Records to skip, from 0 to 10000.")]
RecordID = Annotated[int, Path(gt=0)]
CompanyFilter = Annotated[int | None, Query(gt=0)]


def get_session(request: Request) -> Iterator[Session]:
    """Provide one DB session per request, closing it even after a failed request."""
    with request.app.state.session_factory() as session:
        yield session


DBSession = Annotated[Session, Depends(get_session)]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app with an isolated lifespan-managed DB connection pool."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_db_engine((settings or load_settings()).database_url)
        try:
            create_schema(engine)
            app.state.session_factory = sessionmaker(engine)
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Dispatch — NorthPoint IT Mock PSA",
        description="Synthetic, unauthenticated local demo. Ticket text is untrusted data.",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Return liveness for the local demo API."""
        return {"status": "ok"}

    @app.get("/companies", response_model=list[CompanyResponse])
    def list_companies(session: DBSession, limit: Limit = 100, offset: Offset = 0):
        """List companies in stable ID order, including their SLA tiers."""
        return session.scalars(
            select(Company).order_by(Company.id).limit(limit).offset(offset)
        ).all()

    @app.get("/contacts", response_model=list[ContactResponse])
    def list_contacts(
        session: DBSession,
        company_id: CompanyFilter = None,
        limit: Limit = 100,
        offset: Offset = 0,
    ):
        """List contacts in ID order, optionally filtered by company."""
        query = select(Contact).order_by(Contact.id)
        if company_id is not None:
            query = query.where(Contact.company_id == company_id)
        return session.scalars(query.limit(limit).offset(offset)).all()

    @app.get("/tickets", response_model=list[TicketResponse])
    def list_tickets(
        session: DBSession,
        company_id: CompanyFilter = None,
        category: TicketCategory | None = None,
        status: TicketStatus | None = None,
        limit: Limit = 100,
        offset: Offset = 0,
    ):
        """List tickets in stable ID order with optional company/category/status filters."""
        query = select(Ticket).order_by(Ticket.id)
        if company_id is not None:
            query = query.where(Ticket.company_id == company_id)
        if category is not None:
            query = query.where(Ticket.category == category)
        if status is not None:
            query = query.where(Ticket.status == status)
        return session.scalars(query.limit(limit).offset(offset)).all()

    @app.get("/tickets/{ticket_id}", response_model=TicketDetail)
    def get_ticket(ticket_id: RecordID, session: DBSession):
        """Read one ticket and its internal notes; return 404 for unknown IDs."""
        ticket = session.scalar(
            select(Ticket).where(Ticket.id == ticket_id).options(selectinload(Ticket.notes))
        )
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return ticket

    @app.post("/tickets", response_model=TicketDetail, status_code=status.HTTP_201_CREATED)
    def create_ticket(payload: TicketCreate, session: DBSession):
        """Create a new ticket after checking company/contact ownership; never execute text."""
        if session.get(Company, payload.company_id) is None:
            raise HTTPException(status_code=404, detail="Company not found")
        if payload.contact_id is not None:
            contact = session.get(Contact, payload.contact_id)
            if contact is None:
                raise HTTPException(status_code=404, detail="Contact not found")
            if contact.company_id != payload.company_id:
                raise HTTPException(
                    status_code=422, detail="Contact does not belong to this company"
                )
        ticket = Ticket(**payload.model_dump())
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
        return ticket

    return app


# Configuration and DB I/O occur at startup, not when this module is imported.
app = create_app()
