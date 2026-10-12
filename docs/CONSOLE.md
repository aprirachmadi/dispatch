# Technician console and operational checks

[Back to Dispatch](../README.md)

Use ticket review, background triage, weekly reports, and source-separated audit views. If startup
reports an old schema, follow [the explicit migration guide](API.md#upgrading-an-existing-database);
do not delete the demo database.

## Launch and navigation

Use your **existing migrated synthetic demo database**. Do not reseed/reset or copy `.env.example`
over your configured `.env`. Stop an older API before launching this version. One API worker,
without reload, is required for background jobs.

```bash
poetry install
# Safe first inspection; this process override does not edit .env:
DISPATCH_READ_ONLY=true poetry run uvicorn dispatch.mock_psa.main:app \
  --host 127.0.0.1 --port 8000 --workers 1
```

Open **<http://127.0.0.1:8000/console/>**. All four areas use the actual REST API:

- **Tickets:** company/status/category/priority/text filters, saved attention, original
  source/company/contact/notes, saved assessment and historical ticket links, and paged draft
  review. No assessment means **Not assessed**, not “safe.” The queue defaults to new tickets; All
  statuses includes retained historical and additional demo work.
- **Triage runs:** default Preview, explicit single-ticket or new-queue scope, persisted run
  history, real model/MCP workflow, and monitoring/recovery. Leaving the page or pausing monitoring
  does not cancel the backend job. Inspect individual results, including skips/partial/uncertain
  writes, rather than treating “completed” as quality.
- **Weekly report:** Monday-based UTC window and optional company filter, opened and resolved
  counts, category/priority breakdowns, and a separately timestamped current backlog. A shortcut
  opens the fixed synthetic seed week; there are no invented SLAs, trends, attention totals, or
  response-time calculations.
- **Audit:** backend and MCP metadata in independent source pages with scan warnings. No fabricated
  technician identity, merged order, or tamper-proof guarantee.

For deliberate **Apply, draft editing, and approval**, stop the read-only API and restart explicitly
in write mode, preserving the same database/model configuration:

```bash
DISPATCH_READ_ONLY=false poetry run uvicorn dispatch.mock_psa.main:app \
  --host 127.0.0.1 --port 8000 --workers 1
```

The console reads write capability from the server; it cannot enable writes itself. Agent
configuration stays in trusted `.env`/environment. If your configured model requires structured
schema output (as the tested Ollama evaluation did), prefix that server launch with
`OPENAI_OUTPUT_MODE=json_schema`; changing output mode for an offline evaluation command does not
change a running API. Do not run CLI triage or another backend concurrently against the same
database.

## Review and persistence semantics

1. Preview shows a transient generated assessment/draft in the run view. It does not update
   ticket/draft/note records; job/audit metadata is still persisted.
2. Apply **regenerates**, then writes through the existing guarded MCP workflow. It does not promote
   or promise to save the exact text from an earlier preview.
3. Open the resulting ticket and inspect the saved assessment and linked unsent draft. Save draft
   edits explicitly; approval is disabled while there are unsaved edits.
4. Approve exactly the saved reviewed revision. An approved draft becomes read-only through the API
   and remains **unsent**. There is no send or ticket-close control.

On a revision conflict, local editor text is kept beside the latest saved revision. Choose the saved
text or explicitly rebase/review your local text before another Save; no automatic
overwrite/merge/approval occurs. On a write timeout, the outcome is unknown: read saved state before
a deliberate next action. A lost start response is reconciled using persisted run history, never an
automatic second POST.

Ticket category/priority and saved model labels can differ after an external manual label edit. The
UI shows source ticket fields separately from the saved assessment; the console provides no manual
urgency/security override or model retraining. Human review is still essential. The offline
evaluation remains a separate pipeline and never populates the technician queue or dashboard by
itself.

## Operational checks

**Terminal 1:** after migration, start one backend and leave it running. Stop any older API first.
Use **one worker, without `--workers 1`**, while testing background jobs:

```bash
poetry run uvicorn dispatch.mock_psa.main:app --host 127.0.0.1 --port 8000 --workers 1
```

**Terminal 2:** reads only, no AI model required:

```bash
poetry run python -m dispatch.mock_psa.checks --base-url http://127.0.0.1:8000
```

Expect **7/7 checks passed**. `--ticket-id` chooses an existing ticket (default 31). An absent saved
assessment is normal before applying triage, not a failed check.

For explicit review testing, stop Terminal 1 and restart in write mode:

```bash
DISPATCH_READ_ONLY=false poetry run uvicorn dispatch.mock_psa.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Then in Terminal 2:

```bash
poetry run python -m dispatch.mock_psa.checks --base-url http://127.0.0.1:8000 --review
```

Expect **14/14**. This deliberately retains one new synthetic ticket and approved unsent draft; IDs
are printed. It checks edit, stale approval rejection, approval and repeat rejection. Do not run
`--review` against a read-only backend: intake could create the synthetic ticket before draft
creation is blocked. Use a separate demo DB if preferred.

## Server-managed generation

Configure the agent in your local `.env` first; no settings/keys come from REST payloads. For
Ollama, assuming it is running and the model is installed, example settings are:

```dotenv
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
OPENAI_MODEL=qwen3.5:4b
OPENAI_API_KEY=
OPENAI_REASONING_EFFORT=none
```

Use the **exact model tag from `ollama list`**, not a guessed alias. Optional reasoning control is
unset by default and forwarded only when explicitly configured; `none` requests no thinking for
supported Ollama models. Compatibility depends on installed Ollama/model versions; there is no
automatic provider-specific fallback or retry. See
[Ollama compatibility](https://docs.ollama.com/api/openai-compatibility) and
[official OpenAI Chat Completions parameters](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create).
Judge settings stay separate and are used only by offline evals. **Restart FastAPI after
configuration changes.** Ensure `DISPATCH_PSA_BASE_URL` matches its actual port; the background
agent launches MCP itself, and MCP still calls FastAPI over REST.

With the API running, Terminal 2:

```bash
# Real model + MCP + retrieval, but no ticket/draft/note writes:
poetry run python -m dispatch.mock_psa.checks --base-url http://127.0.0.1:8000 --triage preview --ticket-id 31
# Explicitly persist classification + an unsent draft + completion note (write-mode API):
poetry run python -m dispatch.mock_psa.checks --base-url http://127.0.0.1:8000 --triage apply --ticket-id 31
```

Expect **9/9** with assessment AND generated draft. A previously completed ticket is skipped; the
checker reports that as not verifying generation. Choose another untriaged new ticket or create a
fresh synthetic intake ticket—do not reset/reseed your database. The first similarity use may
download public embedding artifacts. No separate MCP terminal is needed. Stop FastAPI with Ctrl+C
when finished.

POST returns 202 + `run_id`; poll until `completed`, `incomplete`, `failed`, or `interrupted`.
`completed` can include skipped tickets; always inspect per-ticket results. `incomplete` includes a
failed/partial workflow. Missing model configuration returns 503; write-mode denial returns 403; a
second active job returns 409. Input is limited to mode, optional positive ticket ID and limit 1–20
(default 5); no API keys, model endpoints or file paths are accepted. Each mutation
intent/confirmation is persisted before/after the workflow call; uncertain writes may already have
committed. Do not blindly resubmit after timeouts/failures. There is no cancellation endpoint:
Ctrl+C in the polling client does **not** cancel the backend's job.

This intentionally small runner supports **one API process/worker and one active job**. A SQLite
unique slot also prevents duplicate active claims. Do not run CLI triage or a second backend
concurrently against the same database. Shutdown cancels the owned job; startup marks
crash-abandoned jobs interrupted without replay. Preview leaves PSA records untouched, but job/audit
metadata is intentionally persisted even in read-only mode. No Celery/Redis, authentication, online
judge/retry gate, sending, or Docker is provided. The technician-only route separation is a code
boundary, **not identity authorization**; any local REST client can invoke it. Keep this demo bound
to loopback.

```bash
poetry run pytest tests/test_migrations.py tests/test_console_api.py
poetry run pytest
poetry run ruff check .
poetry run ruff format --check .
poetry check --lock
```
