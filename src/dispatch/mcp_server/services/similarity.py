"""Cosine ranking of genuine historical resolutions, without keyword fallback or writes."""

import math

import anyio
from pydantic import TypeAdapter

from dispatch.domain import TicketStatus
from dispatch.mcp_server.providers.base import TicketProvider
from dispatch.mcp_server.providers.models import RecordID, TicketSummary
from dispatch.mcp_server.services.common import collect_tickets, require_aware
from dispatch.mcp_server.services.embeddings import CachedEmbeddings
from dispatch.mcp_server.services.models import MatchLimit, SimilarityResult, SimilarMatch


def problem_text(ticket: TicketSummary) -> str:
    """Exact subject + description only: notes/labels are not embedded as problem evidence."""
    return f"{ticket.subject}\n\n{ticket.description}"


class SimilarityService:
    def __init__(self, embeddings: CachedEmbeddings):
        self.embeddings = embeddings

    async def find(
        self,
        provider: TicketProvider,
        ticket_id: int,
        limit: int = 5,
        company_id: int | None = None,
    ) -> SimilarityResult:
        TypeAdapter(RecordID).validate_python(ticket_id)
        TypeAdapter(MatchLimit).validate_python(limit)
        if company_id is not None:
            TypeAdapter(RecordID).validate_python(company_id)
        source = await provider.get_ticket(ticket_id)
        require_aware(source.created_at)
        tickets = await collect_tickets(
            provider, company_id, (TicketStatus.RESOLVED, TicketStatus.CLOSED)
        )
        historical = [
            ticket
            for ticket in tickets
            if ticket.id != ticket_id
            and ticket.resolved_at is not None
            and ticket.resolved_at < source.created_at
        ]
        matches: list[SimilarMatch] = []
        if historical:
            texts = [problem_text(source)] + [problem_text(ticket) for ticket in historical]
            vectors = await anyio.to_thread.run_sync(self.embeddings.encode, texts)
            query = vectors[0]
            for ticket, vector in zip(historical, vectors[1:], strict=True):
                score = max(
                    -1.0, min(1.0, math.fsum(a * b for a, b in zip(query, vector, strict=True)))
                )
                matches.append(SimilarMatch(ticket=ticket, score=score))
            matches.sort(key=lambda match: (-match.score, match.ticket.id))
        return SimilarityResult(
            source_ticket_id=ticket_id,
            history_cutoff=source.created_at,
            model=self.embeddings.identity,
            eligible_tickets=len(historical),
            matches=matches[:limit],
        )
