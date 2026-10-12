"""Technician-only demo REST operations. No identity authentication or sending path."""

from typing import Annotated, Literal
from uuid import UUID

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, update

from dispatch.mcp_server.providers.base import ProviderError
from dispatch.mcp_server.providers.mock_psa import MockPSAProvider
from dispatch.mcp_server.services.models import WeeklyReport, WeekStart
from dispatch.mcp_server.services.reports import ReportService
from dispatch.mock_psa.assessments import saved_assessments
from dispatch.mock_psa.audit_reader import read_mcp_events
from dispatch.mock_psa.console_schemas import (
    ApproveDraft,
    EditDraft,
    ReviewDraft,
    RunResponse,
    SavedAssessment,
    StartTriage,
)
from dispatch.mock_psa.database import utc_now
from dispatch.mock_psa.dependencies import DBSession, Limit, Offset, RecordID, require_write_mode
from dispatch.mock_psa.models import BackendAudit, DraftReply, Ticket, TriageRun
from dispatch.mock_psa.run_manager import run_response

router = APIRouter()


def require_ticket(session, ticket_id):
    if session.get(Ticket, ticket_id) is None:
        raise HTTPException(404, "Ticket not found")


def require_draft(session, ticket_id, draft_id):
    require_ticket(session, ticket_id)
    draft = session.get(DraftReply, draft_id)
    if draft is None or draft.ticket_id != ticket_id:
        raise HTTPException(404, "Draft not found for this ticket")
    return draft


@router.get("/tickets/{ticket_id}/drafts", response_model=list[ReviewDraft])
def drafts(ticket_id: RecordID, session: DBSession, limit: Limit = 100, offset: Offset = 0):
    """List this ticket's unsent drafts/approved reviews, oldest first, at most 100."""
    require_ticket(session, ticket_id)
    return session.scalars(
        select(DraftReply)
        .where(DraftReply.ticket_id == ticket_id)
        .order_by(DraftReply.id)
        .limit(limit)
        .offset(offset)
    ).all()


def mutate(session, ticket_id, draft_id, payload, approve):
    require_draft(session, ticket_id, draft_id)
    now = utc_now()
    values = {"revision": payload.revision + 1, "updated_at": now}
    if approve:
        values.update(status="approved", approved_at=now)
    else:
        values["body"] = payload.body
    changed = session.execute(
        update(DraftReply)
        .where(
            DraftReply.id == draft_id,
            DraftReply.ticket_id == ticket_id,
            DraftReply.status == "draft",
            DraftReply.revision == payload.revision,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        session.rollback()
        raise HTTPException(409, "Draft changed or is approved; reload before reviewing")
    session.add(
        BackendAudit(
            action="draft_approved" if approve else "draft_edited",
            ticket_id=ticket_id,
            draft_id=draft_id,
            revision=payload.revision + 1,
        )
    )
    session.commit()  # Mutation + audit are atomic; never send anything.
    session.expire_all()
    return session.get(DraftReply, draft_id)


@router.patch(
    "/tickets/{ticket_id}/drafts/{draft_id}",
    response_model=ReviewDraft,
    dependencies=[Depends(require_write_mode)],
)
def edit(ticket_id: RecordID, draft_id: RecordID, payload: EditDraft, session: DBSession):
    """Edit text at the reviewed revision; approved drafts and stale edits get 409."""
    return mutate(session, ticket_id, draft_id, payload, False)


@router.post(
    "/tickets/{ticket_id}/drafts/{draft_id}/approve",
    response_model=ReviewDraft,
    dependencies=[Depends(require_write_mode)],
)
def approve(ticket_id: RecordID, draft_id: RecordID, payload: ApproveDraft, session: DBSession):
    """Record technician approval of exactly this revision. Approval never sends a reply."""
    return mutate(session, ticket_id, draft_id, payload, True)


@router.get("/tickets/{ticket_id}/assessment", response_model=SavedAssessment)
def assessment(ticket_id: RecordID, session: DBSession):
    """Project latest valid completed agent note linked to this ticket's draft; else 404."""
    require_ticket(session, ticket_id)
    saved = saved_assessments(session, [ticket_id]).get(ticket_id)
    if saved is not None:
        return saved
    raise HTTPException(404, "No valid saved assessment found in the latest 100 agent notes")


@router.post("/triage/runs", response_model=RunResponse, status_code=202)
async def start_run(payload: StartTriage, request: Request, session: DBSession):
    """Start one background triage job; default preview. Settings stay server-side."""
    if payload.ticket_id is not None:
        require_ticket(session, payload.ticket_id)
    return await request.app.state.run_manager.start(payload)


@router.get("/triage/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: UUID, session: DBSession):
    """Poll a persisted job including per-ticket partial/uncertain outcomes; never retry."""
    record = session.get(TriageRun, str(run_id))
    if record is None:
        raise HTTPException(404, "Triage run not found")
    return run_response(record)


@router.get("/audit-log")
def audit(
    request: Request,
    session: DBSession,
    source: Literal["backend", "mcp"] = "backend",
    limit: Limit = 100,
    offset: Offset = 0,
):
    """Page metadata oldest-first per source, not merged; no prompts, bodies, keys or paths."""
    if source == "mcp":
        try:
            page = read_mcp_events(request.app.state.mcp_settings.audit_path, limit, offset)
        except OSError:
            raise HTTPException(503, "MCP audit log is unavailable") from None
    else:
        records = session.scalars(
            select(BackendAudit).order_by(BackendAudit.id).offset(offset).limit(limit + 1)
        ).all()
        page = {
            "events": [
                {
                    "source": "backend",
                    "id": item.id,
                    "action": item.action,
                    "ticket_id": item.ticket_id,
                    "draft_id": item.draft_id,
                    "revision": item.revision,
                    "run_id": item.run_id,
                    "timestamp": item.created_at,
                }
                for item in records[:limit]
            ],
            "next_offset": (
                offset + limit if len(records) > limit and offset + limit <= 10_000 else None
            ),
            "scan_truncated": len(records) > limit and offset + limit > 10_000,
            "invalid_lines": 0,
        }
    return {"source": source, "limit": limit, "offset": offset, **page}


@router.get("/reports/weekly", response_model=WeeklyReport)
async def weekly(
    request: Request,
    week_start: WeekStart,
    company_id: Annotated[int | None, Query(gt=0, le=2**63 - 1)] = None,
):
    """UTC Monday-based counts with current backlog, sharing the existing MCP report service."""
    # In-process REST adapter avoids loopback network/port dependence, not a second calculator.
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=request.app), base_url="http://dispatch.internal/"
    ) as client:
        try:
            return await ReportService().weekly(MockPSAProvider(client), week_start, company_id)
        except ProviderError:
            raise HTTPException(
                503, "Report unavailable or ticket scan exceeded its safety limit"
            ) from None
