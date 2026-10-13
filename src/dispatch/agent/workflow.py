"""Code-selected, bounded MCP workflow; no arbitrary model-selected tool execution."""

import json
from collections.abc import Callable

from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from dispatch.agent.llm import LLMError, ModelClient
from dispatch.agent.models import History, Operation, Problem, TriageResult
from dispatch.agent.prompts import classify, draft_response
from dispatch.domain import TicketStatus
from dispatch.mcp_server.providers.models import (
    CompanyContext,
    DraftReplyRecord,
    NoteRecord,
    TicketDetail,
    TicketSearchResult,
    TicketSummary,
)
from dispatch.mcp_server.services.models import SimilarityResult

NOTE_PREFIX = "Dispatch triage v1 "
MAX_QUEUE_SCAN = 100


class WorkflowError(Exception):
    """Safe, deliberate workflow failure; never a raw tool/provider response."""


class MCPGateway:
    def __init__(self, client, *, writes_allowed: bool = False):
        self.client = client
        self.writes_allowed = writes_allowed

    async def call(self, name, arguments, schema):
        allowed_reads = {
            "search_tickets",
            "get_ticket",
            "get_company_context",
            "find_similar_tickets",
        }
        allowed_writes = {"update_ticket", "draft_reply", "add_internal_note"}
        if name not in allowed_reads and not (self.writes_allowed and name in allowed_writes):
            raise WorkflowError("Agent attempted a disallowed tool or write mode is disabled.")
        try:
            result = await self.client.call_tool(name, arguments)
            if result.is_error or result.structured_content is None:
                raise WorkflowError(
                    f"MCP tool {name} failed; check backend, mode, model cache and audit."
                )
            return schema.model_validate(result.structured_content)
        except (MCPError, ValidationError, TimeoutError, OSError):
            raise WorkflowError(
                f"MCP tool {name} could not be confirmed; inspect the audit log."
            ) from None

    async def get_ticket(self, ticket_id: int) -> TicketDetail:
        ticket = await self.call("get_ticket", {"ticket_id": ticket_id}, TicketDetail)
        if ticket.id != ticket_id:
            raise WorkflowError("Backend returned a different ticket ID.")
        return ticket

    async def problem(self, ticket: TicketDetail) -> Problem:
        context = await self.call(
            "get_company_context", {"company_id": ticket.company_id}, CompanyContext
        )
        if context.company.id != ticket.company_id:
            raise WorkflowError("Backend returned a different company ID.")
        # Explicit projection: never show the classifier existing labels or internal notes.
        return Problem(
            subject=ticket.subject,
            description=ticket.description,
            company_name=context.company.name,
            sla_tier=context.company.sla_tier,
            created_at=ticket.created_at,
        )

    async def history(self, ticket: TicketDetail) -> list[History]:
        result = await self.call(
            "find_similar_tickets", {"ticket_id": ticket.id, "limit": 3}, SimilarityResult
        )
        if result.source_ticket_id != ticket.id or result.history_cutoff != ticket.created_at:
            raise WorkflowError("History returned an inconsistent source or cutoff.")
        history = []
        seen = {ticket.id}
        for match in result.matches:
            prior = await self.get_ticket(match.ticket.id)
            if (
                prior.id in seen
                or prior.status not in {TicketStatus.RESOLVED, TicketStatus.CLOSED}
                or prior.resolved_at is None
                or prior.resolved_at.tzinfo is None
                or prior.resolved_at.utcoffset() is None
                or prior.resolved_at >= ticket.created_at
            ):
                raise WorkflowError("History was not a unique, strictly prior resolution.")
            seen.add(prior.id)
            # Do not expose later notes or ambiguous truncated note collections to drafting.
            notes = (
                []
                if prior.notes_truncated
                else [
                    note.body[:1500]
                    for note in prior.notes
                    if note.created_at.tzinfo is not None
                    and note.created_at.utcoffset() is not None
                    and note.created_at < ticket.created_at
                ][-3:]
            )
            history.append(
                History(
                    ticket_id=prior.id,
                    subject=prior.subject,
                    description=prior.description[:2000],
                    resolved_at=prior.resolved_at,
                    notes=notes,
                )
            )
        return history


