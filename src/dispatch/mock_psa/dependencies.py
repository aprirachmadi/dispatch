"""Shared request-scoped database and policy dependencies; no import-time I/O."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Query, Request
from sqlalchemy.orm import Session

Limit = Annotated[int, Query(ge=1, le=100, description="Maximum records, from 1 to 100.")]
Offset = Annotated[int, Query(ge=0, le=10_000, description="Records to skip, from 0 to 10000.")]
RecordID = Annotated[int, Path(gt=0, le=2**63 - 1)]
CompanyFilter = Annotated[int | None, Query(gt=0, le=2**63 - 1)]
SearchQuery = Annotated[str | None, Query(min_length=1, max_length=200)]


def get_session(request: Request) -> Iterator[Session]:
    """Provide one DB session per request, closing it even after a failed request."""
    with request.app.state.session_factory() as session:
        yield session


DBSession = Annotated[Session, Depends(get_session)]


def require_write_mode(request: Request) -> None:
    """Guard tool/technician writes independently of MCP; ticket intake stays enabled."""
    if request.app.state.read_only:
        raise HTTPException(status_code=403, detail="Tool writes are disabled in read-only mode")
