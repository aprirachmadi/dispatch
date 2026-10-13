"""Additive technician reads; legacy REST and MCP response shapes stay unchanged."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import case, func, or_, select

from dispatch.mock_psa.assessments import saved_assessments
from dispatch.mock_psa.console_schemas import QueuePage, QueueTicket, RunPage, RunSummary
from dispatch.mock_psa.dependencies import CompanyFilter, DBSession, Limit, Offset, SearchQuery
from dispatch.mock_psa.models import (
    Company,
    DraftReply,
    Ticket,
    TicketCategory,
    TicketPriority,
    TicketStatus,
    TriageRun,
)

router = APIRouter()
QUEUE_SCAN_LIMIT = 2000


@router.get("/console/config")
def config(request: Request) -> dict[str, bool]:
    """Public local-demo policy only; not model liveness, identity, or credentials."""
    return {"read_only": request.app.state.read_only}


@router.get("/console/tickets", response_model=QueuePage)
def queue(
    session: DBSession,
    query: SearchQuery = None,
    company_id: CompanyFilter = None,
    category: TicketCategory | None = None,
    priority: TicketPriority | None = None,
    status: TicketStatus | None = None,
    attention: Literal["required", "not_flagged", "not_assessed"] | None = None,
    urgency: Literal["immediate", "same_day", "routine", "not_assessed"] | None = None,
    limit: Limit = 25,
    offset: Offset = 0,
):
    """Filter saved attention/urgency before pagination; fail beyond bounded scan."""
    statement = select(Ticket, Company.name).join(Company).order_by(Ticket.id)
    if query is not None:
        text = query.strip()
        if not text:
            raise HTTPException(422, "Search query must not be blank")
        statement = statement.where(
            or_(
                Ticket.subject.icontains(text, autoescape=True),
                Ticket.description.icontains(text, autoescape=True),
            )
        )
    for column, value in [
        (Ticket.company_id, company_id),
        (Ticket.category, category),
        (Ticket.priority, priority),
        (Ticket.status, status),
    ]:
        if value is not None:
            statement = statement.where(column == value)
    records = session.execute(statement.limit(QUEUE_SCAN_LIMIT + 1)).all()
    if len(records) > QUEUE_SCAN_LIMIT:
        raise HTTPException(503, "Queue scan exceeds 2000 tickets; narrow the filters")
    assessments = saved_assessments(session, [ticket.id for ticket, _ in records])
    filtered = []
    for ticket, company_name in records:
        saved = assessments.get(ticket.id)
        state = (
            "not_assessed"
            if saved is None
            else "required"
            if saved.requires_attention
            else "not_flagged"
        )
        if attention is not None and attention != state:
            continue
        saved_urgency = "not_assessed" if saved is None else saved.assessment.urgency
        if urgency is not None and urgency != saved_urgency:
            continue
        filtered.append((ticket, company_name, saved))
    page = filtered[offset : offset + limit]
    ids = [ticket.id for ticket, _, _ in page]
    counts = {
        row.ticket_id: row
        for row in session.execute(
            select(
                DraftReply.ticket_id,
                func.count().label("total"),
                func.sum(case((DraftReply.status == "approved", 1), else_=0)).label("approved"),
            )
            .where(DraftReply.ticket_id.in_(ids))
            .group_by(DraftReply.ticket_id)
        )
    }
    next_offset = offset + limit if offset + limit < len(filtered) else None
    return QueuePage(
        tickets=[
            QueueTicket(
                id=ticket.id,
                company_id=ticket.company_id,
                company_name=company_name,
                subject=ticket.subject,
                category=ticket.category,
                priority=ticket.priority,
                status=ticket.status,
                created_at=ticket.created_at,
                requires_attention=None if saved is None else saved.requires_attention,
                urgency=None if saved is None else saved.assessment.urgency,
                assessment_draft_id=None if saved is None else saved.draft_id,
                draft_count=counts[ticket.id].total if ticket.id in counts else 0,
                approved_count=counts[ticket.id].approved if ticket.id in counts else 0,
            )
            for ticket, company_name, saved in page
        ],
        total=len(filtered),
        limit=limit,
        offset=offset,
        next_offset=next_offset,
    )


@router.get("/triage/runs", response_model=RunPage)
def runs(session: DBSession, limit: Limit = 25, offset: Offset = 0):
    """Newest-first persisted summaries; no generated text or journal payloads."""
    records = session.scalars(
        select(TriageRun)
        .order_by(TriageRun.started_at.desc(), TriageRun.id.desc())
        .limit(limit + 1)
        .offset(offset)
    ).all()
    more = len(records) > limit
    return RunPage(
        runs=[RunSummary.model_validate(record) for record in records[:limit]],
        limit=limit,
        offset=offset,
        next_offset=offset + limit if more and offset + limit <= 10_000 else None,
        scan_truncated=more and offset + limit > 10_000,
    )
