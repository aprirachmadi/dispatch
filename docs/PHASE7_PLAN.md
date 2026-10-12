# Phase 7 — technician console plan

Status: user approved full-console implementation with the Service case-file direction
and code-first workflow on 2026-10-13. The frontend and three additive read contracts
are implemented; verification evidence is recorded separately in DECISIONS.md.

## 1. Job, audience, and outcome

Mode: **Operate**. An IT technician must turn a synthetic incoming ticket into a
reviewed, approved-but-unsent response, understanding the source evidence,
classification, write effects, and unresolved risk at every step.

The first viewport should show usable work, not marketing copy or decorative KPI
cards. Primary navigation: **Tickets**, **Triage runs**, **Weekly report**, **Audit**.
Always identify the app as a synthetic local demo. No fake signed-in technician.

## 2. Stack and delivery

- Semantic HTML, CSS tokens/layouts, and native JavaScript ES modules in `web/`.
- Serve the console at `/console/` through existing FastAPI static-file support;
  keep every current API route and `/docs` intact. Use hash-based navigation under
  that prefix so refresh/deep links need no catch-all that could swallow API errors.
- Relative, same-origin REST requests using Fetch; no CORS wildcard or second dev
  server. No model endpoint, API key, database path, or MCP access in the browser.
- Poetry remains the runtime/dependency manager. No React, Node build pipeline,
  CSS framework, CDN scripts/fonts, new ORM, or Docker.
- Separate modules for API/error handling, navigation/state, queue, detail/review,
  jobs, reports, audit, and shared accessible controls. Avoid one monolithic script
  and avoid inventing a generic component framework.
- Serve only intended UI assets, never repository root, `.env`, data, docs, or
  development design metadata. Resolve paths independent of shell working directory;
  verify asset availability in the supported installed-package launch path.
- Code-first: no generated mockup required before coding. Visual craft remains an
  acceptance requirement, not something postponed until after function is finished.

