# Dispatch product context

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

User delegated the Phase 7 frontend stack recommendation. Approved: semantic
HTML, CSS, and native JavaScript ES modules served by the existing FastAPI app.
Keep Python 3.12, Poetry, SQLite, and the existing REST/MCP/application boundaries.
No new frontend framework or Node build pipeline is planned. The full console uses the approved Service case-file direction and code-first workflow.

## Users

The primary user is an IT technician reviewing a synthetic helpdesk queue,
checking machine-generated assessments, and preparing a response for human approval.

## Product Purpose

Dispatch is a local, synthetic NorthPoint IT helpdesk portfolio demo. It demonstrates
bounded LLM triage with MCP context acquisition, historical similarity, draft-only
persistence, technician review, and separate offline evaluations.

Success means the technician understands the source evidence and persisted state,
can inspect and correct a draft, and can approve exactly the reviewed revision
without sending a message or mistaking a preview for saved work.

## Operating Context

- Existing backend Phases 1–6 expose ticket/company/contact reads, draft review,
  saved assessments, background triage jobs, weekly reports, and separate audit sources.
- Phase 7 covers the full console, delivered with the ticket/draft workflow first.
  HTML/CSS/ES modules in `web/` are served at `/console/`.
- Use the user's existing migrated synthetic demo database for deliberate manual
  demonstrations; do not reset/reseed it. Automated tests use isolated databases.
- One loopback API process/worker and one active triage job are supported. Concurrent
  CLI triage against the same database is unsupported.
- User selected code-first and the Service case-file direction and approved code
  changes on 2026-10-13. Docker/packaging work remains excluded.

## Capabilities and Constraints

- Preview performs real generation/context acquisition but does not change ticket,
  draft, or note records. Job and audit metadata may still be persisted.
- Explicit Apply regenerates through the workflow and persists category/priority,
  an unsent draft, and a completion note; it does not promote a previous preview.
- Saved urgency/security and derived attention are projected from valid completion
  notes. Missing assessment is unknown, not a negative security classification.
- Draft edits and approvals require the reviewed revision. Approved drafts are
  immutable through the API. Approval never sends a reply.
- Writes require trusted server configuration; UI controls do not grant permission.
- Partial/uncertain writes are possible. Do not automatically replay mutations.
- No sending, automatic closing/merging/remediation/paging, authentication, Docker,
  production deployment, or real client data is in this phase.
- Agent/judge configuration and secrets stay server-side. Offline evaluation does
  not update the PSA, and a judge does not automatically improve/train the model.
- Reports use UTC Monday-based windows and a separate current backlog snapshot;
  modeled SLA tiers are not calculated response deadlines.
- Ticket, note, and generated text are untrusted content, never executable markup.

## Evidence on Hand

- `README.md` and `DECISIONS.md`: application behavior and safety contracts.
- `src/dispatch/mock_psa/console_routes.py` and `console_schemas.py`: technician API.
- `src/dispatch/agent/models.py`: assessment, attention, and per-ticket result states.
- `src/dispatch/mock_psa/seed.py`: six fictional companies, 18 contacts, 60 initial
  tickets, including credential disclosure, duplicate printer reports, and injection.
  The user's retained demo may contain additional synthetic records.
- User verified real Ollama E2E preview and a 40-case offline no-judge run. Pipeline
  execution succeeded, but classification/grounding quality requires human review.
  No calibrated real judge or actual-model Apply verification is claimed here.
- Service case-file is the user-selected visual direction. No customer testimonials,
  commercial claims, real client content, or authenticated actor identities exist.

## Product Principles

1. Show source facts, model suggestions, and saved state as distinct information.
2. Human review is essential; generation completion is not a quality guarantee.
3. Make write scope and uncertainty visible before and after every mutation.
4. Preserve synthetic demo work; never silently reset, reseed, retry, or send.
5. Build a focused operational tool, not a marketing dashboard or autonomous chatbot.
