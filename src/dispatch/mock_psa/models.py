"""Company, contact, ticket, and internal-note records for the synthetic PSA."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy import ForeignKey, String, Text
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
