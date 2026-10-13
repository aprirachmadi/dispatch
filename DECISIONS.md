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

## Phase 3 — approved 2026-10-12

### Step 1: restricted writes and draft storage

- `update_ticket` may change category and priority only. No status transitions,
  subject/body edits, contact/company changes, approval, or sending operations.
- Add only a `draft_replies` table, preserving existing Phase 1/2 databases. Status
  is always `draft`, enforced by a database CHECK constraint as well as schemas.
  Future human approval in Phase 6 will need an explicit schema migration.
- Minimal REST writes: POST `/tickets/{id}/drafts`, POST `/tickets/{id}/notes`,
  PATCH `/tickets/{id}`. Bodies are nonblank, trimmed, at most 10,000 characters;
  unexpected fields and null/empty classification updates are rejected.
- `draft_reply` stores supplied text. No AI generation, external messaging, editing
  existing drafts, or approval endpoint is implemented. Notes have fixed author
  `Dispatch MCP` rather than accepting an impersonated author from tool input.
- `DISPATCH_READ_ONLY` defaults to true; only explicit true/false are accepted.
  The provider denies mutations by default, and REST independently guards tool-write
  routes. Existing POST `/tickets` intake is exempt. Both processes need write mode
  enabled for MCP mutations. No automatic retries after ambiguous write failures.

### Step 2: write tools, fail-closed policy, and audit journal

- Always advertise all six tools so clients see precise write schemas, even in
  read-only mode. SDK middleware blocks write dispatch before provider calls; tool,
  provider, and REST guards additionally enforce the policy. Mode is trusted process
  configuration, not a tool parameter; changes require restarting both processes.
- Audit decoded `tools/call` requests at the official SDK middleware boundary, before
  argument validation, including unknown tools, validation errors, policy rejections,
  backend failures, and cancellations. Other protocol messages are not tool calls.
- Audit JSONL is separate from PSA/provider storage so backend outages remain logged.
  Default file `data/audit.jsonl`; append with O_APPEND, restrictive creation mode,
  a single bounded write per event, and fsync. It is not tamper-proof or a distributed
  logging system; raw malformed JSON/transport failures cannot be decoded into calls.
- Persist `started` before execution and `finished` afterward, sharing a UUID call ID,
  UTC times, mode, safe tool label, outcome, and duration. Record only allowlisted IDs,
  enums, numeric bounds, argument count, and text lengths. No bodies/queries/results/
  exception messages/arbitrary keys, headers, or request IDs are copied into audit.
  Unknown/malformed tool names use `unknown_tool` rather than echoing untrusted text.
- If start auditing fails, reject without execution. If completion auditing fails,
  report that execution may have happened; the durable start remains evidence of an
  indeterminate call. Audit failure is not transactional rollback of a REST write.
  A tool error also does not guarantee a write had no effect; do not blindly retry.
- Read-only still appends audit records, while leaving PSA records unchanged. Approval,
  sending, UI audit endpoints, embeddings, reporting, and agents remain out of scope.

### Step 3: real-process writes and operating instructions

- Verify both SDK auto and legacy handshake transports with separately running backend
  and MCP processes. Check all three permitted writes, persisted draft status, invalid
  status/approval attempts, unchanged company/contact records, and complete redacted
  audit pairs. Verify MCP write mode cannot bypass a read-only backend.
- Declare AnyIO directly because audit code uses its worker-thread and cancellation
  APIs. It was already installed as a FastAPI/SDK runtime dependency; no new framework
  or package installation is introduced. Keep the existing Poetry lockfile workflow.
- README documents safe defaults, restarting both processes, host environment overrides,
  local JSONL inspection, audit failure uncertainty, draft verification, and restoring
  read-only mode. No early UI endpoints, external messages, agent, or AI API calls.

### Verification

- All 234 pytest cases pass without warnings; Ruff lint/format, Poetry lockfile checks,
  and whitespace checks pass. Tests use isolated SQLite and audit paths.
- Six real-process stdio cases pass, including safe writes in auto/legacy modes and
  MCP-write/backend-read-only mismatch. Drafts remain `draft`, status/approval attempts
  fail, and both allowed mutations and redacted audit outcomes are verified.
- Audit tests cover failed/unknown/invalid calls, cancellation, concurrent append,
  start failure preventing execution, and completion failure after a committed write.
- No demo reset/reseed or local write test was performed. The existing local database
  is retained; only temporary databases/journals were used for write verification.

