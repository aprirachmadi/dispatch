"""Bounded, independently labeled synthetic cases with frozen chronological context."""

import hashlib
from pathlib import Path
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from dispatch.agent.models import Assessment, Draft, History, Labels, Problem, ShortText
from dispatch.mcp_server.providers.models import Contract

CaseID = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class EvalCase(Contract):
    case_id: CaseID
    problem: Problem
    history: list[History] = Field(default_factory=list, max_length=3)
    expected: Labels
    reference_draft: Draft
    response_requirements: list[ShortText] = Field(min_length=1, max_length=8)
    tags: list[CaseID] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def historical_context(self):
        ids = [item.ticket_id for item in self.history]
        if len(set(ids)) != len(ids) or any(
            item.resolved_at >= self.problem.created_at for item in self.history
        ):
            raise ValueError("History must be unique and resolved strictly before the source")
        return self


class CalibrationCase(Contract):
    case: EvalCase
    assessment: Assessment
    draft: Draft
    expected_quality_pass: bool = Field(strict=True)
    expected_consistency_pass: bool = Field(strict=True)


def load_dataset(path: Path, schema=EvalCase) -> tuple[list, str]:
    """Read 1–100 cases, at most 1 MiB; fail safely instead of silently dropping bad rows."""
    with path.open("rb") as stream:
        raw = stream.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("Evaluation dataset exceeds 1 MiB")
    cases = []
    seen = set()
    try:
        lines = raw.decode("utf-8").splitlines()
        for line in lines:
            if not line.strip():
                continue
            case = schema.model_validate_json(line)
            case_id = case.case_id if isinstance(case, EvalCase) else case.case.case_id
            if case_id in seen or len(cases) == 100:
                raise ValueError("Duplicate IDs or too many cases")
            seen.add(case_id)
            cases.append(case)
        if not cases:
            raise ValueError("Empty dataset")
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError(
            "Dataset is invalid; check JSON, schemas, IDs and historical cutoffs."
        ) from None
    return cases, hashlib.sha256(raw).hexdigest()
