from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from analytics_helpers import NOW, MemoryProvider, ticket
from pydantic import ValidationError

from dispatch.mcp_server.providers.base import RecordNotFound
from dispatch.mcp_server.services.reports import ReportService

pytestmark = pytest.mark.anyio


def with_times(ticket_id, created, resolved=None, **values):
    return ticket(ticket_id, **values).model_copy(
        update={
            "created_at": created,
            "updated_at": resolved or created,
            "resolved_at": resolved,
        }
    )


async def test_utc_half_open_window_and_current_backlog_are_separate():
    end = NOW + timedelta(days=7)
    records = [
        with_times(1, NOW, status="new"),
        with_times(2, end - timedelta(microseconds=1), status="closed"),
        with_times(3, end, status="new"),
        with_times(4, NOW - timedelta(days=10), NOW, status="resolved"),
        with_times(5, NOW - timedelta(days=20), end, status="closed"),
        with_times(6, NOW - timedelta(days=20), status="in_progress"),
    ]
    report = await ReportService(clock=lambda: end + timedelta(days=3)).weekly(
        MemoryProvider(records), "2026-10-12"
    )
    assert report.opened == 2 and report.resolved == 1
    assert report.week_end_exclusive == date(2026, 10, 19)
    assert report.current_backlog.new == 2
    assert report.current_backlog.in_progress == 1
    assert report.current_backlog.total == 3  # Current, NOT a reconstructed week-end backlog.
    assert report.period_complete is True
    assert report.opened_by_category["hardware"] == 2
    assert report.opened_by_priority["medium"] == 2


async def test_timezone_offsets_are_compared_as_instants_and_normalized_to_utc():
    jakarta = timezone(timedelta(hours=7))
    created = datetime(2026, 10, 12, 7, tzinfo=jakarta)
    record = with_times(1, created, status="new")
    report = await ReportService(clock=lambda: created).weekly(
        MemoryProvider([record]), "2026-10-12"
    )
    assert report.opened == 1
    assert report.generated_at == NOW
    assert report.generated_at.tzinfo == UTC
    assert report.period_complete is False


async def test_company_filter_and_unknown_company():
    records = [with_times(1, NOW, company_id=1), with_times(2, NOW, company_id=2)]
    provider = MemoryProvider(records)
    report = await ReportService(clock=lambda: NOW).weekly(provider, "2026-10-12", company_id=1)
    assert report.opened == 1 and report.scanned_tickets == 1
    with pytest.raises(RecordNotFound):
        await ReportService().weekly(provider, "2026-10-12", company_id=999)


async def test_empty_report_is_zero_with_all_breakdown_keys():
    report = await ReportService(clock=lambda: NOW).weekly(MemoryProvider([]), "2026-10-12")
    assert report.opened == report.resolved == report.current_backlog.total == 0
    assert len(report.opened_by_category) == 7
    assert len(report.opened_by_priority) == 4
    assert set(report.opened_by_category.values()) == {0}


async def test_unclassified_tickets_count_without_inventing_a_category():
    record = with_times(1, NOW, status="new").model_copy(update={"category": None})
    report = await ReportService(clock=lambda: NOW).weekly(MemoryProvider([record]), "2026-10-12")
    assert report.opened_by_category["unclassified"] == 1


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-13",
        "2026-02-30",
        "not a date",
        "20261012",
        "2026-10-12T00:00:00Z",
        1791763200,
        True,
        datetime(2026, 10, 12, tzinfo=UTC),
        "9999-12-27",
    ],
)
async def test_invalid_week_start_rejected_before_provider_access(value):
    provider = MemoryProvider([])
    with pytest.raises(ValidationError):
        await ReportService().weekly(provider, value)
    assert provider.calls == []


async def test_report_reads_all_pages():
    records = [with_times(index, NOW, status="new") for index in range(1, 151)]
    provider = MemoryProvider(records)
    report = await ReportService(clock=lambda: NOW).weekly(provider, date(2026, 10, 12))
    assert report.opened == 150 and report.scanned_tickets == 150
    assert len(provider.calls) == 2