## Phase 4 — approved 2026-10-12

### Step 1: local semantic similarity and bounded scans

- Add FastEmbed CPU/ONNX inference (0.9.x, locked by Poetry), not PyTorch, a vector
  database, an embedding API, or another agent framework. Declare Hugging Face Hub
  directly because pinned snapshot download uses its API (already a FastEmbed dependency).
- Model: `BAAI/bge-small-en-v1.5`, 384 dimensions, 512-token truncation. Pin its
  quantized ONNX repository `Qdrant/bge-small-en-v1.5-onnx-Q` to immutable revision
  `aa8f8b060edb00e03bfdd08813a2949946c8ba55`. Inference is local; only public model
  artifacts are downloaded on first use, with no token or ticket text transmitted.
- Embed exact subject + description only; never notes, draft replies, priority/category
  labels, or instructions from ticket text. Long inputs are truncated by the model's
  tokenizer at 512 tokens; this is similarity evidence, not full-ticket classification.
- Cache normalized vectors by model/preprocessing identity and SHA-256 of exact text.
  Store no plaintext in the cache; validate finite/nonzero vectors/dimensions, bound
  entries/file size, and write via same-directory atomic replacement. Vector storage
  is not anonymization; synthetic data only. No silent keyword fallback on model failure.
- Historical matches must be resolved/closed, have a resolution timestamp strictly
  before the source ticket's creation, and exclude the source ID. The user explicitly
  chose chronological history to prevent future-data leakage in later evaluations.
- Cosine scores are in [-1,1], not confidence percentages. Return at most ten matches,
  tied by ascending ticket ID. Search across synthetic companies unless company_id
  filters candidates; source ticket's company need not equal the candidate filter.
- Fetch through TicketProvider/REST only. Cap complete scans at 2000 combined records;
  use one-record lookahead, reject larger/unstable/invalid scans rather than returning
  misleading partial results. Unknown company filters return an error, not zero counts.

### Step 2: explicit UTC weekly calculations

- Require an explicit ISO Monday date. Use [Monday 00:00 UTC, following Monday 00:00
  UTC), not the technician's local timezone, and reject invalid/overflowing dates.
- `opened` counts creation timestamps in the window; `resolved` counts recorded
  resolution timestamps in the window, including tickets created before it. These
  are current record fields, not a complete status-transition event history.
- Category and priority breakdowns describe tickets opened in that window. Missing
  category is `unclassified`, not a guessed enum. Include zero-valued breakdown keys.
- Report current new/in-progress backlog separately, with an explicit as-of time.
  Do not claim to reconstruct historical week-end backlog. Mark whether the requested
  period has fully elapsed; current/future weeks may be incomplete in time.
- No SLA compliance, first-response times, average response metrics, forecasts, or
  AI-written narrative: those cannot be truthfully inferred from current models.

### Step 3: MCP wiring and read-only semantics

- Add `find_similar_tickets` and `weekly_report`, for eight total tools. Both have
  precise typed/validated schemas, read-only annotations, bounded inputs and outputs,
  and normal start/finish auditing, including failure paths. Audit allowlists ISO week
  metadata without copying arbitrary date strings or returned report/ticket bodies.
- Keep analytics above TicketProvider: no PSA ORM imports, direct SQL reads, or early
  UI/report REST endpoints. Use the existing paginated REST reads only.
- Configure separate trusted `DISPATCH_EMBEDDING_CACHE_PATH` and
  `DISPATCH_MODEL_CACHE_PATH` paths. Loading/creating the MCP server does not download
  a model. Reports and no-candidate similarity results also require no model download.
- Read-only is a PSA mutation policy; audit, model download, and local vector cache
  writes remain permitted, just like audit journaling in Phase 3. No agent, labeled
  triage evals, UI, Docker, external embedding inference, or auto-sending is added.
- Multi-page REST scans are not a transactional database snapshot. Ordering/duplicate
  and filter checks detect some concurrent changes; do not promise historical record
  versions or detection of every edit. The demo's bounded counts are observational.

### Verification

- All 294 pytest cases pass with real-model smoke tests explicitly enabled, without
  warnings. Ruff lint/format, `poetry check --lock`, and whitespace checks pass.
  The normal download-free suite skips the three opt-in real-model cases.
