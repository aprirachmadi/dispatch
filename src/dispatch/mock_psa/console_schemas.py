"""Technician REST contracts; intentionally separate from strict legacy MCP DTOs."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from dispatch.agent.models import Assessment, TriageResult
from dispatch.mock_psa.schemas import Description, ORMResponse, PositiveID

Revision = Annotated[int, Field(strict=True, ge=1, le=2**63 - 2)]


class ApproveDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: Revision


class EditDraft(ApproveDraft):
    body: Description


class ReviewDraft(ORMResponse):
    id: int
    ticket_id: int
    body: str
    status: Literal["draft", "approved"]
    revision: int
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None


class SavedAssessment(BaseModel):
    note_id: int
    draft_id: int
    created_at: datetime
    assessment: Assessment
    requires_attention: bool
    history_ids: list[PositiveID] = Field(max_length=3)


class CompletionMarker(BaseModel):
    """Validate the whole persisted marker; ticket text cannot become executable policy."""

    model_config = ConfigDict(extra="forbid")
    state: Literal["completed"]
    draft_id: PositiveID
    assessment: Assessment
    requires_attention: bool = Field(strict=True)
    history_ids: list[PositiveID] = Field(max_length=3)


class StartTriage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["preview", "apply"] = "preview"
    ticket_id: PositiveID | None = None
    limit: int = Field(default=5, strict=True, ge=1, le=20)


class RunResponse(BaseModel):
    run_id: str
    state: Literal["running", "completed", "incomplete", "failed", "interrupted"]
    mode: Literal["preview", "apply"]
    ticket_id: int | None
    limit: int
    model: str
    prompt_version: str
    started_at: datetime
    finished_at: datetime | None
    message: str | None
    results: list[TriageResult]
