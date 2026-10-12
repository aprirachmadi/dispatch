"""REST adapter with default-denied writes; caller owns the injected HTTP client."""

from typing import Any

import httpx2
from pydantic import TypeAdapter, ValidationError

from dispatch.mcp_server.providers.base import ProviderError, ReadOnlyError, RecordNotFound
from dispatch.mcp_server.providers.models import (
    BodyInput,
    CompanyContext,
    CompanyRecord,
    ContactRecord,
    DraftReplyRecord,
    NoteRecord,
    RecordID,
    TicketChanges,
    TicketDetail,
    TicketSearch,
    TicketSearchResult,
    TicketSummary,
)

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
ID_ADAPTER = TypeAdapter(RecordID)


class MockPSAProvider:
    """Translate bounded reads from the mock API into provider-neutral validated DTOs."""

    def __init__(self, client: httpx2.AsyncClient, *, read_only: bool = True):
        if type(read_only) is not bool:
            raise ValueError("read_only must be a boolean")
        self._client = client
        self._read_only = read_only

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Do not follow redirects, execute text, or expose raw backend response errors."""
        try:
            async with self._client.stream(
                method, path, params=params, json=payload, follow_redirects=False, timeout=5.0
            ) as response:
                if response.status_code == 404:
                    raise RecordNotFound("Requested PSA record was not found.")
                if method != "GET" and response.status_code == 403:
                    raise ReadOnlyError("PSA tool writes are disabled in read-only mode.")
                if response.status_code not in (200, 201):
                    raise ProviderError("PSA request failed; check that the mock API is running.")
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ProviderError("PSA response exceeded the 5 MiB safety limit.")
                    chunks.append(chunk)
                content = b"".join(chunks)
                return httpx2.Response(200, content=content).json()
        except httpx2.TimeoutException:
            if method != "GET":
                raise ProviderError(
                    "Write outcome is unknown after a timeout; verify before retrying."
                ) from None
            raise ProviderError(
                "PSA read timed out; retry when the mock API is available."
            ) from None
        except httpx2.RequestError:
            if method != "GET":
                raise ProviderError(
                    "Write outcome is unknown after a network failure; verify before retrying."
                ) from None
            raise ProviderError(
                "Cannot reach the PSA API; check its address and availability."
            ) from None
        except (ValueError, UnicodeError):
            raise ProviderError("PSA returned an invalid response.") from None

    def _require_write(self, ticket_id: int) -> None:
        if self._read_only:
            raise ReadOnlyError("Tool writes are disabled in read-only mode.")
        ID_ADAPTER.validate_python(ticket_id)

    async def draft_reply(self, ticket_id: int, body: str) -> DraftReplyRecord:
        self._require_write(ticket_id)
        payload = BodyInput(body=body)
        data = await self._request(
            "POST", f"tickets/{ticket_id}/drafts", payload=payload.model_dump()
        )
        try:
            draft = DraftReplyRecord.model_validate(data)
            if draft.ticket_id != ticket_id:
                raise ValueError("Ticket ID mismatch")
            return draft
        except ValueError:
            raise ProviderError(
                "Write returned an invalid draft response; verify before retrying."
            ) from None

    async def add_internal_note(self, ticket_id: int, body: str) -> NoteRecord:
        self._require_write(ticket_id)
        payload = BodyInput(body=body)
        data = await self._request(
            "POST", f"tickets/{ticket_id}/notes", payload=payload.model_dump()
        )
        try:
            note = NoteRecord.model_validate(data)
            if note.ticket_id != ticket_id:
                raise ValueError("Ticket ID mismatch")
            return note
        except ValueError:
            raise ProviderError(
                "Write returned an invalid note response; verify before retrying."
            ) from None

    async def update_ticket(self, ticket_id: int, changes: TicketChanges) -> TicketSummary:
        self._require_write(ticket_id)
        data = await self._request(
            "PATCH",
            f"tickets/{ticket_id}",
            payload=changes.model_dump(mode="json", exclude_unset=True),
        )
        try:
            ticket = TicketSummary.model_validate(data)
            if ticket.id != ticket_id:
                raise ValueError("Ticket ID mismatch")
            return ticket
        except ValueError:
            raise ProviderError(
                "Write returned an invalid ticket response; verify before retrying."
            ) from None

    async def search_tickets(self, filters: TicketSearch) -> TicketSearchResult:
        data = await self._get("tickets", filters.model_dump(mode="json", exclude_none=True))
        try:
            tickets = TypeAdapter(list[TicketSummary]).validate_python(data)
            if len(tickets) > filters.limit:
                raise ValueError("Backend exceeded requested limit")
            return TicketSearchResult(tickets=tickets, limit=filters.limit, offset=filters.offset)
        except (ValidationError, ValueError):
            raise ProviderError("PSA returned an invalid ticket search response.") from None

    async def get_ticket(self, ticket_id: int) -> TicketDetail:
        ID_ADAPTER.validate_python(ticket_id)
        data = await self._get(f"tickets/{ticket_id}")
        try:
            if not isinstance(data, dict) or not isinstance(data.get("notes"), list):
                raise ValueError("Expected ticket and notes")
            bounded = {
                **data,
                "notes": data["notes"][:100],
                "notes_truncated": len(data["notes"]) > 100,
            }
            ticket = TicketDetail.model_validate(bounded)
            if ticket.id != ticket_id or any(note.ticket_id != ticket_id for note in ticket.notes):
                raise ValueError("Ticket ID mismatch")
            return ticket
        except (ValidationError, ValueError):
            raise ProviderError("PSA returned an invalid ticket detail response.") from None

    async def get_company_context(self, company_id: int) -> CompanyContext:
        ID_ADAPTER.validate_python(company_id)
        data = await self._get(f"companies/{company_id}")
        contacts = await self._get("contacts", {"company_id": company_id, "limit": 100})
        try:
            company = CompanyRecord.model_validate(data)
            staff = TypeAdapter(list[ContactRecord]).validate_python(contacts)
            if (
                company.id != company_id
                or len(staff) > 100
                or any(contact.company_id != company_id for contact in staff)
            ):
                raise ValueError("Company ID or contact limit mismatch")
            more = []
            if len(staff) == 100:
                more = await self._get(
                    "contacts", {"company_id": company_id, "limit": 1, "offset": 100}
                )
                extra = TypeAdapter(list[ContactRecord]).validate_python(more)
                if len(extra) > 1 or any(contact.company_id != company_id for contact in extra):
                    raise ValueError("Invalid contact continuation")
            return CompanyContext(company=company, contacts=staff, contacts_truncated=bool(more))
        except (ValidationError, ValueError):
            raise ProviderError("PSA returned an invalid company context response.") from None