- Downloaded and ran the pinned quantized ONNX snapshot: 384-dimensional vectors;
  related synthetic printer text ranks above unrelated onboarding text. Public artifacts
  use about 65 MiB on disk. No API key/token or ticket-text transmission was needed.
- Real stdio analytics tests pass in both auto and legacy modes against a separate
  seeded uvicorn process: chronological matches, historical printer #29 retrieval,
  exact seed weekly counts, stable cache reuse, rejected invalid inputs, full redacted
  audit pairs, and every PSA record unchanged.
- All PSA/vector/audit verification paths were temporary. Only the approved public
  model artifact cache was retained under ignored `data/models`; no demo reset/reseed,
  classification changes, draft creation, agent, or UI work was performed.

## Runnable examples convention — approved 2026-10-12

- Store user-run Python integration checks and demos in root `examples/`, not README
  heredocs or pytest-only instructions. Apply the convention to future approved agent,
  routing, and evaluation workflows when those phases exist; do not implement them now.
- Keep only tool discovery and read-only MCP smoke scripts, with opt-in real analytics.
  Each client includes its small stdio launch configuration and forces read-only mode
  even if `.env` enables writes. No shared launcher or separate warm-up script: use
  `poetry run python -m dispatch.mcp_server.server` when explicitly starting the server.
- Discovery needs no backend. Actual calls require a separately running seeded FastAPI
  backend. Model inference/download is explicit via `--analytics`;
  ordinary discovery/read checks never download artifacts. No AI service/key is involved.
- Use existing configured audit/cache paths; explain those local side effects. Do not
  mutate/reset/reseed PSA data. Smoke checks distinguish expected validation rejections
  from unexpected failures and exit nonzero on failure for repeatable command-line use.
- Document which terminal holds FastAPI, automatic MCP lifecycle, expected output, and
  rerun commands instead of ambiguous instructions to “restart MCP.” Examples are manual
  checks, not application modules; per user preference, do not maintain dedicated pytest
  tests for them. Verify them manually with isolated backend/audit/vector data instead.
- Verification: all 294 application tests pass with real-model checks enabled; lint,
  formatting, lockfile, and whitespace checks pass. Manually ran the remaining examples:
  eight tools discovered without a backend, 3/3 basic checks and 7/7 analytics checks
  passed against a temporary seeded API, and a full SQL snapshot remained unchanged.

## Phase 5 — approved 2026-10-12

### Step 1: provider-neutral model calls

- Replace the original Claude requirement with explicit OpenAI-compatible Chat
  Completions configuration. Use the official OpenAI SDK 3.29.0, locked with Poetry;
  no LangChain, LangGraph, PydanticAI, hosted-agent API, native MCP or tool-calling
  requirement. Pydantic validates outputs; Python selects tools and ticket IDs.
- Require an explicit endpoint/model. Separate agent and offline judge configuration;
  no fallback to another provider/model. Require HTTPS/credentials remotely; allow
  keyless HTTP only on named loopback hosts, using a nonsecret SDK dummy credential.
- Text JSON is the portable default. JSON object/schema modes and token-parameter
  selection are explicit opt-ins. No temperature/reasoning-provider options by default.
  Bound total call time, input characters/output tokens, and parsed output size; reject
  duplicate JSON keys, nonfinite constants, wrong fields/enums and incomplete responses.
  No SDK retries, redirect following or proxy-env use. Errors redact provider bodies.
- Add triage and eval example entry points; no standalone connectivity checks or shared
  launcher/warm-up scripts or dedicated tests for the examples. Application logic is
  tested in main modules with deterministic model transports, not scored as real accuracy.

### Step 2: bounded workflow and persistence

- Classify source subject/description/company/SLA/timestamp only, excluding existing
  labels, source notes and gold labels. Draft after up to three historical resolutions;
  only strictly earlier, bounded notes are used, and truncated note collections omitted.
- Category/priority remain existing enums. Urgency is immediate/same_day/routine;
  security is a strict boolean. Define the policy explicitly in prompts and RUBRIC.md.
  Code derives urgent/security attention flags, not an additional model permission.
- Default preview forces MCP read-only mode and persists no PSA changes. Explicit
  apply requires trusted write-enabled config; the backend enforces its own mode too.
  Validate classification and draft before writes; retain assessment when drafting fails.
- Apply writes category/priority, an unsent draft, then a structured internal completion
  note with urgency/security/rationale/history/draft ID. No new DB tables are necessary.
  Status stays new; no approval, sending, closing, merging, remediation or paging.