def skip_reason(ticket: TicketDetail) -> str | None:
    if ticket.status != TicketStatus.NEW:
        return "Ticket is no longer new."
    if ticket.notes_truncated:
        return "Notes are truncated; inspect manually before triaging again."
    for note in ticket.notes:
        if note.author_name == "Dispatch MCP" and note.body.startswith(NOTE_PREFIX):
            try:
                marker = json.loads(note.body[len(NOTE_PREFIX) :])
                if (
                    isinstance(marker, dict)
                    and marker.get("state") == "completed"
                    and type(marker.get("draft_id")) is int
                    and marker["draft_id"] > 0
                ):
                    return "Already triaged: a completed assessment note is present."
            except (ValueError, RecursionError):
                pass
    return None


async def triage_ticket(
    gateway: MCPGateway,
    model: ModelClient,
    ticket: TicketDetail,
    *,
    apply: bool = False,
    progress: Callable[[TriageResult], None] | None = None,
) -> TriageResult:
    """Generate before writing; journal each mutation intent/result and stop on any failure."""
    result = TriageResult(ticket_id=ticket.id, state="failed")
    reason = skip_reason(ticket)
    if reason:
        return result.model_copy(update={"state": "skipped", "message": reason})
    if apply and not gateway.writes_allowed:
        raise WorkflowError("Apply mode requires explicitly enabled writes.")
    try:
        problem = await gateway.problem(ticket)
        assessment = await classify(model, problem)
        result = result.model_copy(update={"assessment": assessment})
        history = await gateway.history(ticket)
        result = result.model_copy(update={"history_ids": [item.ticket_id for item in history]})
        draft = await draft_response(model, problem, assessment, history)
        result = result.model_copy(update={"draft": draft, "state": "preview"})
        if not apply:
            return result
        latest = await gateway.get_ticket(ticket.id)
        if latest.model_dump() != ticket.model_dump():
            raise WorkflowError("Ticket changed while generating; no mutations attempted.")
        draft_id = None
        for name, arguments, schema in [
            (
                "update_ticket",
                {
                    "ticket_id": ticket.id,
                    "category": assessment.category.value,
                    "priority": assessment.priority.value,
                },
                TicketSummary,
            ),
            ("draft_reply", {"ticket_id": ticket.id, "body": draft.body}, DraftReplyRecord),
            ("add_internal_note", None, NoteRecord),
        ]:
            if name == "add_internal_note":
                marker = {
                    "state": "completed",
                    "draft_id": draft_id,
                    "assessment": assessment.model_dump(mode="json"),
                    "requires_attention": assessment.requires_attention,
                    "history_ids": result.history_ids,
                }
                arguments = {"ticket_id": ticket.id, "body": NOTE_PREFIX + json.dumps(marker)}
            operations = [*result.operations, Operation(tool=name, outcome="started")]
            result = result.model_copy(update={"state": "partial", "operations": operations})
            if progress:
                progress(result)  # Failure here prevents this mutation from being attempted.
            try:
                record = await gateway.call(name, arguments, schema)
                if (name == "update_ticket" and record.id != ticket.id) or (
                    name != "update_ticket" and record.ticket_id != ticket.id
                ):
                    raise WorkflowError("Mutation returned an inconsistent ticket ID.")
                if name == "update_ticket" and (
                    record.category != assessment.category
                    or record.priority != assessment.priority
                    or record.status != TicketStatus.NEW
                ):
                    raise WorkflowError("Classification mutation returned inconsistent fields.")
                if name == "draft_reply" and record.body != draft.body:
                    raise WorkflowError("Draft mutation returned different text.")
                if name == "draft_reply":
                    draft_id = record.id
            except WorkflowError:
                operations = [*operations[:-1], Operation(tool=name, outcome="uncertain")]
                return result.model_copy(
                    update={
                        "operations": operations,
                        "message": "Mutation unconfirmed; it may have executed. "
                        "Inspect ticket, drafts, run report and audit before rerunning. "
                        "No remaining writes were attempted.",
                    }
                )
            operations = [
                *operations[:-1],
                Operation(tool=name, outcome="confirmed", record_id=record.id),
            ]
            result = result.model_copy(update={"operations": operations})
            if progress:
                progress(result)
        return result.model_copy(update={"state": "applied"})
    except (LLMError, WorkflowError) as error:
        return result.model_copy(
            update={
                "state": "partial" if result.operations else "failed",
                "message": str(error),
            }
        )
    except (ValidationError, ValueError, OSError):
        return result.model_copy(
            update={
                "state": "partial" if result.operations else "failed",
                "message": "Input validation or local reporting failed; "
                "no further writes attempted.",
            }
        )


