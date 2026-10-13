# Offline evaluations

[Back to Dispatch](../README.md)

Measure classification, draft completion, and optionally separate judge scores. **This pipeline does
not read or update the PSA database.** Low scores are findings, not proof that the application
pipeline failed.

## Generator and separate judge

Configure the generator using the
[model setup guide](TRIAGE.md#install-and-configure-your-chosen-model). Run
`poetry install --with evals` only if you want the optional DeepEval judge. Judge endpoint/model/key
settings are separate; live triage never runs a judge.

## Run all 40 labeled cases without a judge

```bash
OPENAI_OUTPUT_MODE=json_schema \
poetry run python -m dispatch.evaluation.cli --no-judge
```

For providers without JSON Schema support, use their supported output mode instead. This generates
classifications and responses with frozen context. It does not exercise live MCP retrieval or update
the SQLite tickets shown in the console.

## Dataset, judging, and report semantics

Read [the label and draft rubric](../evals/RUBRIC.md). `evals/triage.jsonl` has 40 synthetic,
developer-labeled cases with frozen context, expected fields and reference drafts. This evaluates
the same classification/draft functions, not live similarity retrieval. No ticket database, drafts
or audit journal is read or modified by this offline runner.

**Validate data without any models:**

```bash
poetry run python -m dispatch.evaluation.cli --validate-only
```

Expect `VALID: 40 cases`. Start with a cheap two-case generation run, without judging:

```bash
poetry run python -m dispatch.evaluation.cli --limit 2 --no-judge
```

For judged runs, explicitly set the separate `DISPATCH_JUDGE_BASE_URL`, `DISPATCH_JUDGE_MODEL`, and
`DISPATCH_JUDGE_API_KEY` variables. The same provider/model may be used, but there is no silent
fallback to the agent configuration. The judge must produce schema-valid JSON; native function
calling/logprobs are not required.

```bash
# Six known-good/bad fixtures, judge calls only (no agent generation):
poetry run python -m dispatch.evaluation.cli --calibrate-judge
# Small judged smoke run, then all 40 cases:
poetry run python -m dispatch.evaluation.cli --limit 2
poetry run python -m dispatch.evaluation.cli
```

The default run includes two separate GEval metrics: correctness/safety against the
reference/source, and consistency with the generated assessment. Explicit versioned steps accept
different valid wording; critical unsafe/fabricated-action drafts are assigned the low score band.
The 0.8 diagnostic threshold is **provisional/uncalibrated**; inspect calibration scores/reasons
before interpreting pass rates. No automated tuning to achieve a desired score, live judge gate or
automatic regeneration is implemented.

Runs are sequential and bounded to 100 dataset cases (default 40). A fully successful 40-case run
uses 80 generation requests plus 80 judge requests with current explicit steps/non-logprob GEval
path. Tokens, latency and cost depend on your selected models; use `--limit` before the full run.
References/gold labels never enter generation prompts. Only the configured model endpoints receive
synthetic text; DeepEval telemetry, tracing, keyfile loading and cloud uploads are disabled. No
Confident AI login is used.

Reports go to new `data/evals-<uuid>.json` files, including dataset hash, model names, prompt/rubric
versions, per-case input/reference/prediction/draft/judge reasons and:

- Classification accuracy, macro-F1, confusion matrices, exact match and critical recall.
- Draft completion (missing/invalid drafts fail automatically without a judge call).
- Separate GEval scores/coverage. Judge failures remain unassessed, never passes.

No blended classification/draft score is produced. Subsets are marked as subsets and may omit
classes (macro-F1 still includes every declared class). Low scores are findings, not command errors;
missing outputs/judge failures return exit code 1 and an incomplete report. Interrupted runs retain
available progress. Human-review a sample of judged drafts; this small correlated development set
and an LLM judge are not production proof. Initial real-model findings are recorded in
[engineering decisions](../DECISIONS.md#real-model-evidence-received-before-ui-planning). They are
development-set results, not production accuracy or a calibrated judge result.