- Inspect at most 100 new records, process 1–20 (default 5), stop on failures. Completion
  notes skip successful earlier runs; truncated source notes require manual inspection.
  This is not guaranteed idempotency/authentication. Concurrent agent runs unsupported.
- Re-read source before applying to detect observed changes, not as a transactional
  concurrency guarantee. Journal write intentions/confirmations privately with fsync
  and atomic replacement. Writes are not one transaction; uncertain/partial results
  stop further mutations. No blind retries. Existing report files are never overwritten.

### Step 3: offline-only DeepEval and separate scoring

- Add DeepEval 4.2.8 in an optional Poetry evals group, justified by user familiarity
  and the core drafting requirement. Never import it in live triage. Shared lock
  resolution selects compatible Click 8.3.3 and Hugging Face Hub 1.16.1 (down from the
  earlier lock); verify the existing API/MCP/real embedding behavior after this change.
- Forty developer-authored synthetic cases, independently labeled from evaluated models,
  with frozen historical context and reference replies; six separate judge calibration
  fixtures. Cover all categories/priorities/urgencies, casual urgent security, duplicates,
  prompt injection and varied impact. This is a small development set, not production
  accuracy or 40 independent samples. Review ground-truth policy judgments explicitly.
- Reuse live classification/drafting functions with frozen inputs. The eval runner has
  no PSA/MCP access or write path. It does not evaluate live retrieval ranking. Never
  send expected labels/reference drafts/response requirements to the generator.
- Deterministically score each field, all-class macro-F1, confusion matrices with missing
  predictions, exact match and urgent/security recall. Count failures in denominators;
  retain valid labels after draft failure. Report draft completion separately.
- Two GEval metrics: reference/source correctness plus safety, and internal assessment
  consistency. Wrong classification plus matching wrong draft can be consistent; never
  blend draft scores into classification accuracy. Explicit versioned steps and score
  bands, with serious safety/grounding failures assigned the lowest quality band.
- Use a schema-aware, asynchronous custom judge adapter and the non-logprob scoring
  path. Explicit judge endpoint/model only; disable telemetry/tracing/keyfile loading
  and cloud uploads, including cached settings. No Confident AI account is needed.
- Provisional 0.8 diagnostic threshold remains labeled uncalibrated. Run known-good/bad
  fixtures and inspect score reasons before interpreting pass rates; no automatic
  threshold tuning to a target score. Missing drafts fail completion automatically;
  judge errors remain unassessed with coverage shown. Human review remains mandatory.
- Optional no-judge and small-subset runs control cost; full current 40-case successful
  run uses 80 generator and 80 judge calls. Reports retain hashes/versions/model names,
  expected/generated pairs and explicit incomplete/interrupted states. Low quality is
  a finding; missing outputs/judge failures are execution failures. No online retry gate.

### Verification status

- All 373 application tests pass with optional eval dependencies and real embedding
  checks enabled, without warnings; Ruff, formatting, lockfile and whitespace checks
  pass. Without the optional eval group/default real-model flag: 367 passed, six
  intentional skips. Live agent imports do not load DeepEval, even when installed.
- Manually verified example commands using a temporary local Chat Completions protocol
  stand-in plus separate real MCP/FastAPI processes: connectivity, preview (full SQL
  snapshot unchanged), apply (one unsent draft, status still new), repeat-skip, two-case
  offline generation/judging, no-judge generation and six-fixture calibration. Reports
  were temporary and discarded; no demo database/reset/reseed was used. These protocol
  stand-in results are not LLM accuracy or judge calibration results.
- Real LLM endpoint/model have not been selected; no external LLM calls or real accuracy
  claim has been made. Real connectivity, triage, judge calibration and the 40-case
  accuracy report remain the explicit user-configured verification step.
- User explicitly requested leaving model/judge settings empty for now. `.env` was not
  edited; `.env.example` keeps both endpoint/model/key values blank. This was the Phase 5
  checkpoint; the user subsequently approved Phase 6 while real LLM verification remains
  pending. Do not imply that a real-model accuracy report exists.
- Removed the standalone model-connectivity example and its unused CLI helper at the
  user's request. Prefer one-ticket previews or one-case offline generation/calibration
  to verify actual workflow behavior. No local inference runtime/model was installed,
  started or selected while inspecting existing hardware/runtime availability.