async def run_batch(
    gateway: MCPGateway,
    model: ModelClient,
    *,
    limit: int = 5,
    ticket_id: int | None = None,
    apply: bool = False,
    progress: Callable[[TriageResult], None] | None = None,
) -> list[TriageResult]:
    """Inspect at most 100 new records; process at most 20, stop batch after a failure."""
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Batch limit must be 1–20")
    if ticket_id is not None and (type(ticket_id) is not int or not 1 <= ticket_id <= 2**63 - 1):
        raise ValueError("ticket_id must be a positive signed 64-bit integer")
    if apply and not gateway.writes_allowed:
        raise WorkflowError("Apply mode requires explicitly enabled writes.")
    if ticket_id is not None:
        ids = [ticket_id]
    else:
        queue = await gateway.call(
            "search_tickets", {"status": "new", "limit": MAX_QUEUE_SCAN}, TicketSearchResult
        )
        ids = [item.id for item in queue.tickets]
        if ids != sorted(set(ids)) or any(
            item.status != TicketStatus.NEW for item in queue.tickets
        ):
            raise WorkflowError("Queue returned duplicate, unordered, or non-new tickets.")
    return await _triage_ids(gateway, model, ids, limit=limit, apply=apply, progress=progress)


async def run_all(
    gateway: MCPGateway,
    model: ModelClient,
    *,
    apply: bool = False,
    progress: Callable[[TriageResult], None] | None = None,
) -> list[TriageResult]:
    """Snapshot the paged new queue, then visit each ticket once without a run limit."""
    if apply and not gateway.writes_allowed:
        raise WorkflowError("Apply mode requires explicitly enabled writes.")
    ids = []
    for offset in range(0, 10_001, MAX_QUEUE_SCAN):
        queue = await gateway.call(
            "search_tickets",
            {"status": "new", "limit": MAX_QUEUE_SCAN, "offset": offset},
            TicketSearchResult,
        )
        page_ids = [item.id for item in queue.tickets]
        if (
            queue.offset != offset
            or queue.limit != MAX_QUEUE_SCAN
            or page_ids != sorted(set(page_ids))
            or (ids and page_ids and page_ids[0] <= ids[-1])
            or any(item.status != TicketStatus.NEW for item in queue.tickets)
        ):
            raise WorkflowError("Queue pagination returned inconsistent or unordered tickets.")
        ids.extend(page_ids)
        if len(page_ids) < MAX_QUEUE_SCAN:
            break
    else:
        raise WorkflowError("Queue exceeds the provider pagination limit; no tickets processed.")
    return await _triage_ids(gateway, model, ids, limit=None, apply=apply, progress=progress)


async def _triage_ids(gateway, model, ids, *, limit, apply, progress):
    results = []
    processed = 0
    for record_id in ids:
        ticket = await gateway.get_ticket(record_id)
        result = await triage_ticket(gateway, model, ticket, apply=apply, progress=progress)
        results.append(result)
        if progress:
            progress(result)
        if result.state in {"failed", "partial"}:
            break
        if result.state != "skipped":
            processed += 1
        if limit is not None and processed >= limit:
            break
    return results
