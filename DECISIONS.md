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
  edited; `.env.example` keeps both endpoint/model/key values blank. Do not advance to
  Phase 6 or imply that a real-model accuracy report exists.
- Removed the standalone model-connectivity example and its unused CLI helper at the
  user's request. Prefer one-ticket previews or one-case offline generation/calibration
  to verify actual workflow behavior. No local inference runtime/model was installed,
  started or selected while inspecting existing hardware/runtime availability.
- No Phase 6 endpoints, UI, Docker, auth, approval/sending or real client data added.
