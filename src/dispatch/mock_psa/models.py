"""Synthetic PSA records, unsent reply reviews, and local job/audit journals."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dispatch.domain import SLATier as SLATier
from dispatch.domain import TicketCategory as TicketCategory
from dispatch.domain import TicketPriority as TicketPriority
from dispatch.domain import TicketStatus as TicketStatus
from dispatch.mock_psa.database import Base, UTCDateTime, utc_now


def enum_column(enum: type[StrEnum]) -> SQLAlchemyEnum:
    return SQLAlchemyEnum(
        enum,
        values_callable=lambda values: [item.value for item in values],
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        name=enum.__name__.lower(),
    )


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    sla_tier: Mapped[SLATier] = mapped_column(enum_column(SLATier))
    contacts: Mapped[list["Contact"]] = relationship(back_populates="company")
    tickets: Mapped[list["Ticket"]] = relationship(back_populates="company")


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    company: Mapped[Company] = relationship(back_populates="contacts")
    tickets: Mapped[list["Ticket"]] = relationship(back_populates="contact")


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[TicketCategory | None] = mapped_column(enum_column(TicketCategory))
    priority: Mapped[TicketPriority] = mapped_column(
        enum_column(TicketPriority), default=TicketPriority.MEDIUM
    )
    status: Mapped[TicketStatus] = mapped_column(
        enum_column(TicketStatus), default=TicketStatus.NEW, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    company: Mapped[Company] = relationship(back_populates="tickets")
    contact: Mapped[Contact | None] = relationship(back_populates="tickets")
    notes: Mapped[list["Note"]] = relationship(back_populates="ticket", order_by="Note.id")


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    author_name: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    ticket: Mapped[Ticket] = relationship(back_populates="notes")


class DraftReply(Base):
    """An unsent reply. Approval records review, never delivery."""

    __tablename__ = "draft_replies"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'approved')", name="draft_status"),
        CheckConstraint("revision >= 1", name="positive_revision"),
        CheckConstraint(
            "(status = 'draft' AND approved_at IS NULL) OR "
            "(status = 'approved' AND approved_at IS NOT NULL)",
            name="approval_timestamp",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    revision: Mapped[int] = mapped_column(default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class BackendAudit(Base):
    """Metadata only; successful technician changes commit with their audit row."""

    __tablename__ = "backend_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(40))
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"))
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("draft_replies.id"))
    revision: Mapped[int | None]
    run_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)


class TriageRun(Base):
    """Local single-worker job journal; never automatically replay interrupted writes."""

    __tablename__ = "triage_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('running','completed','incomplete','failed','interrupted')",
            name="run_state",
        ),
        CheckConstraint(
            "(state = 'running' AND active_slot IS NOT NULL AND active_slot = 1) OR "
            "(state != 'running' AND active_slot IS NULL)",
            name="active_run",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    active_slot: Mapped[int | None] = mapped_column(unique=True)
    state: Mapped[str] = mapped_column(String(20), default="running")
    mode: Mapped[str] = mapped_column(String(10))
    ticket_id: Mapped[int | None]
    limit: Mapped[int]
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(40))
    results_json: Mapped[str] = mapped_column(Text, default="[]")
    message: Mapped[str | None] = mapped_column(String(300))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
