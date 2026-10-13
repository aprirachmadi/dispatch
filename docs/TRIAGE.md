# Live triage: model → MCP → saved drafts

[Back to Dispatch](../README.md)

Run the actual context-acquisition and generation workflow against the mock PSA. **Preview does not
save ticket assessments. Apply does. Neither sends a reply.**

## Run the existing demo queue

Use the retained synthetic database; do not reset or reseed it. Configure a generator as described
below, keep Ollama/your chosen model service running, and stop any older Dispatch API before
launching this one.

**Terminal 1 — start the write-enabled API:**

```bash
OPENAI_OUTPUT_MODE=json_schema DISPATCH_READ_ONLY=false \
poetry run uvicorn dispatch.mock_psa.main:app \
  --host 127.0.0.1 --port 8000 --workers 1
```

**Terminal 2 — process up to 20 eligible tickets:**

```bash
OPENAI_OUTPUT_MODE=json_schema DISPATCH_READ_ONLY=false \
poetry run python -m dispatch.agent.cli \
  --apply --limit 20 --base-url http://127.0.0.1:8000
```

These schema-mode overrides suit the tested local Ollama setup; use your provider's supported output
mode. The command persists assessments and unsent drafts for **untriaged new tickets**, not every
historical/in-progress/resolved/closed scenario. The fresh seed contains 24 new tickets; your
retained database may differ.

To process the entire eligible queue in one command, use `--all` instead of `--limit`:

```bash
OPENAI_OUTPUT_MODE=json_schema DISPATCH_READ_ONLY=false \
poetry run python -m dispatch.agent.cli \
  --apply --all --base-url http://127.0.0.1:8000
```

`--all` snapshots IDs across the paginated new-ticket queue before generation, then processes
each ticket sequentially in one model/MCP session, with no per-run ticket limit. It supports Preview too: omit `--apply`.
Completed markers are skipped, so saved drafts are not regenerated; Preview visits each candidate
once even though it saves no completion markers. Tickets arriving after the snapshot wait for a
later run. Do not combine `--all` with `--limit` or `--ticket-id`.

On failed/partial/uncertain results, the whole command stops and exits nonzero. Inspect the report
and saved records instead of blindly rerunning. All results and write checkpoints share one local
JSON report. The underlying provider caps offsets at 10,000: if the queue fills the final 100-record
page, `--all` fails before generation or ticket writes rather than claiming an exhaustive run.
Ordinary `--limit` batches scan only the first 100 new records. Do not run a UI background triage job
or another CLI agent concurrently.

Refresh the console to see saved attention and drafts. CLI runs write local JSON reports, not the
UI's persisted background-run history; use **Triage runs** in the console if you want server-managed
job history. Approval and sending are not part of this command. For all 40 frozen labeled cases
instead, use [offline evaluation](EVALUATION.md).

## Workflow and model boundary

The agent is plain Python with Pydantic validation and the official OpenAI SDK (Poetry-locked
3.29.0), not LangChain/LangGraph/PydanticAI. Python controls the workflow and MCP tools; the model
produces classifications and drafts, never arbitrary tool calls. DeepEval (locked 4.2.8) is isolated
in the optional `evals` dependency group and is **never imported or called by live triage**. It adds
evaluation dependencies, not an agent framework. Current package contracts are tested before
lockfile updates.

## Install and configure your chosen model

From the repository root:

```bash
poetry install
# Only needed for offline LLM-as-a-judge evaluation:
poetry install --with evals
```

Edit your existing `.env`; do not overwrite it with `.env.example` if it has settings you want to
keep. Add the generator variables from `.env.example`. Minimum agent settings:

```dotenv
OPENAI_BASE_URL=<your-provider-base-url-including-v1-if-required>
OPENAI_MODEL=<your-provider-model-name>
OPENAI_API_KEY=<your-key-if-required>
```

The angle-bracket values are placeholders, not working credentials/configuration. Nothing defaults
to a hosted provider or model. Remote endpoints require HTTPS and an explicit key; HTTP/keyless use
is restricted to `localhost`, `127.0.0.1` or `::1`. For a keyless loopback endpoint, leave API_KEY
empty (the SDK gets a dummy local-only credential). The selected endpoint must implement
`/chat/completions` with system/user messages and text content; this is not a universal guarantee
for every "compatible" model. If local, start your chosen inference server separately; Dispatch does
not host it.

