"""Small validated model inputs/outputs and explicit partial workflow states."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from dispatch.domain import SLATier, TicketCategory, TicketPriority
from dispatch.mcp_server.providers.models import Contract, RecordID

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
DraftBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]


class Urgency(StrEnum):
    IMMEDIATE = "immediate"
    SAME_DAY = "same_day"
    ROUTINE = "routine"


class Labels(Contract):
    category: TicketCategory
    priority: TicketPriority
    urgency: Urgency
    security_flag: bool = Field(strict=True)


class Assessment(Labels):
    rationale: ShortText

    @property
    def requires_attention(self) -> bool:
        """Derived by code, never dependent on a separate model escalation decision."""
        return (
            self.security_flag
            or self.category == TicketCategory.SECURITY
            or self.priority == TicketPriority.URGENT
            or self.urgency == Urgency.IMMEDIATE
        )


class Draft(Contract):
    body: DraftBody


class Problem(Contract):
    subject: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    description: Annotated[str, StringConstraints(min_length=1, max_length=10_000)]
    company_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    sla_tier: SLATier
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value


class History(Contract):
    ticket_id: RecordID
    subject: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    description: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    resolved_at: datetime
    notes: list[Annotated[str, StringConstraints(max_length=1500)]] = Field(max_length=3)

    @field_validator("resolved_at")
    @classmethod
    def aware_time(cls, value):
        return Problem.aware_time(value)


class Operation(Contract):
    tool: Literal["update_ticket", "draft_reply", "add_internal_note"]
    outcome: Literal["started", "confirmed", "uncertain"]
    record_id: RecordID | None = None


class TriageResult(Contract):
    ticket_id: RecordID
    state: Literal["preview", "applied", "failed", "partial", "skipped"]
    assessment: Assessment | None = None
    draft: Draft | None = None
    history_ids: list[RecordID] = Field(default_factory=list, max_length=3)
    operations: list[Operation] = Field(default_factory=list, max_length=3)
    message: str | None = None