- No Phase 6 endpoints, UI, Docker, auth, approval/sending or real client data added.


## Phase 6 — approved technician backend (2026-10-12)

- User approved this phase with local Ollama planned, while real LLM/eval results remain
  pending. Implement only backend endpoints; no Phase 7 UI, Docker, auth framework,
  real data, message sending, online judge or retry gate. `.env` remains untouched.
- Keep all existing ticket and MCP-facing draft creation shapes/tool permissions intact.
  Separate review DTOs expose revision/updated/approval fields through technician REST.
  Agent composition is shared by CLI and backend; its gateway still cannot approve/edit.
- Explicit SQLite version 6 via `PRAGMA user_version`; no Alembic dependency. Fresh
  schemas initialize normally. Existing unversioned schemas must upgrade explicitly,
  with all Dispatch processes stopped. Unknown/future shapes fail closed; no silent
  rebuild/reset/reseed. SQLite backup API writes a private unique sibling backup before
  transactional DDL, preserving WAL-committed data. Tests verify IDs/text/timestamps,
  foreign keys, approval constraints, repeated upgrade and rollback of corrupt data.
- Draft edits/approvals use an atomic conditional UPDATE on ticket ID, draft status and
  reviewed revision. Both transitions increment revision; stale/repeat requests get
  409. Approved drafts are immutable through the API, never sent. Successful mutation
  and metadata-only audit insertion share one SQLite transaction; audit failure rolls
  back the change. No claim of identity/authentication or protection from direct DB edits.
- Read assessment from the latest valid completed agent note linked to a draft belonging
  to the ticket, scanning at most 100 matching notes. Validate bounded fields and derive
  the attention flag again in code. Saved notes are not authenticated classification proof.
- Background jobs return 202 with persisted state and results. Default preview, explicit
  write-enabled apply, bounded batch, model config loaded only on job request. Reuse
  the same real MCP/LLM workflow as CLI, no extra orchestration framework. Journal each
  write intent/confirmation; partial/uncertain outcomes remain visible. Never replay or
  automatically retry writes. No API payload can select credentials/endpoints/files.
- One active job per database enforced by a unique nullable slot; one API worker/process
  is supported. Startup marks abandoned journals interrupted, shutdown cancels the owned
  task. CLI/another backend against the same DB concurrently remain unsupported. Job
  and audit metadata are exempt from ticket read-only mode, like the MCP audit journal.
- Audit endpoint pages backend SQLite and MCP JSONL separately with explicit source
  labels; no invented total merged ordering. Preserve original MCP journal and redact
  again on reading. Bounded MCP scans report malformed/truncated records and ignore a
  partial tail. Successful technician/job lifecycle events only; not a REST request log
  or tamper-proof audit. Reports reuse existing service/REST provider in-process, avoiding
  duplicate arithmetic and self-network port configuration for deterministic reads.
- Optional agent/judge REASONING_EFFORT env setting, blank by default, forwarded only
  when explicit. `none` is useful for supported Ollama thinking models; compatibility
  remains model/runtime-dependent. No automatic model selection/fallback/download.
- Add only `examples/console_api_smoke.py` for real read/review/background-job checks,
  with explicit mutation options, retained synthetic IDs and failure exit codes. Keep
  server startup as a module command and application tests in tests/, not example tests.


### Phase 6 verification

- All 422 application tests pass with optional eval dependencies and real embedding
  checks enabled (no real generative model calls). Ruff, formatting, Poetry lockfile
  and whitespace checks pass. Tests include simultaneous approval (one success/one
  conflict), audit rollback, active-slot constraints, failed reporting redaction,
  restart/shutdown recovery, unsupported schemas and unchanged MCP DTOs/permissions.
- Manually verified separate temporary FastAPI + real MCP processes with a local
  Chat Completions protocol stand-in: console reads 7/7, retained synthetic review
  14/14, background preview/apply 9/9 each, 8-tool discovery and MCP analytics 7/7.
  Preview preserved all PSA records; apply stored one status-draft reply and a valid
  assessment while the ticket remained new. Shared CLI preview also passed. Temporary
  databases/journals/run reports were discarded; no user database or `.env` was edited.
- Migration CLI verified on a temporary legacy DB: upgrade with backup, then repeat
  no-op. Tests snapshot all seven current SQLite tables at the MCP read-only boundary.