This recommendation uses existing capabilities: [FastAPI static files](https://fastapi.tiangolo.com/tutorial/static-files/),
[native JavaScript modules](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Modules),
and [Fetch](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch).
Use APIs supported by the installed FastAPI version; do not assume newer convenience
methods in online documentation are available without checking.

## 3. Screens and primary flow

### Tickets: queue and workbench

- Default to new tickets; explicit All/status/company/category/priority filters and
  literal subject/description search. Show result scope, pagination, stable order,
  and clear empty-filter versus empty-database states. No invented global counts.
- Rows: ticket ID, subject, company, saved category/priority/status, saved attention
  state, and draft-review summary. Missing saved assessment says **Not assessed**;
  it is not a reassuring green badge. Attention is a model-derived review cue, not
  proof the ticket is safe or that an incident has been escalated.
- Selected ticket: original description and notes, company/contact/SLA tier,
  saved fields, latest valid saved assessment/rationale/date/linked draft, and history
  ticket links. Never call similarity a confidence score. No calculated SLA countdown.
- Draft area: list all drafts with ID/revision/time/status; emphasize the draft linked
  to the saved assessment without implying other drafts are deleted or superseded.
  Large plain-text editor, explicit Save edits, unsaved-change guard, read-only
  approved state, and visible **Approved — not sent** outcome.
- Preview area remains separate from persisted assessment and persisted drafts.
  Label its result **Preview — not saved to ticket**. Draft review/approval applies
  only to an actual saved draft; do not offer approval on transient preview text.
- Human-edit category/priority may use existing guarded PATCH if included in the
  confirmed build scope. It is separate from model assessment: there is no technician
  urgency/security override API or revision-checked ticket-label update today. Do not
  imply editing labels rewrites the saved model assessment or eliminates its flag.

### Triage runs

- Explicit single-ticket or bounded new-ticket queue scope, batch limit 1–20, default
  Preview. Show server-selected model/prompt version when a run is available.
- Apply confirmation names its scope and effects: regenerate; update category/priority;
  create an unsent draft and completion note; no approval, sending, status change,
  paging, or remediation. **Apply is not “save this exact preview.”**
- One request per deliberate start. Disable local duplicate submission. The server
  remains authoritative for the global active slot and read-only/write policy.
- Poll returned run ID while running; stop at terminal state. Display per-ticket
  preview/applied/skipped/failed/partial result and operation intentions/confirmations.
  A completed job containing only skips is not “new drafts generated.”
- Show elapsed time/last successful poll, never fabricated percentages or workflow
  steps the API does not emit. Leaving the page or stopping polling does not cancel
  the job. No cancellation control or automatic write retry.
- After Apply, re-fetch source fields, assessment, drafts, queue, and run state. Show
  actual saved records, not optimistic replacement with generated text.

### Weekly report

- Monday date selector and optional company filter. Initial week follows current
  UTC date; make the fixed synthetic seed week discoverable without silently changing
  dates or pretending the dataset is current operational traffic.
- Show opened/resolved counts and category/priority breakdowns using API values.
  Keep **current backlog as of [timestamp]** separate from the selected week.
- Display UTC window, generated timestamp, incomplete-period indicator, and scope.
  Use readable bars/tables with numeric labels; no invented trends/percentage changes,
  response-time metrics, SLA compliance, or attention totals.

### Audit

- Separate Backend and MCP tabs/pages with source-labelled entries, timestamps,
  available ticket/draft/run links, and independent pagination.
- Honor next_offset, malformed-line count and scan_truncated warnings. No merged
  total ordering, fabricated actor identity, prompt/draft bodies, or tamper-proof claim.
- Empty, unavailable, and incomplete scan are distinct states.

## 4. Existing API mapping

| UI task | Existing contract |
| --- | --- |
| Ticket reads and filters | `GET /tickets`, `GET /tickets/{id}` |
| Company/contact context | `GET /companies`, `GET /companies/{id}`, `GET /contacts?company_id=...` |
| Saved assessment | `GET /tickets/{id}/assessment`; 404 can be normal absence |
| Saved draft pages | `GET /tickets/{id}/drafts?limit=...&offset=...` |
| Save draft | `PATCH /tickets/{id}/drafts/{draft_id}` with body + reviewed revision |
| Approve saved revision | `POST /tickets/{id}/drafts/{draft_id}/approve` with revision |
| Explicit label edit, if included | `PATCH /tickets/{id}` with category/priority only |
| Start/poll generation | `POST /triage/runs`, `GET /triage/runs/{run_id}` |
| Report | `GET /reports/weekly?week_start=YYYY-MM-DD[&company_id=...]` |
| Audit source page | `GET /audit-log?source=backend|mcp&limit=...&offset=...` |

## 5. Additive backend reads approved with the implementation

The full console adds three technician-only read contracts, implemented and tested
before frontend verification.
Preserve legacy REST shapes, MCP DTOs, tools, and write policy.

1. `GET /console/config`: minimal public capability information (read-only mode),
   with no credentials, endpoint URLs or paths. Model availability is not inferred
   from `/health`; configuration/start failures remain explicit 503 responses.
2. `GET /console/tickets`: paginated envelope with ticket/company summary, latest
   valid saved-assessment attention state, draft/approval summary, and truthful
   matching count/next offset. Reuse the assessment validation/derived-attention logic
   and its bounded latest-100-marker semantics. Support existing filters plus a saved
   attention/not-assessed filter; filter before pagination. No per-row browser fan-out
   and no inference call. No automatic background scan of every detail endpoint.
3. `GET /triage/runs`: bounded, newest-first persisted run summaries with run IDs,
   mode/state/scope/time and pagination. Enables active-job discovery and recovery
   across browser reload, lost start responses, and other local tabs. Keep details
   on the existing by-ID route. No resubmission to recover an unknown outcome.

These reads require no schema migration or new dependency. An absent assessment
is null/unknown, never false. Any count/filter must describe **saved assessments**,
not all possibly urgent tickets. Test the shared projection against corrupt markers,
linked drafts, and manual category/priority changes. Do not widen MCP permissions.

## 6. State, reliability, and security rules

- Server snapshots and local editor text are separate state. Track dirty body and
  reviewed revision; do not erase unsaved edits on polling/navigation/reload conflicts.
- Approve requires a clean saved editor and explicit confirmation of exactly that
  revision. On 409 preserve local text, reload latest saved revision, and require
  renewed review; never silently overwrite, auto-merge, or approve a newer revision.
- On mutation timeout/disconnect report **Outcome unknown — inspect saved state**.
  Never automatically replay PATCH/POST. Offer safe read refresh/reconciliation.
- Start timeout: recover through the run list, not another POST. Polling failure:
  show disconnected/last-known state, keep run ID, offer Resume monitoring (GET only).
- 403: writes disabled by server; do not offer a browser toggle. 409 start: another
  job active; discover it. 422: accessible field errors. 503: redacted configuration/
  backend problem. 404 ticket differs from normal absent assessment. Error text is
  safe, useful, and never presents raw provider responses/secrets.
- Race-safe reads: abort obsolete navigations/search requests and ignore stale
  responses. Prevent overlapping polls and stop polling when its view is disposed.
- Render all dynamic text through textContent/DOM text nodes or textarea values,
  not interpolated innerHTML, Markdown/HTML rendering, eval, or automatic URL actions.
- No browser-persisted ticket text, editor content, credentials, or preview bodies.
  URL state may contain IDs/filter settings; run recovery uses server data.
- Existing notes include machine completion markers. Group those as system metadata
  without presenting them as technician messages; preserve source access.
- Loopback-only, no authentication claim. Same-origin frontend is not an authorization
  mechanism. No new security exemptions, permissive CORS, or public hosting setup.

## 7. Selected visual and interaction direction — Service case-file

Physical scene assumption: a technician reviewing tickets on a desktop in an ordinarily
lit office. Light surfaces and legible dense text are recommended; this is a design
assumption, not a known user accessibility preference. Dark mode is not required now.

The user selected Service case-file: source evidence next to a generous draft review
sheet, with revision identity on the action footer. This defines the console's visual
grammar without changing backend truth or safety policy.

Regardless of direction: readable system sans, restrained action color distinct from
warning/error semantics, consistent controls, no decorative gradients/AI sparkle,
no fake chatbot, giant marketing heading, dashboard-card carpet, or success confetti.
The signature interaction should make saved-versus-preview state and reviewed
revision unmistakable, not merely animate a generic panel.

Desktop: persistent navigation, scan-friendly queue, source evidence next to response
review when space allows. Tablet: queue/detail navigation without a cramped editor.
Phone: one logical pane at a time, preserved Back/filter context, wrapped plain text,
and visible action scope. Do not force a desktop three-column layout onto a phone.

Accessibility acceptance: keyboard-complete workflow, semantic headings/forms/tables,
visible focus, labelled controls, status text beyond color, focus restored after
confirmations, restrained live announcements (not every poll), reduced-motion behavior,
readable zoom/reflow, and adequately sized touch targets. Aim for WCAG AA contrast.

## 8. Implementation sequence and gates

1. **Read contract gaps + shell:** isolated application tests for additive reads;
   same-origin asset serving, navigation, tokens, shared loading/error/empty states.
2. **Ticket workbench:** queue/filter/pagination, source/company/contact, saved
   assessment/attention and linked drafts. Verify #31, duplicates #21/#22, injection #32.
3. **Draft review:** local edit state, explicit save, conflict/reconciliation, exact-
   revision approval, immutable approved-unsent display and guards against dirty exits.
4. **Triage workflow:** preview default, explicit Apply scope, background monitoring,
   recovery, partial/uncertain states, truthful saved-state refresh, bounded batches.
5. **Reports + audit:** source-faithful UTC report, current backlog separation,
   independent audit sources/pagination/warnings, cross-links.
6. **Integrated verification + documentation:** accessibility/responsive checks,
   end-to-end interaction evidence, backend regressions, launch instructions and
   DECISIONS updates. The user requested completion/testing without further design-workflow gates.

No screen is considered finished with only its happy path. Small increments can be
reviewed independently, but the approved scope remains all four console areas.

## 9. Acceptance and verification

- Existing API/MCP tests remain green and eight MCP tools keep their contracts.
- Add application tests for read capabilities/projections/run pagination and asset
  serving/route isolation. Draft approval/write-job security regression tests remain.
- Browser checks exercise search, filter, page, deep link/back, selection changes,
  unsaved edits, valid save, stale-revision 409, approve, repeated approval, read-only
  denial, unavailable API/model, active-job 409, missing assessment, empty report,
  malformed/truncated audit, long text, and injection/HTML-shaped text rendered literally.
- Deterministic browser workflow uses an isolated synthetic backend and model stand-in
  for deliberate failures; no tests write to the retained demo database. Browser
  tooling uses Playwright in the Poetry dev group; Chromium checks are opt-in.
- Manual real-model demo uses the existing database without reset/reseed. Reading
  existing data is allowed; an Apply/edit/approve demonstration explicitly identifies
  the synthetic record being changed and retains it. No inference during planning.
- Real preview and real Apply are distinct gates; capture the actual persisted
  assessment/unsent draft and subsequent exact-revision review. A previously applied
  ticket can skip, so use a deliberate new synthetic intake if needed, never reset.
- Inspect desktop and phone together once, batch fixes, confirm once; run Impeccable's
  mechanical detector at build finish. No screenshots/design pass claimed for planning.
- Run `poetry run pytest`, `poetry run ruff check .`,
  `poetry run ruff format --check .`, `poetry check --lock`, and `git diff --check`.

## 10. Explicit exclusions and resolved scope

Excluded: Docker/packaging phase, public deployment, login/roles, sending, ticket
status changes, bulk approval, automatic retries/remediation, online judge gating,
model training/selection/configuration screens, and a browser eval-report dashboard.

Resolved: Service case-file; the three additive reads; Poetry development-only
Playwright for opt-in browser verification. Manual category/priority editing is not
added to this UI; ticket fields remain separately visible and existing PATCH clients
remain supported. No new urgency/security override contract is introduced.
The full no-judge model run proves execution, not a safe-to-trust autonomous system.
