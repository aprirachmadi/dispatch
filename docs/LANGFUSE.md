# Langfuse tracing and experiments

Dispatch reads `LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY`
from the application environment, loading `.env` without overriding exported values.
Use project API keys from Langfuse Settings, not dashboard login credentials.
All three unset disables integration. Partial configuration fails with a safe error.
Restart the application after changing configuration. Never commit `.env`.

The host configuration is `LANGFUSE_BASE_URL=http://localhost:3210`.
This installation was checked against Langfuse server **4.58.0**, Python SDK **4.17.0**,
OpenAI **3.29.0**, and DeepEval **4.2.8**. Langfuse is a runtime dependency because
both live triage and optional offline experiments need its exporter and SDK; DeepEval
remains in the optional `evals` dependency group. No OpenAI or DeepEval upgrades were needed.

## Traces

Observability → Traces contains one trace per triaged ticket and one per experiment item.
Each actual model request has one manual generation observation. Classification, drafting,
and DeepEval judge calls share their current request/item context. Standalone model calls
produce standalone traces. Inputs contain the actual final system/user messages; outputs
contain the provider response. Provider model, usage, start/end times and safe error type
are recorded where available. JSON validation failures are errors too.

There is no automatic OpenAI instrumentation, so requests are not exported twice.
DeepEval's existing Confident AI tracing/telemetry opt-outs remain enabled.
Exporter failures do not change generation results. CLI/workflow completion flushes telemetry.
Prompts and responses are intentionally stored in your configured Langfuse project;
application API keys are not part of exported metadata or errors.

## Datasets and Experiments

```bash
poetry install --with evals
poetry run python -m dispatch.evaluation.cli --import-langfuse
poetry run python -m dispatch.evaluation.cli --calibrate-judge --import-langfuse
poetry run python -m dispatch.evaluation.cli --limit 1
```

Datasets → `dispatch/triage` contains the 40 golden cases.
`dispatch/judge-calibration` contains the six calibration fixtures.
`--langfuse-dataset NAME` selects another dataset. Stable IDs derived from dataset name
and case ID make repeated imports upserts, including updates to an existing case.
Import preserves source problems, frozen historical context, expected classifications,
reference drafts, response requirements, tags and dataset hash. Calibration imports also
preserve fixture assessments/drafts and expected pass decisions. Import does not delete
older items removed from the source; experiments select only current local cases.

Configured evaluation commands import the full dataset, then run the requested subset as
an Experiment. Find its UUID from the local `data/evals-<UUID>.json` report in Experiments
or the dataset's runs. The report also includes the experiment ID and dashboard URL.
`--no-judge` still creates an experiment with deterministic scores only.
`--validate-only` performs local validation without Langfuse or model requests.

Each item has classification field/exact-match and draft-completion scores. Existing
DeepEval GEval scores and reasons are reused as Langfuse Evaluations; unassessed metrics
get a coverage indicator and no fabricated quality score. Aggregate experiment scores
reuse the existing classification accuracies, macro F1, critical recalls, completion,
judge coverage, assessed means and provisional pass rates. Local JSON reports, rubric,
0.8 provisional threshold, generation prompts and exit semantics are preserved.
Execution remains sequential. Each experiment task owns its model clients in the SDK's
async loop, avoiding cross-loop HTTP connection reuse.

## Docker connectivity

There is currently no Dispatch Dockerfile or Compose deployment in this repository.
The existing Langfuse stack publishes port 3210 on host loopback only. A container's
`localhost:3210` is therefore unreachable (verified from the Langfuse worker container).
From the same Docker network, `http://langfuse-web:3000/api/public/health` returns 200.
For a containerized Dispatch, join that network and set
`LANGFUSE_BASE_URL=http://langfuse-web:3000`; inject the same project API keys at runtime.
On Linux, host networking also allows the host URL. A bridge container using
`host.docker.internal` needs a host-reachable bind; the current loopback-only publication
is insufficient. No Docker stack/network settings were modified.

## Sessions and verification

Sessions are unconfigured: no session IDs are set. Dispatch processes individual tickets;
its historical resolved-ticket examples are frozen context, not multi-turn conversations.
The workbench supports draft revisions and human approval, but does not continue an LLM
chat or maintain conversation memory.

For this v4 server, verify persistence through Observations API v2, Experiments API and
Scores API v3. Legacy trace/dataset-run reads are removed in events-only mode. Omit
`parseIoAsJson=true`; I/O is returned as strings. The observations field selection should
include `basic` (names/errors), `io`, `model`, `usage`, `metrics` and `trace_context`.

Official guidance reviewed before implementation:

- [Python SDK instrumentation](https://langfuse.com/docs/observability/sdk/instrumentation)
- [SDK Experiments](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)
- [DeepEval integration](https://langfuse.com/resources/engineering/deepeval)
- [Datasets and upserts](https://langfuse.com/docs/evaluation/experiments/datasets)
- [Self-hosted compatibility](https://langfuse.com/self-hosting/upgrade/versioning)
- [v4 API migration](https://langfuse.com/faq/all/deprecated-api-migration)
