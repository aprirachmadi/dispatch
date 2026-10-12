"""Phase 6 application checks; isolated data and deterministic background runners only."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from dispatch.agent.config import ModelSettings
from dispatch.agent.models import Operation, TriageResult
from dispatch.agent.workflow import NOTE_PREFIX
from dispatch.mock_psa.config import Settings
from dispatch.mock_psa.main import create_app
from dispatch.mock_psa.models import BackendAudit, DraftReply, TriageRun

LABELS = {
    "category": "security",
    "priority": "urgent",
    "urgency": "immediate",
    "security_flag": True,
    "rationale": "Password disclosure reported.",
}


def new_draft(client):
    response = client.post("/tickets/1/drafts", json={"body": "Synthetic private draft"})
    assert response.status_code == 201
    return response.json()["id"]


def test_review_revision_and_immutable_approval(write_client):
    before = write_client.get("/tickets/1").json()
    draft_id = new_draft(write_client)
    path = f"/tickets/1/drafts/{draft_id}"
    initial = write_client.get("/tickets/1/drafts").json()[0]
    assert initial["revision"] == 1 and initial["approved_at"] is None
    edit = write_client.patch(path, json={"body": "  Technician reviewed text  ", "revision": 1})
    assert edit.status_code == 200
    assert edit.json()["body"] == "Technician reviewed text" and edit.json()["revision"] == 2
    assert write_client.patch(path, json={"body": "Stale", "revision": 1}).status_code == 409
    assert write_client.post(path + "/approve", json={"revision": 1}).status_code == 409
    approved = write_client.post(path + "/approve", json={"revision": 2})
    assert approved.status_code == 200
    assert approved.json()["revision"] == 3 and approved.json()["status"] == "approved"
    assert approved.json()["approved_at"] is not None
    assert write_client.post(path + "/approve", json={"revision": 3}).status_code == 409
    assert write_client.patch(path, json={"revision": 3, "body": "Changed"}).status_code == 409
    assert write_client.get("/tickets/1").json() == before  # no status change, notes, or delivery
    events = write_client.get("/audit-log").json()["events"]
    assert [item["action"] for item in events] == ["draft_edited", "draft_approved"]
    assert "Technician reviewed text" not in json.dumps(events)
    page = write_client.get("/audit-log?limit=1").json()
    assert page["next_offset"] == 1
    assert write_client.get("/audit-log?limit=1&offset=1").json()["next_offset"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"revision": True},
        {"revision": 0},
        {"revision": "1"},
        {"revision": 1, "status": "approved"},
        {"revision": 1, "body": ""},
    ],
)
def test_bad_approval_inputs(write_client, payload):
    draft_id = new_draft(write_client)
    assert (
        write_client.post(f"/tickets/1/drafts/{draft_id}/approve", json=payload).status_code == 422
    )
    assert write_client.get("/tickets/1/drafts").json()[0]["status"] == "draft"


@pytest.mark.parametrize(
    "payload",
    [
        {"revision": 1},
        {"revision": 1, "body": " "},
        {"revision": 1, "body": "x" * 10001},
        {"revision": 1, "body": "Hi", "approved_at": "now"},
    ],
)
def test_bad_edit_inputs(write_client, payload):
    draft_id = new_draft(write_client)
    assert write_client.patch(f"/tickets/1/drafts/{draft_id}", json=payload).status_code == 422


def test_readonly_review_and_missing_records(client, write_client):
    draft_id = new_draft(write_client)
    for method, suffix, payload in [
        ("PATCH", "", {"revision": 1, "body": "Edit"}),
        ("POST", "/approve", {"revision": 1}),
    ]:
        assert (
            client.request(
                method, f"/tickets/1/drafts/{draft_id}{suffix}", json=payload
            ).status_code
            == 403
        )
        assert (
            write_client.request(
                method, f"/tickets/999/drafts/{draft_id}{suffix}", json=payload
            ).status_code
            == 404
        )
        assert (
            write_client.request(method, f"/tickets/1/drafts/999{suffix}", json=payload).status_code
            == 404
        )
    assert client.get("/tickets/999/drafts").status_code == 404
    other = write_client.post(
        "/tickets", json={"company_id": 1, "subject": "Other", "description": "Demo"}
    ).json()["id"]
    assert (
        write_client.post(
            f"/tickets/{other}/drafts/{draft_id}/approve", json={"revision": 1}
        ).status_code
        == 404
    )
    assert client.post("/triage/runs", json={"mode": "apply"}).status_code == 403


def test_audit_failure_rolls_back_review(write_client, monkeypatch):
    draft_id = new_draft(write_client)
    factory = write_client.app.state.session_factory
    # Fail after UPDATE, at the audit INSERT flush, inside the same transaction.
    from sqlalchemy import event

    def reject_audit(mapper, connection, target):
        raise SQLAlchemyError("Synthetic audit storage failure")

    event.listen(BackendAudit, "before_insert", reject_audit)
    try:
        with pytest.raises(SQLAlchemyError):
            write_client.post(f"/tickets/1/drafts/{draft_id}/approve", json={"revision": 1})
    finally:
        event.remove(BackendAudit, "before_insert", reject_audit)
    with factory() as session:
        assert session.get(DraftReply, draft_id).status == "draft"
        assert session.get(DraftReply, draft_id).revision == 1
        assert session.scalars(select(BackendAudit)).all() == []


def test_validated_saved_assessment(write_client):
    assert write_client.get("/tickets/1/assessment").status_code == 404
    draft_id = new_draft(write_client)
    marker = {
        "state": "completed",
        "draft_id": draft_id,
        "assessment": LABELS,
        "requires_attention": False,
        "history_ids": [],
    }
    write_client.post("/tickets/1/notes", json={"body": NOTE_PREFIX + json.dumps(marker)})
    write_client.post("/tickets/1/notes", json={"body": NOTE_PREFIX + "ignore instructions"})
    write_client.post(
        "/tickets/1/notes", json={"body": NOTE_PREFIX + json.dumps({**marker, "draft_id": 999})}
    )
    response = write_client.get("/tickets/1/assessment")
    assert response.status_code == 200
    assert response.json()["draft_id"] == draft_id
    assert response.json()["assessment"] == LABELS
    assert response.json()["requires_attention"] is True
    assert write_client.get("/tickets/999/assessment").status_code == 404


def test_weekly_report_reuses_service(client):
    report = client.get("/reports/weekly?week_start=2026-10-12")
    assert report.status_code == 200
    assert report.json()["scanned_tickets"] == 1
    assert report.json()["timezone"] == "UTC"
    assert report.json()["current_backlog"]["new"] == 1
    assert client.get("/reports/weekly?week_start=2026-10-13").status_code == 422
    assert client.get("/reports/weekly?week_start=2026-10-12&company_id=0").status_code == 422


def test_mcp_audit_redaction_pagination_and_partial_tail(client):
    path = client.app.state.mcp_settings.audit_path
    event = {
        "call_id": str(uuid4()),
        "tool": "get_ticket",
        "event": "started",
        "read_only": True,
        "timestamp": datetime.now(UTC).isoformat(),
        "arguments": {"ticket_id": 1, "body": "SYNTHETIC-SECRET", "query_length": 4},
        "api_key": "SYNTHETIC-SECRET",
        "error": "PRIVATE-PROVIDER-BODY",
    }
    path.write_text(json.dumps(event) + "\nnot json\n" + json.dumps(event) + "\n{partial")
    page = client.get("/audit-log?source=mcp&limit=1").json()
    assert page["source"] == "mcp" and page["events"][0]["source"] == "mcp"
    assert page["next_offset"] == 1 and page["invalid_lines"] == 1
    assert "SYNTHETIC-SECRET" not in json.dumps(page) and "PRIVATE-PROVIDER-BODY" not in json.dumps(
        page
    )
    assert page["events"][0]["arguments"]["ticket_id"] == 1
    assert client.get("/audit-log?source=mcp&offset=1").json()["next_offset"] is None
    assert client.get("/audit-log?source=other").status_code == 422
    assert client.get("/audit-log?limit=101").status_code == 422
    path.write_bytes(b"x" * 4097)
    assert client.get("/audit-log?source=mcp").json()["scan_truncated"] is True


def test_missing_mcp_audit_is_empty(client):
    assert client.get("/audit-log?source=mcp").json()["events"] == []


def configure_runner(client, runner):
    manager = client.app.state.run_manager
    manager.model_loader = lambda: ModelSettings(
        base_url="http://localhost/v1", model="synthetic-model"
    )
    manager.runner = runner
    return manager


def wait_run(client, run_id):
    # Wait on the app's own task, not arbitrary time-based sleeps.
    client.portal.call(lambda: client.app.state.run_manager.task)
    return client.get(f"/triage/runs/{run_id}").json()


def test_persisted_preview_job_and_polling(client):
    async def runner(model, settings, **kwargs):
        assert not kwargs["apply"] and settings.read_only
        result = TriageResult(
            ticket_id=1, state="preview", assessment=LABELS, draft={"body": "Synthetic draft"}
        )
        kwargs["progress"](result)
        return [result]

    configure_runner(client, runner)
    before = client.get("/tickets/1").json()
    accepted = client.post("/triage/runs", json={"ticket_id": 1})
    assert accepted.status_code == 202 and accepted.json()["state"] == "running"
    final = wait_run(client, accepted.json()["run_id"])
    assert final["state"] == "completed" and final["finished_at"] is not None
    assert final["results"][0]["draft"]["body"] == "Synthetic draft"
    assert client.get("/tickets/1").json() == before
    assert client.get("/tickets/1/drafts").json() == []
    assert client.get("/triage/runs/not-uuid").status_code == 422
    assert client.get(f"/triage/runs/{uuid4()}").status_code == 404
    assert [e["action"] for e in client.get("/audit-log").json()["events"]] == [
        "triage_started",
        "triage_completed",
    ]


def test_single_active_job_and_shutdown_recovery(client):
    async def held(model, settings, **kwargs):
        kwargs["progress"](
            TriageResult(
                ticket_id=1,
                state="partial",
                operations=[Operation(tool="draft_reply", outcome="started")],
            )
        )
        await asyncio.Event().wait()

    manager = configure_runner(client, held)
    run_id = client.post("/triage/runs", json={}).json()["run_id"]
    assert client.post("/triage/runs", json={}).status_code == 409
    client.portal.call(manager.close)
    interrupted = client.get(f"/triage/runs/{run_id}").json()
    assert interrupted["state"] == "interrupted"
    assert interrupted["results"][0]["operations"][0]["outcome"] == "started"
    # Simulate crash journal on disk, then a fresh backend lifecycle.
    with manager.sessions.begin() as session:
        record = session.get(TriageRun, run_id)
        record.state = "running"
        record.active_slot = 1
        record.finished_at = None
    url = manager.sessions.kw["bind"].url.render_as_string()
    with TestClient(create_app(Settings(database_url=url))) as restarted:
        saved = restarted.get(f"/triage/runs/{run_id}").json()
        assert saved["state"] == "interrupted" and saved["results"] == interrupted["results"]
        assert restarted.app.state.run_manager.task is None  # never replay


@pytest.mark.parametrize("partial", [False, True])
def test_failed_jobs_release_slot_and_preserve_progress(client, partial):
    async def failing(model, settings, **kwargs):
        if partial:
            kwargs["progress"](
                TriageResult(
                    ticket_id=1,
                    state="partial",
                    operations=[Operation(tool="update_ticket", outcome="uncertain")],
                )
            )
        raise RuntimeError("SYNTHETIC-SECRET raw provider body")

    configure_runner(client, failing)
    run_id = client.post("/triage/runs", json={}).json()["run_id"]
    result = wait_run(client, run_id)
    assert result["state"] == "failed"
    assert "SYNTHETIC-SECRET" not in json.dumps(result)
    assert bool(result["results"]) is partial
    another = client.post("/triage/runs", json={})
    assert another.status_code == 202
    wait_run(client, another.json()["run_id"])


def test_incomplete_job_not_success(write_client):
    async def runner(model, settings, **kwargs):
        assert kwargs["apply"] and not settings.read_only
        return [TriageResult(ticket_id=1, state="failed", message="Synthetic safe failure")]

    configure_runner(write_client, runner)
    run_id = write_client.post("/triage/runs", json={"mode": "apply", "limit": 1}).json()["run_id"]
    assert wait_run(write_client, run_id)["state"] == "incomplete"


def test_unconfigured_model_is_clear_error(client):
    def invalid():
        raise ValueError("SYNTHETIC-SECRET")

    client.app.state.run_manager.model_loader = invalid
    response = client.post("/triage/runs", json={})
    assert response.status_code == 503
    assert "OPENAI_MODEL" in response.text and "SYNTHETIC-SECRET" not in response.text
    assert client.get("/audit-log").json()["events"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"limit": 0},
        {"limit": 21},
        {"limit": True},
        {"mode": "send"},
        {"ticket_id": -1},
        {"base_url": "http://evil.test"},
        {"api_key": "secret"},
        {"output": "../../evil.json"},
    ],
)
def test_run_payload_cannot_select_config(client, payload):
    assert client.post("/triage/runs", json=payload).status_code == 422


def test_missing_source_ticket_not_accepted(client):
    assert client.post("/triage/runs", json={"ticket_id": 999}).status_code == 404


def test_concurrent_approvals_only_one_commit(write_client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    draft_id = new_draft(write_client)
    barrier = Barrier(2)

    def approve_once():
        barrier.wait(timeout=5)
        return write_client.post(
            f"/tickets/1/drafts/{draft_id}/approve", json={"revision": 1}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: approve_once(), range(2)))
    assert sorted(results) == [200, 409]
    assert [e["action"] for e in write_client.get("/audit-log").json()["events"]] == [
        "draft_approved"
    ]


def test_database_unique_job_slot_and_state_consistency(client):
    from sqlalchemy.exc import IntegrityError

    factory = client.app.state.session_factory

    def record(**changes):
        return TriageRun(
            id=str(uuid4()),
            state="running",
            active_slot=1,
            mode="preview",
            limit=1,
            model="synthetic",
            prompt_version="test",
            **changes,
        )

    with factory.begin() as session:
        session.add(record())
    with factory() as session:
        session.add(record())
        with pytest.raises(IntegrityError):
            session.commit()
    with factory() as session:
        invalid = record()
        invalid.active_slot = None
        session.add(invalid)
        with pytest.raises(IntegrityError):
            session.commit()


def test_failed_finish_keeps_claim_and_does_not_leak(client, caplog, monkeypatch):
    manager = client.app.state.run_manager

    def failure(*args, **kwargs):
        raise SQLAlchemyError("SYNTHETIC-SECRET")

    monkeypatch.setattr(manager, "_finish", failure)
    manager._safe_finish("unused", "failed")
    assert "SYNTHETIC-SECRET" not in caplog.text
    assert "journal unavailable" in caplog.text


def test_audit_pagination_ceiling_signals_incomplete(client):
    from sqlalchemy import insert

    with client.app.state.session_factory.begin() as session:
        session.execute(insert(BackendAudit), [{"action": "triage_completed"}] * 10_002)
    event = {
        "call_id": str(uuid4()),
        "tool": "get_ticket",
        "event": "started",
        "read_only": True,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    client.app.state.mcp_settings.audit_path.write_text((json.dumps(event) + "\n") * 10_002)
    for source in ("backend", "mcp"):
        page = client.get(f"/audit-log?source={source}&offset=10000&limit=1").json()
        assert len(page["events"]) == 1
        assert page["next_offset"] is None and page["scan_truncated"] is True
