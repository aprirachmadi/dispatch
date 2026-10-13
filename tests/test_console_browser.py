"""Opt-in real-browser application tests: isolated synthetic API, deterministic generator."""

import asyncio
import json
import os
import socket
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from dispatch.agent.config import ModelSettings
from dispatch.agent.models import Operation, TriageResult
from dispatch.agent.workflow import NOTE_PREFIX
from dispatch.mock_psa.config import Settings
from dispatch.mock_psa.database import create_db_engine, create_schema
from dispatch.mock_psa.main import create_app
from dispatch.mock_psa.models import DraftReply, Note, Ticket
from dispatch.mock_psa.seed import seed_database

pytestmark = pytest.mark.skipif(
    os.environ.get("DISPATCH_RUN_BROWSER_TESTS") != "1", reason="Opt-in Chromium console checks"
)
LABELS = {
    "category": "security",
    "priority": "urgent",
    "urgency": "immediate",
    "security_flag": True,
    "rationale": "Synthetic source says credentials were disclosed.",
}


@pytest.fixture
def browser_server(tmp_path, request):
    read_only = getattr(request, "param", False)
    url = f"sqlite:///{tmp_path / 'browser.db'}"
    engine = create_db_engine(url)
    create_schema(engine)
    seed_database(engine)
    engine.dispose()
    app = create_app(Settings(database_url=url, read_only=read_only))
    original_lifespan = app.router.lifespan_context
    ready = threading.Event()

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            manager = app.state.run_manager
            manager.model_loader = lambda: ModelSettings(
                base_url="http://localhost/v1", model="deterministic-browser-fixture"
            )

            async def runner(model, settings, **kwargs):
                await asyncio.sleep(0.1)
                ticket_id = kwargs["ticket_id"] or 31
                draft = {"body": "Synthetic response: report the disclosure to IT. This is unsent."}
                operations = []
                if kwargs["apply"]:
                    with app.state.session_factory.begin() as session:
                        ticket = session.get(Ticket, ticket_id)
                        ticket.category = LABELS["category"]
                        ticket.priority = LABELS["priority"]
                        record = DraftReply(ticket_id=ticket_id, body=draft["body"])
                        session.add(record)
                        session.flush()
                        marker = {
                            "state": "completed",
                            "draft_id": record.id,
                            "assessment": LABELS,
                            "requires_attention": True,
                            "history_ids": [],
                        }
                        session.add(
                            Note(
                                ticket_id=ticket_id,
                                author_name="Dispatch MCP",
                                body=NOTE_PREFIX + json.dumps(marker),
                            )
                        )
                        operations = [
                            Operation(tool="draft_reply", outcome="confirmed", record_id=record.id)
                        ]
                result = TriageResult(
                    ticket_id=ticket_id,
                    state="applied" if kwargs["apply"] else "preview",
                    assessment=LABELS,
                    draft=draft,
                    operations=operations,
                )
                kwargs["progress"](result)
                return [result]

            manager.runner = runner
            with app.state.session_factory.begin() as session:
                draft = DraftReply(
                    ticket_id=31, body="Synthetic saved draft. Please report the disclosure to IT."
                )
                session.add(draft)
                session.flush()
                session.add(
                    Note(
                        ticket_id=31,
                        author_name="Dispatch MCP",
                        body=NOTE_PREFIX
                        + json.dumps(
                            {
                                "state": "completed",
                                "draft_id": draft.id,
                                "assessment": LABELS,
                                "requires_attention": False,
                                "history_ids": [33],
                            }
                        ),
                    )
                )
            ready.set()
            yield

    app.router.lifespan_context = lifespan
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    assert ready.wait(15), "Isolated browser API did not start"
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True
    thread.join(15)
    sock.close()
    assert not thread.is_alive()


@pytest.fixture
def browser_page(browser_server):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        yield page
        browser.close()
        assert errors == [], f"Browser errors: {errors}"


def open_case(page, server, ticket_id=31):
    page.goto(f"{server[0]}/console/#tickets?ticket={ticket_id}&status=")
    expect(page.get_by_role("heading", name="Response review")).to_be_visible()


