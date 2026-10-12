"""Additive frontend reads preserve legacy contracts, policy, and saved projections."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from dispatch.agent.workflow import NOTE_PREFIX
from dispatch.mock_psa import workbench_routes
from dispatch.mock_psa.frontend import console_directory
from dispatch.mock_psa.models import BackendAudit, Note, TriageRun

LABELS = {
    "category": "security",
    "priority": "high",
    "urgency": "same_day",
    "security_flag": True,
    "rationale": "Synthetic credential disclosure.",
}


def saved(client, ticket_id=1, labels=None, body=None, draft_id=None):
    draft_id = (
        draft_id
        or client.post(f"/tickets/{ticket_id}/drafts", json={"body": "Synthetic response"}).json()[
            "id"
        ]
    )
    marker = {
        "state": "completed",
        "draft_id": draft_id,
        "assessment": labels or LABELS,
        "requires_attention": False,
        "history_ids": [],
    }
    client.post(
        f"/tickets/{ticket_id}/notes", json={"body": body or NOTE_PREFIX + json.dumps(marker)}
    )
    return draft_id


def test_policy_does_not_expose_configuration(client, write_client):
    assert client.get("/console/config").json() == {"read_only": True}
    assert write_client.get("/console/config").json() == {"read_only": False}


def test_queue_unknown_attention_and_legacy_shape(client):
    before = client.get("/tickets").json()
    item = client.get("/console/tickets").json()
    assert item["total"] == 1 and item["next_offset"] is None
    assert item["tickets"][0]["requires_attention"] is None
    assert item["tickets"][0]["company_name"] == "Synthetic Maple"
    assert item["tickets"][0]["draft_count"] == 0
    assert client.get("/console/tickets?attention=not_flagged").json()["total"] == 0
    assert client.get("/console/tickets?attention=not_assessed").json()["total"] == 1
    assert client.get("/tickets").json() == before
    with client.app.state.session_factory() as session:
        assert session.scalars(select(BackendAudit)).all() == []


def test_shared_valid_projection_ignores_bad_markers_and_preserves_labels(write_client):
    draft_id = saved(write_client)
    saved(write_client, body=NOTE_PREFIX + "not json", draft_id=draft_id)
    write_client.post(
        "/tickets/1/notes", json={"body": NOTE_PREFIX + json.dumps({"state": "completed"})}
    )
    write_client.patch("/tickets/1", json={"category": "hardware", "priority": "low"})
    detail = write_client.get("/tickets/1/assessment").json()
    row = write_client.get("/console/tickets?attention=required").json()["tickets"][0]
    assert detail["requires_attention"] and row["requires_attention"]
    assert row["category"] == "hardware" and row["priority"] == "low"
    assert row["assessment_draft_id"] == draft_id
    assert row["draft_count"] == 1 and row["approved_count"] == 0
    write_client.post(f"/tickets/1/drafts/{draft_id}/approve", json={"revision": 1})
    assert write_client.get("/console/tickets").json()["tickets"][0]["approved_count"] == 1


def test_no_flag_distinct_from_missing(write_client):
    labels = {**LABELS, "category": "email", "security_flag": False, "priority": "medium"}
    saved(write_client, labels=labels)
    assert write_client.get("/console/tickets?attention=not_flagged").json()["total"] == 1
    assert write_client.get("/console/tickets?attention=not_assessed").json()["total"] == 0


def test_attention_filter_applied_before_pagination(write_client):
    for index in range(4):
        ticket = write_client.post(
            "/tickets",
            json={"company_id": 1, "subject": f"Case {index}", "description": "Synthetic"},
        ).json()
        if index in [1, 3]:
            saved(write_client, ticket["id"])
    first = write_client.get("/console/tickets?attention=required&limit=1").json()
    second = write_client.get("/console/tickets?attention=required&limit=1&offset=1").json()
    assert first["total"] == 2 and first["next_offset"] == 1
    assert second["next_offset"] is None
    assert first["tickets"][0]["id"] < second["tickets"][0]["id"]


def test_projection_bound_and_wrong_ticket_draft(write_client):
    other = write_client.post(
        "/tickets", json={"company_id": 1, "subject": "Other", "description": "Synthetic"}
    ).json()["id"]
    other_draft = write_client.post(
        f"/tickets/{other}/drafts", json={"body": "Other response"}
    ).json()["id"]
    saved(write_client, draft_id=other_draft)
    assert write_client.get("/tickets/1/assessment").status_code == 404
    draft_id = saved(write_client)
    with write_client.app.state.session_factory.begin() as session:
        session.add_all(
            [
                Note(ticket_id=1, author_name="Dispatch MCP", body=NOTE_PREFIX + "invalid")
                for _ in range(100)
            ]
        )
    assert write_client.get("/tickets/1/assessment").status_code == 404
    assert write_client.get("/console/tickets").json()["tickets"][0]["requires_attention"] is None
    assert draft_id != other_draft


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "offset=10001",
        "attention=safe",
        "query=%20",
        "status=bad",
        "company_id=0",
    ],
)
def test_invalid_queue_inputs(client, query):
    assert client.get(f"/console/tickets?{query}").status_code == 422


def test_literal_search_and_intersecting_filters(write_client):
    write_client.post(
        "/tickets",
        json={
            "company_id": 2,
            "subject": "Literal %_ text",
            "description": "Synthetic",
            "category": "network",
            "priority": "high",
        },
    )
    data = write_client.get(
        "/console/tickets", params={"query": "%_", "category": "network", "company_id": 2}
    ).json()
    assert data["total"] == 1
    assert write_client.get("/console/tickets?company_id=1&priority=high").json()["total"] == 0


def test_queue_fails_explicitly_on_scan_limit(client, monkeypatch):
    monkeypatch.setattr(workbench_routes, "QUEUE_SCAN_LIMIT", 0)
    assert client.get("/console/tickets").status_code == 503


def test_queue_reads_bounded_projection_without_body_serialization(write_client):
    saved(write_client)
    engine = write_client.app.state.session_factory.kw["bind"]
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        data = write_client.get("/console/tickets").json()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert "Synthetic response" not in json.dumps(data)
    assert len(statements) <= 5
    assert any("row_number" in s for s in statements)


def test_run_discovery_newest_first_without_generator_text(client):
    now = datetime.now(UTC)
    ids = [str(uuid4()), str(uuid4())]
    with client.app.state.session_factory.begin() as session:
        for i, id in enumerate(ids):
            session.add(
                TriageRun(
                    id=id,
                    state="completed",
                    mode="preview",
                    limit=1,
                    model="synthetic",
                    prompt_version="triage-v1",
                    started_at=now + timedelta(seconds=i),
                    finished_at=now,
                    results_json='[{"body":"PRIVATE-RESPONSE"}]',
                )
            )
    page = client.get("/triage/runs?limit=1").json()
    assert page["runs"][0]["run_id"] == ids[1] and page["next_offset"] == 1
    assert "PRIVATE-RESPONSE" not in json.dumps(page) and "results" not in page["runs"][0]
    assert client.get("/triage/runs?offset=1&limit=1").json()["runs"][0]["run_id"] == ids[0]
    assert client.get("/triage/runs?limit=101").status_code == 422


def test_assets_isolated_from_api_and_working_directory(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert console_directory().is_absolute()
    response = client.get("/console/")
    assert response.status_code == 200 and "Technician console" in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    for path in [
        "/console/.env",
        "/console/data/dispatch.db",
        "/console/README.md",
        "/console/../.env",
        "/console/assets/nope.js",
    ]:
        assert client.get(path).status_code == 404
    assert client.get("/console/assets/app.js").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/tickets/999").status_code == 404
    assert client.get("/console/config").headers["content-type"].startswith("application/json")


def test_ui_has_no_dynamic_html_or_remote_assets():
    web = console_directory()
    for path in Path(web / "assets").glob("*.js"):
        source = path.read_text()
        assert "innerHTML" not in source and "eval(" not in source
    html = (web / "index.html").read_text()
    assert "https://" not in html and "http://" not in html


@pytest.mark.parametrize("suffix", ["\x00", "\x00ignored trailing data", " " * 10000])
def test_projection_does_not_truncate_invalid_marker_into_valid_json(write_client, suffix):
    draft_id = write_client.post("/tickets/1/drafts", json={"body": "Synthetic"}).json()["id"]
    marker = {
        "state": "completed",
        "draft_id": draft_id,
        "assessment": LABELS,
        "requires_attention": False,
        "history_ids": [],
    }
    with write_client.app.state.session_factory.begin() as session:
        session.add(
            Note(
                ticket_id=1,
                author_name="Dispatch MCP",
                body=NOTE_PREFIX + json.dumps(marker) + suffix,
            )
        )
    assert write_client.get("/tickets/1/assessment").status_code == 404
    assert write_client.get("/console/tickets").json()["tickets"][0]["requires_attention"] is None


def test_projection_preserves_multibyte_unicode(write_client):
    draft_id = write_client.post("/tickets/1/drafts", json={"body": "Synthetic"}).json()["id"]
    labels = {**LABELS, "rationale": "😀" * 1000}
    marker = {
        "state": "completed",
        "draft_id": draft_id,
        "assessment": labels,
        "requires_attention": False,
        "history_ids": [],
    }
    response = write_client.post(
        "/tickets/1/notes", json={"body": NOTE_PREFIX + json.dumps(marker, ensure_ascii=False)}
    )
    assert response.status_code == 201
    assert write_client.get("/tickets/1/assessment").json()["assessment"] == labels
    assert write_client.get("/console/tickets").json()["tickets"][0]["requires_attention"] is True
