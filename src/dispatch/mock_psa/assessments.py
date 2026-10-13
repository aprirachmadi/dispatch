"""Bounded saved-assessment projection shared by detail and technician queue reads."""

import json

from pydantic import ValidationError
from sqlalchemy import LargeBinary, cast, func, select

from dispatch.agent.workflow import NOTE_PREFIX
from dispatch.mock_psa.console_schemas import CompletionMarker, SavedAssessment
from dispatch.mock_psa.models import DraftReply, Note


def saved_assessments(session, ticket_ids):
    """Latest valid of 100 matching markers per ticket, never trust stored attention."""
    if not ticket_ids:
        return {}
    ranked = (
        select(
            Note.id,
            Note.ticket_id,
            # SQLite TEXT substr stops at NUL: slice UTF-8 bytes instead so a
            # malformed marker cannot become valid by losing its trailing data.
            func.substr(cast(Note.body, LargeBinary), 1, 40_001).label("body"),
            Note.created_at,
            func.row_number()
            .over(partition_by=Note.ticket_id, order_by=Note.id.desc())
            .label("rank"),
        )
        .where(
            Note.ticket_id.in_(ticket_ids),
            Note.author_name == "Dispatch MCP",
            Note.body.startswith(NOTE_PREFIX, autoescape=True),
        )
        .subquery()
    )
    rows = session.execute(
        select(ranked).where(ranked.c.rank <= 100).order_by(ranked.c.id.desc()),
        execution_options={"yield_per": 100},
    )
    found = {}
    for note in rows:
        if note.ticket_id in found or len(note.body) > 40_000:
            continue
        try:
            body = note.body.decode("utf-8")
            if len(body) > 10_000:
                continue
            marker = CompletionMarker.model_validate(json.loads(body[len(NOTE_PREFIX) :]))
        except (ValueError, ValidationError, RecursionError):
            continue
        draft_ticket = session.scalar(
            select(DraftReply.ticket_id).where(DraftReply.id == marker.draft_id)
        )
        if draft_ticket != note.ticket_id:
            continue
        found[note.ticket_id] = SavedAssessment(
            note_id=note.id,
            draft_id=marker.draft_id,
            created_at=note.created_at,
            assessment=marker.assessment,
            requires_attention=marker.assessment.requires_attention,
            history_ids=marker.history_ids,
        )
    return found
