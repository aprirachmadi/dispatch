# Dispatch engineering decisions

## Phase 1 — approved 2026-10-12

### Step 1: layout, environment, and storage

- Use Python 3.12 and Poetry for dependencies and a project-local `.venv`.
  Commit `poetry.lock`; run commands with `poetry run`. No uv/pip workflow.
- Application code lives under `src/dispatch/` consistently. Future MCP and agent
  folders are placeholders only; evaluation assets stay in root `evals/`.
  The src layout makes tests exercise the installed package.
- Use synchronous SQLAlchemy 2 with SQLite. This is enough for a small local demo
  and makes model relationships explicit without an asynchronous DB stack.
- Enable SQLite foreign keys for every connection; store enum values with CHECK
  constraints. Store timestamps in UTC and restore timezone-aware values on reads.
- Create missing tables on startup/seed. No migration framework yet: schema changes
  need an explicit migration or a fresh local demo database, not silent updates.
- Use a small dataclass and `python-dotenv` for `.env` loading instead of a settings
  framework. Existing environment variables take precedence. No Phase 1 secrets.
- Model companies, contacts, tickets, and internal notes. A ticket can lack a contact
  (automated alert) or a category (not yet classified). Default priority is medium.
- Store SLA tiers only: Gold = 1h response, Silver = 4h, Bronze = next business day.
  Deadline calculations await agreed timezone, business hours, and holiday rules.
- All data is synthetic. No authentication: local **demo only**, not production.

### Step 2: REST boundaries

- API lifespan creates tables and owns the connection pool; importing the app does
  no configuration/database I/O. Each request has a short-lived synchronous session.
- `GET /companies`, `GET /contacts`, and `GET /tickets` return JSON arrays in stable
  ID order, defaulting to 100 records. Limits are 1–100; offset is 0–10,000.
  A list envelope/count can be considered when the UI phase needs it.
- Ticket lists filter by company/category/status. Detail includes internal notes;
  list results do not. No notes write endpoint or ticket update endpoint in Phase 1.
- `POST /tickets` always creates `new` tickets. Positive integer IDs, enum values,
  subject (1–200 characters), and description (1–10,000 characters) are validated;
  blank text and unexpected fields are rejected. Only outer whitespace is trimmed.
- Unknown company/contact/ticket IDs return 404; contact/company mismatch returns
  422. API ownership validation complements SQLite foreign keys. Company/contact
  mutations are not exposed in Phase 1.
- Ticket text, including injection attempts, is stored literally, never executed.
  This is not a full prompt-injection defense for the future agent; that belongs
  in later phases. No AI services, outgoing messages, or approval actions exist.

### Step 3: synthetic seed and verification

- Seed six explicitly fictional companies (two per SLA tier), 18 fictional contacts
  with `.example.test` email domains, 60 authored ticket scenarios (10 per category),
  and 37 internal notes. Human requests have messy wording; alerts have machine text.
- Include 24 new, 12 in-progress, 12 resolved, and 12 closed tickets. Historical
  tickets have specific resolution notes and timestamps for future similarity work.
  These are fixture values, not agent predictions or the future labeled eval set.
- Trap IDs in a fresh database: #31 casual credential compromise (urgent/security);
  #21/#22 duplicate printer reports from the same contact; #32 prompt injection.
  Preserve duplicates; no automatic deduplication or execution of embedded commands.
- Seeding uses a fixed UTC reference time (2026-10-12 09:00) for fully reproducible
  fixtures, rather than changing with each run. It is one transaction and refuses
  any nonempty database. No destructive reset flag or automatic startup seeding.
- Validate model/API/seed behavior using isolated on-disk SQLite databases, including
  startup, persistence across app restarts, trap preservation, and seed rollback.
- The installed Starlette TestClient emits a non-failing HTTPX deprecation warning.
  Keep the documented HTTPX client for now rather than adding a second test client
  solely to silence an upstream warning; no warning filters hide it.

### Verification

- 71 pytest cases pass, including the seed CLI's `.env` loading and refusal to
  reseed. Ruff lint/format, `poetry check --lock`, and whitespace checks pass.
- Started a real uvicorn process: seeded `GET /tickets` returned 60 tickets, the
  urgent security trap was preserved, and `POST /tickets` returned 201 with a
  subsequently readable new ticket. POST used a temporary database copy so the
  local demo retains exactly 60 seed tickets.

## Phase 2 — approved 2026-10-12

### Step 1: SDK, provider boundary, and REST reads