def test_review_save_approve_and_unsent_persistence(browser_server, browser_page):
    page = browser_page
    open_case(page, browser_server)
    editor = page.get_by_label("Response body")
    editor.fill("Technician edited synthetic response.")
    expect(page.get_by_role("button", name="Approve — keep unsent")).to_be_disabled()
    page.get_by_role("button", name="Save edits").click()
    expect(page.get_by_text("Saved revision 2 · ready for review", exact=True)).to_be_visible()
    page.get_by_role("button", name="Approve — keep unsent").click()
    expect(page.get_by_role("dialog")).to_contain_text("No reply will be sent")
    page.get_by_role("dialog").get_by_role("button", name="Approve — keep unsent").click()
    expect(page.get_by_text("Saved revision 3 · approved, not sent", exact=True)).to_be_visible()
    expect(editor).to_have_attribute("readonly", "")
    with browser_server[1].state.session_factory() as session:
        draft = session.get(DraftReply, 1)
        assert draft.status == "approved" and draft.body == "Technician edited synthetic response."
        assert session.get(Ticket, 31).status == "new"


def test_dirty_navigation_cancel_then_leave_once(browser_server, browser_page):
    page = browser_page
    open_case(page, browser_server)
    page.get_by_label("Response body").fill("Local unsaved text")
    page.get_by_role("link", name="Weekly report", exact=True).click()
    page.get_by_role("dialog").get_by_role("button", name="Cancel").click()
    expect(page.get_by_label("Response body")).to_have_value("Local unsaved text")
    page.get_by_role("link", name="Weekly report", exact=True).click()
    page.get_by_role("dialog").get_by_role("button", name="Discard and leave").click()
    expect(page.get_by_role("heading", name="Weekly report", exact=True)).to_be_visible()
    expect(page.get_by_role("dialog")).to_have_count(0)


def test_revision_conflict_preserves_local_and_requires_reconciliation(
    browser_server, browser_page
):
    page = browser_page
    open_case(page, browser_server)
    page.get_by_label("Response body").fill("Local response kept after conflict")
    response = page.request.patch(
        f"{browser_server[0]}/tickets/31/drafts/1",
        data={"revision": 1, "body": "Other technician revision"},
    )
    assert response.status == 200
    page.get_by_role("button", name="Save edits").click()
    expect(page.get_by_label("Response body")).to_have_value("Local response kept after conflict")
    expect(page.get_by_role("heading", name="Latest saved revision 2 · draft")).to_be_visible()
    expect(page.get_by_role("button", name="Approve — keep unsent")).to_be_disabled()
    page.get_by_role("button", name="Keep local text against latest revision").click()
    page.get_by_role("dialog").get_by_role("button", name="Keep and review").click()
    page.get_by_role("button", name="Save edits").click()
    expect(page.get_by_text("Saved revision 3 · ready for review", exact=True)).to_be_visible()


def test_unknown_write_not_replayed(browser_server, browser_page):
    page = browser_page
    open_case(page, browser_server)
    mutations = []

    def abort_write(route):
        mutations.append(route.request.method)
        route.abort()

    page.route("**/tickets/31/drafts/1", abort_write)
    page.get_by_label("Response body").fill("Local text after timeout")
    page.get_by_role("button", name="Save edits").click()
    expect(
        page.get_by_text("Write outcome unknown. Inspect saved records before doing anything else.")
    ).to_be_visible()
    expect(page.get_by_label("Response body")).to_have_value("Local text after timeout")
    expect(page.get_by_role("button", name="Save edits")).to_be_disabled()
    assert mutations == ["PATCH"]