- Real Ollama triage, real judge calibration and 40-case model-quality/accuracy remain
  pending user configuration/testing. The protocol stand-in is not a quality result.
  No Phase 7 UI or Phase 8 packaging/architecture work was started.


## Examples/application CLI correction — approved 2026-10-13

- Supersedes the earlier convention where general-purpose runners/checkers lived in
  examples. Preserve all three filenames, but use small editable synthetic constants
  and direct public application calls; no CLI forwarding or broad argument parsers.
- `run_triage.py` shows classification and drafting, expected labels and intermediate
  assessment/history. Explicitly an LLM-stage demonstration, not live MCP retrieval,
  queue processing or persistence. Optional expected-label assertion is a regression
  aid, not an accuracy guarantee; labels never enter generation prompts.
- `run_evals.py` shows one labeled frozen case, expected/generated fields, deterministic
  classification scores, completion and separate optional judge scores/reasons/coverage.
  No blended quality/accuracy score, PSA access or report file. Real judge thresholds
  and model accuracy remain unmeasured; one-case macro-F1 includes absent classes.
- `console_api_smoke.py` is a focused read-only synthetic ticket/draft/assessment REST
  inspection with visible requests/results, not a multi-mode operational sweep.
- Move the existing operational read/review/background-job checker into the application
  as `python -m dispatch.mock_psa.checks`. Enable existing full-workflow and dataset CLIs
  as `python -m dispatch.agent.cli` and `python -m dispatch.evaluation.cli`. Document
  operational commands separately from examples; retain explicit write opt-ins,
  read-only preview, no retries, unsent approval and separate judge configuration.
- Test application entrypoints, not example scripts. No dependencies/frameworks, MCP
  contract/security changes, Phase 7/UI or Docker work. Preserve existing Phase 6 work;
  do not edit `.env` or the user's demo database. No automatic commit.


### Correction verification

- Default Poetry pytest run: **446 passed, 3 opt-in real-embedding tests skipped**,
  including 27 new application-entrypoint cases. No dedicated example-script tests.
  Ruff lint, formatting check, `poetry check --lock` and `git diff --check` pass.
- Manually ran the LLM-stage probe, one-case judged/unjudged probes, operational read
  checker, dataset subset and six-fixture calibration modules using a temporary local
  Chat Completions protocol stand-in. This verifies execution, not real model accuracy
  or judge calibration. No external LLM request or selected inference runtime/model.
- Focused REST example (only its URL constant changed in a temporary copy) and module
  read checks passed against a separate temporary seeded FastAPI process; snapshots of
  all seven SQLite tables remained unchanged. Temporary records/reports/processes were
  cleaned up. `.env`, the demo database and the dependency lockfile were not changed.


## Phase 7 — frontend planning requested 2026-10-13

- User requested Impeccable planning and selected the full console, delegated stack
  recommendation, and accepted the recommended code-first workflow. This opens UI
  planning; earlier Phase 6/correction exclusions still describe those checkpoints.
  It is not approval to implement the plan or start Docker/packaging work.
- Recommend semantic HTML/CSS/native JavaScript modules served same-origin from the
  existing FastAPI app at `/console/`. Keep Poetry and existing frameworks; no Node
  build pipeline, separate frontend server, CDN assets, or new production dependency.
  Record durable product context in `PRODUCT.md` and workflow in Impeccable config.
- Full scope: ticket queue/detail and saved attention, exact-revision unsent draft
  review, explicit preview/apply job monitoring, UTC weekly report, and separate audit
  sources. Workflow-first delivery; design direction awaits user choice. Detailed
  screen/API/state/verification brief: `docs/PHASE7_PLAN.md`.
- Identify proposed additive technician reads for server read-only capability, queue
  attention/draft summaries, and persisted run discovery. Existing endpoints alone
  do not provide these bulk/recovery contracts. Preserve MCP tools/DTOs and existing
  REST responses; no schema migration is proposed. These changes await brief approval.
- Preserve preview versus saved-state distinction: Apply regenerates, not a promotion
  of an earlier preview. Missing assessment means not assessed; approved means unsent.
  Unknown mutation outcomes require inspection, never automated replay. Keep secrets
  server-side and untrusted text literal. No browser eval dashboard or online judge.
- Planning does not read/change `.env` or the demo database, start inference, add UI
  source, install dependencies, or claim browser verification. Automated tests remain
  isolated; deliberate manual demonstrations will use the retained migrated demo.

### Real-model evidence received before UI planning

