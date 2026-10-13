"""Bounded allowlisted projection of the trusted local MCP JSONL audit file."""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dispatch.domain import TicketCategory, TicketPriority, TicketStatus
from dispatch.mcp_server.audit import KNOWN_TOOLS


class Arguments(BaseModel):
    model_config = ConfigDict(extra="ignore")
    argument_count: int = Field(default=0, strict=True, ge=0, le=1000)
    ticket_id: int | Literal["invalid"] | None = Field(default=None)
    company_id: int | Literal["invalid"] | None = Field(default=None)
    limit: int | Literal["invalid"] | None = None
    offset: int | Literal["invalid"] | None = None
    category: TicketCategory | Literal["invalid"] | None = None
    priority: TicketPriority | Literal["invalid"] | None = None
    status: TicketStatus | Literal["invalid"] | None = None
    query_length: int | Literal["invalid"] | None = None
    body_length: int | Literal["invalid"] | None = None
    week_start: date | Literal["invalid"] | None = None
    invalid_arguments: bool = Field(default=False, strict=True)

    @field_validator(
        "ticket_id", "company_id", "limit", "offset", "query_length", "body_length", mode="before"
    )
    @classmethod
    def safe_integer(cls, value):
        if value is None or value == "invalid":
            return value
        if type(value) is not int or not 0 <= value < 2**63:
            raise ValueError("Invalid metadata integer")
        return value


class MCPEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: Literal["mcp"] = "mcp"
    call_id: UUID
    tool: str
    event: Literal["started", "finished"]
    timestamp: datetime
    read_only: bool = Field(strict=True)
    arguments: Arguments | None = None
    outcome: Literal["success", "error", "blocked", "cancelled"] | None = None
    duration_ms: int | None = Field(default=None, ge=0, le=2**63 - 1)

    @field_validator("tool")
    @classmethod
    def known_tool(cls, value):
        if value not in KNOWN_TOOLS | {"unknown_tool"}:
            raise ValueError("Unrecognized tool")
        return value

    @field_validator("timestamp")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Missing timezone")
        return value


def read_mcp_events(path: Path, limit: int, offset: int) -> dict:
    """Oldest-first valid events; 16 MiB/20000-line scan ceiling, ignore partial tail."""
    events = []
    valid = invalid = consumed = 0
    truncated = False
    if not path.exists():
        return {"events": [], "next_offset": None, "scan_truncated": False, "invalid_lines": 0}
    with path.open("rb") as stream:
        for _ in range(20_000):
            line = stream.readline(4097)
            if not line:
                break
            consumed += len(line)
            if consumed > 16 * 1024 * 1024 or len(line) > 4096:
                truncated = True
                break
            if not line.endswith(b"\n"):
                break  # A concurrent append can leave the last record temporarily incomplete.
            try:
                event = MCPEvent.model_validate(json.loads(line))
            except (ValueError, ValidationError, RecursionError):
                invalid += 1
                continue
            if valid >= offset:
                events.append(event.model_dump(mode="json", exclude_none=True))
            valid += 1
            if len(events) > limit:
                break
        else:
            truncated = True
    more = len(events) > limit
    next_offset = offset + limit if more and offset + limit <= 10_000 else None
    return {
        "events": events[:limit],
        "next_offset": next_offset,
        "scan_truncated": truncated or (more and next_offset is None),
        "invalid_lines": invalid,
    }
