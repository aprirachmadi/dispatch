"""Validated provider-neutral inputs and DTOs, independent of SQLAlchemy and MCP."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from dispatch.domain import SLATier, TicketCategory, TicketPriority, TicketStatus

RecordID = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
Limit = Annotated[int, Field(strict=True, ge=1, le=100)]
Offset = Annotated[int, Field(strict=True, ge=0, le=10_000)]
SearchText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TicketSearch(Contract):
    query: SearchText | None = None
    company_id: RecordID | None = None
    category: TicketCategory | None = None
    priority: TicketPriority | None = None
    status: TicketStatus | None = None
    limit: Limit = 25
    offset: Offset = 0


class TicketSummary(Contract):
    id: RecordID
    company_id: RecordID
    contact_id: RecordID | None
    subject: str
    description: str
    category: TicketCategory | None
    priority: TicketPriority
    status: TicketStatus
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class NoteRecord(Contract):
    id: RecordID
    ticket_id: RecordID
    author_name: str
    body: str
    created_at: datetime


class TicketDetail(TicketSummary):
    notes: list[NoteRecord] = Field(max_length=100)
    notes_truncated: bool = False


class TicketSearchResult(Contract):
    tickets: list[TicketSummary] = Field(max_length=100)
    limit: Limit
    offset: Offset


class CompanyRecord(Contract):
    id: RecordID
    name: str
    sla_tier: SLATier


class ContactRecord(Contract):
    id: RecordID
    company_id: RecordID
    name: str
    email: str


class CompanyContext(Contract):
    company: CompanyRecord
    contacts: list[ContactRecord] = Field(max_length=100)
    contacts_truncated: bool = False