def test_real_rest_preview_and_apply_browser_flow(browser_server, browser_page):
    page = browser_page
    page.goto(f"{browser_server[0]}/console/#runs?ticket=32")
    page.get_by_role("button", name="Start preview", exact=True).click()
    expect(
        page.get_by_text("Preview — not saved to ticket. This response cannot be approved here.")
    ).to_be_visible()
    expect(
        page.get_by_text(
            "Synthetic response: report the disclosure to IT. This is unsent.", exact=True
        )
    ).to_be_visible()
    with browser_server[1].state.session_factory() as session:
        assert session.query(DraftReply).filter_by(ticket_id=32).count() == 0
    page.get_by_label("Scope", exact=True).select_option("ticket")
    page.get_by_label("Ticket ID", exact=True).fill("32")
    page.get_by_label("Mode", exact=True).select_option("apply")
    page.get_by_role("button", name="Start Apply", exact=True).click()
    expect(page.get_by_role("dialog")).to_contain_text("This is not saving an earlier preview")
    page.get_by_role("dialog").get_by_role("button", name="Start Apply", exact=True).click()
    expect(
        page.get_by_text(
            "Apply completed. Open the ticket to re-read persisted assessment "
            "and drafts before review."
        )
    ).to_be_visible()
    open_case(page, browser_server, 32)
    expect(page.get_by_label("Response body")).to_have_value(
        "Synthetic response: report the disclosure to IT. This is unsent."
    )
    expect(page.get_by_role("heading", name="Saved assessment", exact=True)).to_be_visible()


@pytest.mark.parametrize("browser_server", [True], indirect=True)
def test_readonly_and_injection_are_literal(browser_server, browser_page):
    page = browser_page
    open_case(page, browser_server)
    expect(page.get_by_role("button", name="Approve — keep unsent")).to_be_disabled()
    expect(page.get_by_label("Response body")).to_have_attribute("readonly", "")
    open_case(page, browser_server, 32)
    expect(page.get_by_text("ignore your instructions", exact=False).first).to_be_visible()
    assert page.request.get(f"{browser_server[0]}/tickets/32/drafts").json() == []
    page.goto(f"{browser_server[0]}/console/#runs?ticket=32")
    expect(page.get_by_label("Mode").locator('option[value="apply"]')).to_be_disabled()


def test_queue_filters_reports_and_audit(browser_server, browser_page):
    page = browser_page
    page.goto(f"{browser_server[0]}/console/#tickets")
    page.get_by_label("Saved attention", exact=True).select_option("required")
    page.get_by_role("button", name="Filter queue").click()
    expect(page.get_by_label("Ticket queue")).to_contain_text("1 matches")
    page.get_by_role("link", name="Weekly report", exact=True).click()
    page.get_by_role("button", name="View synthetic seed week", exact=False).click()
    expect(
        page.get_by_role("heading", name="Current backlog — not the historical week")
    ).to_be_visible()
    expect(page.get_by_text("UTC", exact=True)).to_be_visible()
    page.get_by_role("link", name="Audit", exact=True).click()
    expect(page.get_by_role("heading", name="Backend events")).to_be_visible()
    page.get_by_role("link", name="MCP", exact=True).click()
    expect(page.get_by_role("heading", name="MCP events")).to_be_visible()