- Supersedes the earlier pending-real-model statements, not their recorded historical
  verification: user ran real Ollama `qwen3.5:4b` backend preview (9/9 operational
  checks) and the full 40-case frozen-context no-judge evaluation with an explicit
  `OPENAI_OUTPUT_MODE=json_schema` command override.
- Report `data/evals-44c350b8-4e92-4859-8131-20ede829f2ef.json`: 40 valid assessments
  and 40 drafts, 17/40 exact classifications; category 82.5%, priority 60%, urgency
  87.5%, security flag 97.5%, urgent recall 5/9. Execution works; these findings do
  not justify autonomous approval or safety claims. Draft grounding needs review.
- Real judge calibration and real-model Apply remain unverified. Offline reports do
  not update PSA records. Model settings are not changed by this planning task.


## Phase 7 — technician console implemented (2026-10-13)

- User selected Service case-file and approved code changes for the full console.
  Implement HTML/CSS/native ES modules in `web/`, served same-origin at `/console/`
  by the existing one-worker FastAPI process. Poetry remains the toolchain; no new
  production framework, frontend server, CDN, authentication, sending, or Docker.
- Ticket queue supports legacy filters plus saved-attention states and draft-review
  summaries. Detail shows source/company/contact/notes beside assessment and response.
  Missing assessment remains unknown. Ticket labels remain separate from the model's
  saved labels; manual label/urgency/security overrides are not added to the UI.
- Explicit edits and unsent approval use existing revision-checked REST writes. Keep
  local text on conflicts, reconcile unknown outcomes through reads, and prevent
  pending draft-page reads from erasing late editor input or racing another page.
- Triage view defaults to Preview, confirms Apply scope, shows per-ticket results and
  write journals, and recovers through persisted history. Apply regenerates rather
  than promoting preview text; no cancellation or automatic mutation retry is added.
  Weekly reports preserve UTC/window/backlog distinctions; audit sources stay separate.
- Add three read-only contracts: `/console/config`, `/console/tickets`, and
  `GET /triage/runs`. Preserve all existing REST/MCP shapes and eight tool permissions.
  Queue attention filters apply before pagination; reject base scans beyond 2,000
  tickets. Share latest-valid-of-100 marker validation between detail and queue,
  including byte-bounded UTF-8 reads that preserve NUL/trailing-data rejection.
- Serve only console assets with a restrictive CSP and safe literal DOM rendering.
  No ticket/draft text or credentials in browser storage; no browser model/PSA endpoint
  configuration. Refuse unsafe JavaScript integer IDs/revisions rather than rounding.
  Wheel assets serve independently of cwd, without exposing repository/data files.
- Add locked development-only Playwright and opt-in Chromium interaction tests using
  a real isolated FastAPI process and deterministic replacement generator. These are
  not real-model accuracy, judge calibration, or Ollama Apply verification.
- User explicitly requested stopping the additional Impeccable workflow. Retain the
  approved interface and tests; no further design-documentation or artifact gates.
  `.env` and the retained demo database remain untouched. No automatic commit.

### Phase 7 verification

- Full suite with browser interaction checks enabled: **485 passed, 4 intentional
  skips** (three opt-in real embeddings, one separately requested screenshot capture).
  Browser suite with capture enabled: **16 passed**. No generative model calls were
  used; all browser mutations targeted isolated synthetic databases.
- Ruff lint/formatting, JavaScript syntax, frontend formatting, Poetry lock checks,
  and whitespace checks pass. Poetry wheel builds with all ten UI assets; isolated
  extracted-wheel serving outside the repository cwd was verified.
- Desktop/phone checks cover all console areas, conflict and unknown-write recovery,
  unsaved navigation, lost job-start response, polling disconnect/resume, read-only
  mode, literal injection/HTML text, UTC reports, and delayed draft-page races.
- No `.env` or demo-database edits, reset/reseed, Docker work, or automatic commit.


## Documentation conventions

- README is the product entry point: purpose, screenshot, capabilities, minimal setup,
  the live queue command, and the live/offline distinction. No phase plans or development diary.
- Task-specific guides in docs/ hold model/triage, console/checks, offline evaluation,
  database/REST, MCP/analytics, and contributor/test/example details.
- Engineering history remains here; retained planning artifacts are not the product quickstart.
- Commands use installed application modules, one-worker job-safe launches, explicit
  write opt-ins, and instructions that preserve the existing synthetic demo database.