Default `OPENAI_OUTPUT_MODE=text` asks for JSON and validates it locally, without requiring provider
JSON mode or tool calling. Opt into `json_object` or `json_schema` only when supported. Default
token field is `max_tokens`; use `max_completion_tokens` if required by your model. No temperature
option is sent; reasoning effort is forwarded only when explicitly configured. Refusals, incomplete
responses, wrong schemas and unknown fields fail visibly. Defaults: 120-second total model-call
timeout, 2048 output tokens; configurable within validated bounds. No hidden SDK retries, redirect
following, proxy-env use, provider fallback or online judging.

Use the one-ticket triage preview below or a one-case offline generation command
(`poetry run python -m dispatch.evaluation.cli --limit 1 --no-judge`) to verify actual model
behavior, rather than a separate API-connectivity script. These make **real model requests**, so
hosted providers may charge. Configuration/request failures exit nonzero without printing raw
provider errors or API keys. Never put keys into Git, `.env.example`, generated prompts, or chat
messages.

## Run triage in preview mode

**Terminal 1:** start the already-seeded FastAPI backend and leave it running. If yours uses another
port, use that same address in Terminal 2's `--base-url`.

```bash
poetry run uvicorn dispatch.mock_psa.main:app --workers 1 --host 127.0.0.1 --port 8000
```

**Terminal 2:** start with a single known seed trap to control cost:

```bash
poetry run python -m dispatch.agent.cli --base-url http://127.0.0.1:8000 --ticket-id 31
# Or a small queue batch (default five; hard maximum twenty):
poetry run python -m dispatch.agent.cli --base-url http://127.0.0.1:8000 --limit 3
```

The triage command launches/stops its own MCP child process. **Do not start MCP separately.** It
classifies, finds at most three strictly prior resolutions, reads bounded earlier notes, and
generates a draft. First similarity use may download the pinned public model. The classifier never
sees existing category/priority, source notes or eval labels. Drafting receives bounded historical
excerpts (description 2000 chars, at most three notes of 1500 chars each); later notes are excluded
and truncated note collections are omitted. Historical fixes are suggestions, not proof this ticket
was fixed.

Expected terminal output: `Mode: preview`, then category/priority/urgency/security flag and
rationale, an `ATTENTION` line for urgent/security assessments, and `DRAFT (unsent)`. Actual
predictions depend on the chosen model; these are outputs to inspect, not a promised accuracy
result. Preview forces MCP read-only mode even if `.env` allows writes. Audit and local model/vector
caches can still be written. No ticket/draft/note is changed.

Each run writes a private JSON progress report to a fresh `data/triage-<uuid>.json`. Use
`--output <new-path>` to choose another path; existing files are never overwritten. Failures exit
nonzero and stop the batch. A valid classification is retained if later retrieval/drafting fails; a
missing/invalid draft is not called workflow success.

## Explicitly save drafts and classifications

To apply, stop FastAPI in Terminal 1 with Ctrl+C and restart it in write mode:

```bash
DISPATCH_READ_ONLY=false poetry run uvicorn dispatch.mock_psa.main:app --workers 1 --host 127.0.0.1 --port 8000
```

Then in Terminal 2:

```bash
DISPATCH_READ_ONLY=false poetry run python -m dispatch.agent.cli --base-url http://127.0.0.1:8000 --ticket-id 31 --apply
```

`--apply` alone does not enable writes: trusted configuration must allow them too. The agent
validates the assessment AND draft before any mutation. It re-reads the source to detect observed
concurrent edits, then updates category/priority, saves a draft, and appends a completion note
containing urgency/security assessment, attention flag, history IDs and draft ID. Draft status is
always `draft`; no reply is sent or approved, and ticket status remains `new`.

Mutation intents and confirmations are checkpointed to the local run report. These three writes are
**not one transaction**. A failed/unconfirmed write stops the remaining writes and the batch; it may
already have committed. Inspect report, audit and persisted records before retrying. Never assume
rollback or blindly rerun a partial run. The completion-note marker skips successfully triaged
tickets on later runs; it is a demo convenience, **not** transactional idempotency or
authentication. Truncated source notes require manual review. Concurrent agent runs are unsupported,
and edits between the final read and writes remain possible. Ordinary batch scanning is capped at the first
100 new records; `--all` traverses pages within the provider offset limit described above.

After testing, stop the write-mode API and restart it with `DISPATCH_READ_ONLY=true`. An attention
flag is technician-visible data, not automated paging, remediation or an SLA timer. Human review
remains mandatory, including for dangerous-but-valid drafts.