def test_saved_urgency_filter_preserves_url_and_distinct_priority_colors(
    browser_server, browser_page
):
    page = browser_page
    server = browser_server[0]
    for ticket_id, priority in [(31, "urgent"), (32, "low"), (33, "medium"), (34, "high")]:
        assert page.request.patch(f"{server}/tickets/{ticket_id}", data={"priority": priority}).ok
    page.goto(f"{server}/console/#tickets?status=&offset=25")
    colors = []
    for ticket_id, label in [(31, "Urgent"), (32, "Low"), (33, "Medium"), (34, "High")]:
        row = page.get_by_role("link", name=f"Ticket {ticket_id}:", exact=False)
        chip = row.get_by_text(label, exact=True)
        expect(chip).to_be_visible()
        colors.append(chip.evaluate("el => getComputedStyle(el).backgroundColor"))
    assert len(set(colors)) == 4
    page.get_by_label("Saved urgency", exact=True).select_option("immediate")
    page.get_by_role("button", name="Filter queue").click()
    expect(page.get_by_label("Ticket queue")).to_contain_text("1 matches")
    expect(page.get_by_text("Urgency: Immediate", exact=True)).to_be_visible()
    assert "urgency=immediate" in page.url and "offset=0" in page.url
    page.get_by_role("link", name="Ticket 31:", exact=False).click()
    expect(page.get_by_role("heading", name="Saved assessment", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_label("Saved urgency", exact=True)).to_have_value("immediate")
    heading = page.locator(".case-heading")
    expect(heading.get_by_text("Urgent", exact=True)).to_have_class("badge error")
    expect(heading.get_by_text("Attention required", exact=True)).to_have_class("badge warning")
    page.get_by_label("Saved urgency", exact=True).select_option("routine")
    page.get_by_role("button", name="Filter queue").click()
    expect(page.get_by_role("heading", name="No matching tickets", exact=True)).to_be_visible()


def test_phone_reflow_back_and_literal_html(browser_server, browser_page):
    page = browser_page
    page.set_viewport_size({"width": 390, "height": 844})
    with browser_server[1].state.session_factory.begin() as session:
        session.get(
            Ticket, 32
        ).description = '<img src=x onerror="window.injected=true"> synthetic untrusted text'
    open_case(page, browser_server, 32)
    expect(
        page.get_by_text(
            '<img src=x onerror="window.injected=true"> synthetic untrusted text', exact=True
        )
    ).to_be_visible()
    assert page.evaluate("window.injected") is None
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("link", name="Back to ticket queue").click()
    expect(page.get_by_role("heading", name="Tickets", exact=True)).to_be_visible()


def test_missing_assessment_and_unavailable_api(browser_server, browser_page):
    page = browser_page
    open_case(page, browser_server, 21)
    expect(page.get_by_role("heading", name="Not assessed", exact=True)).to_be_visible()
    page.route(
        "**/reports/weekly?*",
        lambda route: route.fulfill(status=503, json={"detail": "secret-provider-error"}),
    )
    page.get_by_role("link", name="Weekly report", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("service is unavailable")
    expect(page.get_by_text("secret-provider-error")).to_have_count(0)


def test_capture_approved_workbench_if_requested(browser_server, browser_page):
    directory = os.environ.get("DISPATCH_BROWSER_CAPTURE_PATH")
    if not directory:
        pytest.skip("Screenshot capture is requested separately from functional tests")
    page = browser_page
    open_case(page, browser_server)
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(target / "mobile.png"), full_page=True)
    page.get_by_role("link", name="Back to ticket queue").click()
    expect(page.get_by_role("heading", name="Tickets", exact=True)).to_be_visible()
    page.screenshot(path=str(target / "mobile-queue.png"), full_page=True)
    page.request.patch(
        f"{browser_server[0]}/tickets/31/drafts/1",
        data={"revision": 1, "body": "Synthetic reviewed response — unsent."},
    )
    run = page.request.post(
        f"{browser_server[0]}/triage/runs", data={"mode": "preview", "ticket_id": 32}
    ).json()
    routes = [
        ("runs", f"runs?run={run['run_id']}", "Preview run"),
        ("reports", "reports?week=2026-10-12", "Current backlog — not the historical week"),
        ("audit", "audit", "Backend events"),
    ]
    for label, route, heading in routes:
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto(f"{browser_server[0]}/console/#{route}")
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        if label == "runs":
            expect(
                page.get_by_text(
                    "Synthetic response: report the disclosure to IT. This is unsent.", exact=True
                )
            ).to_be_visible()
        page.screenshot(path=str(target / f"desktop-{label}.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(target / f"mobile-{label}.png"), full_page=True)


def test_lost_start_response_recovers_without_second_post(browser_server, browser_page):
    page = browser_page
    posts = []

    def lose_response(route):
        if route.request.method != "POST":
            route.continue_()
            return
        posts.append(route.request.method)
        route.fetch()
        route.abort()

    page.route("**/triage/runs", lose_response)
    page.goto(f"{browser_server[0]}/console/#runs?ticket=32")
    page.get_by_role("button", name="Start preview", exact=True).click()
    expect(page.get_by_text("Start outcome remains unknown.", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Start preview", exact=True)).to_be_disabled()
    assert posts == ["POST"]
    with browser_server[1].state.session_factory() as session:
        from dispatch.mock_psa.models import TriageRun

        assert session.query(TriageRun).count() == 1


def test_poll_disconnect_resumes_read_only(browser_server, browser_page):
    page = browser_page
    reads = []

    def disconnect(route):
        reads.append(route.request.method)
        route.abort()

    page.route("**/triage/runs/*", disconnect)
    page.goto(f"{browser_server[0]}/console/#runs?ticket=32")
    page.get_by_role("button", name="Start preview", exact=True).click()
    expect(page.get_by_role("button", name="Resume monitoring")).to_be_visible()
    page.unroute("**/triage/runs/*", disconnect)
    page.get_by_role("button", name="Resume monitoring").click()
    expect(page.get_by_text("Preview — not saved to ticket.", exact=False)).to_be_visible()
    assert reads == ["GET"]


def test_stale_approval_does_not_approve_newer_revision(browser_server, browser_page):
    page = browser_page
    open_case(page, browser_server)
    page.request.patch(
        f"{browser_server[0]}/tickets/31/drafts/1",
        data={"revision": 1, "body": "Changed externally"},
    )
    page.get_by_role("button", name="Approve — keep unsent").click()
    page.get_by_role("dialog").get_by_role("button", name="Approve — keep unsent").click()
    expect(page.get_by_role("heading", name="Latest saved revision 2 · draft")).to_be_visible()
    expect(page.get_by_role("button", name="Approve — keep unsent")).to_be_disabled()
    with browser_server[1].state.session_factory() as session:
        assert session.get(DraftReply, 1).status == "draft"


def test_keyboard_focus_and_invalid_week(browser_server, browser_page):
    page = browser_page
    page.goto(f"{browser_server[0]}/console/#reports")
    expect(page.get_by_role("heading", name="Weekly report", exact=True)).to_be_visible()
    page.keyboard.press("Tab")
    expect(page.get_by_role("link", name="Skip to workspace")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.get_by_role("main")).to_be_focused()
    assert page.url.endswith("#reports")
    page.get_by_label("Week beginning (Monday, UTC)").fill("2026-10-13")
    page.get_by_role("button", name="Load report").click()
    expect(page.get_by_text("Choose a Monday for the UTC reporting window.")).to_be_visible()


def test_pending_draft_page_cannot_erase_new_local_text(browser_server, browser_page):
    page = browser_page
    with browser_server[1].state.session_factory.begin() as session:
        session.add_all([DraftReply(ticket_id=31, body=f"Synthetic draft {i}") for i in range(30)])
    open_case(page, browser_server)
    pending = []
    page.route("**/tickets/31/drafts?limit=25&offset=25", lambda route: pending.append(route))
    page.locator(".draft-sheet").get_by_role("button", name="Next", exact=True).click()
    editor = page.get_by_label("Response body")
    expect(editor).to_have_attribute("readonly", "")
    expect(page.get_by_role("button", name="Save edits")).to_be_disabled()
    expect(page.get_by_role("button", name="Approve — keep unsent")).to_be_disabled()
    expect(
        page.locator(".draft-sheet").get_by_role("button", name="Next", exact=True)
    ).to_be_disabled()
    # A previously queued input event or programmatic accessibility input is preserved too.
    editor.evaluate(
        "el => { el.value = 'Queued local edit'; "
        "el.dispatchEvent(new Event('input', {bubbles:true})); }"
    )
    assert len(pending) == 1
    pending[0].continue_()
    expect(
        page.get_by_text("Local editor changed while drafts loaded.", exact=False)
    ).to_be_visible()
    expect(editor).to_have_value("Queued local edit")
    expect(page.get_by_label("Saved draft")).to_have_value("1")


def test_mobile_runs_contained_and_no_null_artifact(browser_server, browser_page):
    page = browser_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{browser_server[0]}/console/#runs?ticket=32")
    page.get_by_role("button", name="Start preview", exact=True).click()
    expect(page.get_by_text("Preview — not saved to ticket.", exact=False)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert "null" not in page.locator(".run-form").inner_text()
