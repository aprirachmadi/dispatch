"""Validated REST inputs and explicit response shapes; ticket text stays untrusted."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from dispatch.mock_psa.models import SLATier, TicketCategory, TicketPriority, TicketStatus

PositiveID = Annotated[int, Field(strict=True, gt=0)]
Subject = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)
]


class ORMResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CompanyResponse(ORMResponse):
    id: int
    name: str
    sla_tier: SLATier


class ContactResponse(ORMResponse):
    id: int
    company_id: int
    name: str
    email: str


class TicketCreate(BaseModel):
    """Create a new ticket, never set its status, timestamps, or internal notes."""

    model_config = ConfigDict(extra="forbid")

    company_id: PositiveID
    contact_id: PositiveID | None = None
    subject: Subject
    description: Description
    category: TicketCategory | None = None
    priority: TicketPriority = TicketPriority.MEDIUM


class TicketResponse(ORMResponse):
    id: int
    company_id: int
    contact_id: int | None
    subject: str
    description: str
    category: TicketCategory | None
    priority: TicketPriority
    status: TicketStatus
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class NoteResponse(ORMResponse):
    id: int
    ticket_id: int
    author_name: str
    body: str
    created_at: datetime


class TicketDetail(TicketResponse):
    notes: list[NoteResponse]
