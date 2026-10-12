import pytest
from pydantic import ValidationError

from dispatch.mcp_server.providers.models import BodyInput, TicketChanges


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"priority": None},
        {"category": None},
        {"status": "closed"},
        {"priority": "critical"},
        {"category": "billing"},
    ],
)
def test_invalid_classification_contract(changes):
    with pytest.raises(ValidationError):
        TicketChanges(**changes)


@pytest.mark.parametrize("body", ["", "   ", "x" * 10001, 123])
def test_invalid_body_contract(body):
    with pytest.raises(ValidationError):
        BodyInput(body=body)
