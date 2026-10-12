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
