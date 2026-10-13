# Development and verification

[Back to Dispatch](../README.md)

Contributor setup, repository layout, isolated application/browser tests, and small editable
scenario probes. Architecture trade-offs and development history belong in
[DECISIONS.md](../DECISIONS.md), not the product quickstart.

## Development setup

Requires Python 3.12 and Poetry 2.x. Run from the repository root:

```bash
poetry env use python3.12
poetry install
test -f .env || cp .env.example .env
poetry run pytest
poetry run ruff check .
poetry run ruff format --check .
```

Poetry uses the ignored project-local `.venv/`. Dependencies are pinned in `poetry.lock`.
Environment variables override `.env`; the default SQLite database is `data/dispatch.db`. Tests use
isolated temporary databases.

## Layout

```text
src/dispatch/mock_psa/   # Mock PSA and technician API
src/dispatch/domain.py   # Shared provider-neutral enums
src/dispatch/mcp_server/ # Official SDK v2 server, providers/, tools/
src/dispatch/agent/      # Bounded LLM triage workflow and CLI composition
src/dispatch/evaluation/ # Offline scoring, optional DeepEval adapter and reports
evals/                  # Synthetic gold cases, rubric and judge calibration fixtures
tests/                  # Isolated API/model/seed tests
examples/               # Small editable synthetic probes and MCP client demos
web/                    # Same-origin technician console and native ES modules
```

See [DECISIONS.md](../DECISIONS.md) for scope and engineering trade-offs.

## Application verification

```bash
poetry run pytest tests/test_agent_client.py tests/test_agent_workflow.py tests/test_evaluation.py
poetry run pytest tests/test_cli_entrypoints.py
# Optional DeepEval integration checks, deterministic responses (no real LLM calls):
poetry run pytest tests/test_evaluation_judge.py
poetry run pytest
poetry run ruff check .
poetry run ruff format --check .
poetry check --lock
```

Tests never use model credentials, contact LLM endpoints, or mutate your demo database. Three
real-embedding tests remain opt-in as described in [the MCP guide](MCP.md#verify-analytics).
DeepEval integration tests skip when the optional group is not installed. No dedicated
example-script tests are maintained; application entrypoints are tested and examples manually
verified with isolated synthetic inputs. Endpoint contracts are in [the API guide](API.md).

Reference documentation: [Official MCP SDK](https://github.com/modelcontextprotocol/python-sdk),
[v2 migration guide](https://py.sdk.modelcontextprotocol.io/migration/),
[OpenAI SDK configuration](https://developers.openai.com/api/reference/python),
[DeepEval GEval](https://deepeval.com/docs/metrics-llm-evals),
[custom judges](https://deepeval.com/guides/guides-using-custom-llms),
[Poetry setup](https://python-poetry.org/docs/basic-usage/),
[FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/), and
[SQLite foreign-key support](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#foreign-key-support).

## Browser verification

Playwright is development-only and locked through Poetry. Chromium installation is needed only for
the opt-in browser suite, not for running the console:

```bash
poetry run python -m playwright install chromium
DISPATCH_RUN_BROWSER_TESTS=1 poetry run pytest tests/test_console_browser.py
poetry run pytest
poetry run ruff check .
poetry run ruff format --check .
poetry check --lock
```

Browser tests use an isolated seeded SQLite database, a real local FastAPI process, and a
deterministic replacement generator—never your retained demo, Ollama, or real client data. They
verify application/interaction behavior, not model quality, real judge calibration, or actual-model
Apply. Screenshots can be requested separately with
`DISPATCH_BROWSER_CAPTURE_PATH=.impeccable/review`; they are local review artifacts. Assets are
included in Poetry wheels and served independently of the launch directory.

## Editable scenario probes (not application commands)

Keep edits synthetic. The existing filenames are deliberately stable; these scripts have no
general-purpose argument parser. Edit the constants at the top and run from the repository root
after `poetry install`:

```bash
poetry run python examples/run_triage.py
poetry run python examples/run_evals.py
poetry run python examples/console_api_smoke.py
```

- **`run_triage.py` — LLM stages only:** `PROBLEM`, `EXPECTED`, and frozen `HISTORY` are visible
  inputs. Calls public `classify` and `draft_response` functions directly, printing the
  expected/generated classification, rationale, derived attention flag, draft input history, and
  unsent draft. Two real generator calls require the model settings in
  [the triage guide](TRIAGE.md#install-and-configure-your-chosen-model) and may cost money.
  `ASSERT_EXPECTED` optionally checks regression. No API/MCP, live retrieval, queue processing,
  report files or PSA writes. **This is not the full MCP workflow**; use the
  [application triage command](TRIAGE.md#run-the-existing-demo-queue) for that.
- **`run_evals.py` — one editable labeled case:** edit `CASE` and `JUDGE_ENABLED`. Calls public
  evaluation/scoring functions with frozen synthetic input, then prints expected/generated fields,
  classification scores, draft completion, and separate judge scores/reasons/coverage. Expected
  labels and reference draft never enter the generator. Default: two generator + two judge calls;
  configure the separate judge and install `poetry install --with evals`. Set `JUDGE_ENABLED=False`
  to skip judging. No API/MCP/PSA access or files. Low scores are findings; missing outputs or judge
  failures exit nonzero. One case is not an accuracy estimate; macro-F1 includes all declared
  classes, including absent classes. Judge thresholds remain uncalibrated.
- **`console_api_smoke.py` — focused REST inspection:** start the seeded API first, then edit
  `BASE_URL`, `SYNTHETIC_TICKET_ID` (default seed #31), and `DRAFT_PAGE`. Prints explicit GET
  requests, status codes and JSON for the ticket, saved drafts, and saved assessment. Before applied
  triage, drafts may be empty and assessment 404 is expected. Reads only; no jobs, mutation, LLM/MCP
  or report files.

Real model outputs depend on your selected model and require human review. These probes are manually
checked, not maintained as dedicated unit-test targets. Reusable commands have
application-entrypoint tests and configurable CLI options instead:

```bash
poetry run python -m dispatch.agent.cli --help
poetry run python -m dispatch.evaluation.cli --help
poetry run python -m dispatch.mock_psa.checks --help
```