- Use the official `mcp` v2 SDK (`mcp>=2.3,<3`), initially locked to 2.3.0.
  The official v2 class is `MCPServer`, renamed from v1's `FastMCP`; do not use
  the separate third-party `fastmcp` framework. Poetry remains the environment manager.
- Use HTTPX2 for asynchronous REST reads. It is also required by the official v2
  SDK and supported by the installed Starlette TestClient, replacing old HTTPX
  rather than maintaining two clients. This also removes the earlier warning.
- `TicketProvider` is an asynchronous structural Protocol, with neutral validated
  request/response models. It exposes only the three Phase 2 reads; future methods
  will be added in their own approved phases. Tools know no REST or SQLAlchemy details.
- Shared enums move to `dispatch/domain.py`; old model imports are re-exported for
  compatibility. Neutral provider DTOs intentionally describe the integration
  contract separately from the mock API's ORM serialization models.
- The mock adapter uses REST, never SQLite. Add only single-company lookup and
  literal case-insensitive subject/description plus priority filtering to REST.
  All filters intersect before stable-ID pagination; SQL wildcard characters are
  escaped. SQLite case-insensitive matching is ASCII-oriented, not Unicode casefold.
- Search query: omitted for listing or 1–200 trimmed characters; limit 1–100
  (MCP default 25), offset 0–10,000. IDs are positive signed 64-bit integers.
- The adapter sends only GET, disables redirects, applies five-second per-operation
  network timeouts, caps each response at 5 MiB, and exposes safe errors rather than
  raw backend bodies/addresses. Notes and contacts returned by MCP are capped at
  100 and explicitly flag truncation. Company context includes only modeled data.
- No writes, audit log, configurable read-only flag, similarity, report, AI, UI,
  authentication, or Docker implementation in this phase.

### Step 2: MCP server and tools

- Expose exactly `search_tickets`, `get_ticket`, and `get_company_context` over stdio.
  SDK-generated schemas carry typed enums, strict IDs/limits, and bounded text.
  Return provider-neutral Pydantic DTOs as structured results; not-found/backend
  failures become model-readable tool errors through the SDK's `ToolError`.
- Use read-only, non-destructive, idempotent, closed-world tool annotations. These
  are descriptions, not enforcement: the actual adapter performs only bounded GETs.
- Tool and server descriptions explicitly identify ticket/notes/company text as
  untrusted. No content is executed or used as credentials, URLs, or authorization.
  This is still not a complete prompt-injection defense for a future AI agent.
- Validate extra arguments using the public request context because SDK-generated
  flat argument models otherwise ignore unknown fields. No SDK internals are patched.
- MCP lifespan owns and closes the HTTP client; tools access the injected provider
  via the SDK request context. Tests can substitute a provider without changing tools.
- Configuration permits HTTP(S) backend URLs without embedded credentials/query/
  fragments. The trusted `.env`/environment selects the backend, never tool input.
  Environment proxy configuration is disabled for the local backend connection.
- Keep stdout exclusively for protocol messages; diagnostics use the SDK's stderr
  logging. No network/database access occurs just by importing the server module.

### Step 3: real transport verification and run instructions

- Exercise a separate stdio MCP subprocess against a separate seeded uvicorn process,
  using temporary SQLite databases and automatic process cleanup. Test both SDK auto
  connection and legacy initialize-handshake mode for host interoperability.
- Compare every company/contact/ticket/note record before and after tool calls,
  including injection text, both duplicate tickets, missing records, invalid inputs,
  and attempts to call unregistered write tools. No persisted data may change.
- Bound REST POST reference IDs too: oversized integers previously could overflow
  SQLite; now reject them with 422 consistently with path/filter/MCP ID validation.
- README includes two-process launch instructions, trusted backend URL configuration,
  sample stdio host configuration, tool contracts, limitations, and test commands.
  No inspector/CLI extra, authentication stack, frontend, or AI dependency is added.

### Verification

- All 162 pytest cases pass without warnings, including the original Phase 1 suite.
  Ruff lint/format, `poetry check --lock`, and `git diff --check` pass.
- Real stdio discovery and reads pass in both auto and legacy-handshake modes against
  a separate seeded uvicorn process. All persisted records remain unchanged; the
  backend-unavailable path returns a safe tool error. Test subprocesses are cleaned up.
- The existing local demo database still has 60 tickets. It was not reseeded or
  modified by Phase 2 tests. SDK 2.3.0 and HTTPX2 2.13.1 are recorded in the lockfile.
