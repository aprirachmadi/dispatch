"""Local offline eval command, with optional DeepEval loaded only when explicitly used."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dispatch import observability
from dispatch.agent.config import load_model_settings
from dispatch.agent.llm import LLMError, OpenAICompatibleClient
from dispatch.agent.prompts import PROMPT_VERSION
from dispatch.agent.reporting import RunFile
from dispatch.evaluation.dataset import CalibrationCase, load_dataset
from dispatch.evaluation.runner import evaluate_cases, summarize


def eval_main() -> None:
    parser = argparse.ArgumentParser(description="Offline synthetic evals; never modify PSA data.")
    parser.add_argument("--dataset", type=Path, help="Override the JSONL dataset")
    parser.add_argument(
        "--limit", type=int, help="Evaluate a labeled subset (1–100); default all cases"
    )
    parser.add_argument(
        "--no-judge", action="store_true", help="Generate and score fields without DeepEval"
    )
    parser.add_argument(
        "--calibrate-judge",
        action="store_true",
        help="Judge known-good/bad drafts only; no agent calls",
    )
    parser.add_argument(
        "--validate-only", action="store_true", help="Check dataset without models or DeepEval"
    )
    parser.add_argument(
        "--output", type=Path, help="New local JSON report path; never overwrite another run"
    )
    parser.add_argument(
        "--import-langfuse", action="store_true", help="Import the full dataset without model calls"
    )
    parser.add_argument("--langfuse-dataset", help="Override the Langfuse dataset name")
    arguments = parser.parse_args()
    if arguments.limit is not None and not 1 <= arguments.limit <= 100:
        parser.error("--limit must be 1–100")
    if arguments.calibrate_judge and arguments.no_judge:
        parser.error("--calibrate-judge cannot be combined with --no-judge")
    try:
        code = asyncio.run(_evaluate(arguments))
    except ImportError:
        parser.exit(1, "Offline judge unavailable: poetry install --with evals\n")
    except (ValueError, OSError, LLMError):
        parser.exit(1, "Evaluation stopped: check dataset, model configuration and local report.\n")
    raise SystemExit(code)


async def _evaluate(arguments) -> int:
    path = arguments.dataset or Path(
        "evals/judge_calibration.jsonl" if arguments.calibrate_judge else "evals/triage.jsonl"
    )
    cases, digest = (
        load_dataset(path, CalibrationCase) if arguments.calibrate_judge else load_dataset(path)
    )
    if arguments.validate_only:
        print(f"VALID: {len(cases)} cases; SHA-256={digest}. No model calls or PSA access.")
        return 0
    langfuse = observability.get_langfuse()
    items = None
    if langfuse is not None:
        from dispatch.evaluation.langfuse import import_dataset

        if not langfuse.auth_check():
            raise ValueError("Langfuse project API authentication failed")
        items = import_dataset(
            langfuse,
            cases,
            digest=digest,
            name=arguments.langfuse_dataset
            or ("dispatch/judge-calibration" if arguments.calibrate_judge else "dispatch/triage"),
        )
    if arguments.import_langfuse:
        if langfuse is None:
            raise ValueError(
                "Configure LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY"
            )
        print(f"Imported {len(items)} Langfuse dataset items. No model calls.")
        return 0
    full_count = len(cases)
    if arguments.limit is not None:
        cases = cases[: arguments.limit]
        if items is not None:
            items = items[: arguments.limit]
    # Validate all required settings before issuing any model requests.
    agent_settings = None if arguments.calibrate_judge else load_model_settings()
    judge_settings = None if arguments.no_judge else load_model_settings(judge=True)
    judge_type = None
    if judge_settings:
        from dispatch.evaluation.judge import DraftJudge

        judge_type = DraftJudge
    run_id = str(uuid4())
    report = RunFile(arguments.output or Path(f"data/evals-{run_id}.json"))
    payload = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "state": "running",
        "mode": "judge_calibration" if arguments.calibrate_judge else "frozen_context_eval",
        "dataset_sha256": digest,
        "dataset_cases": full_count,
        "evaluated_cases": len(cases),
        "subset": len(cases) != full_count,
        "prompt_version": PROMPT_VERSION,
        "agent_model": agent_settings.model if agent_settings else None,
        "judge_model": judge_settings.model if judge_settings else None,
        "judge_rubric_version": "draft-geval-v1" if judge_settings else None,
        "judge_threshold_status": "provisional_uncalibrated",
        "rows": [],
    }
    report.write(payload)
    print(f"Offline run: {len(cases)} cases. Report: {report.path}. No PSA access or mutations.")

    def progress(rows):
        payload["rows"] = rows
        report.write(payload)
        print(f"Completed {len(rows)}/{len(cases)}")

    try:
        experiment_rows = None
        if langfuse is not None:
            from dispatch.evaluation.langfuse import run_experiment

            experiment_rows = run_experiment(
                langfuse,
                cases,
                items=items,
                payload=payload,
                agent_settings=agent_settings,
                judge_settings=judge_settings,
                judge_type=judge_type,
                progress=progress,
            )
        if arguments.calibrate_judge:
            if experiment_rows is not None:
                rows = experiment_rows
            else:
                async with judge_type(judge_settings) as judge:
                    rows = []
                    for case in cases:
                        scores = await judge.score(case.case, case.assessment, case.draft)
                        expected = {
                            "correctness_safety": case.expected_quality_pass,
                            "assessment_consistency": case.expected_consistency_pass,
                        }
                        agreements = {
                            name: score.get("passes_provisional_threshold") == expected[name]
                            if score["state"] == "assessed"
                            else None
                            for name, score in scores.items()
                        }
                        rows.append(
                            {
                                "case_id": case.case.case_id,
                                "judge": scores,
                                "expected_pass": expected,
                                "agrees_with_fixture": agreements,
                            }
                        )
                        progress(rows)
            incomplete = any(
                score["state"] != "assessed" for row in rows for score in row["judge"].values()
            )
            payload["summary"] = {
                "fixture_agreements": {
                    name: sum(row["agrees_with_fixture"][name] is True for row in rows)
                    for name in ("correctness_safety", "assessment_consistency")
                },
                "total": len(rows),
                "note": "Calibration diagnostic, not a classification score "
                "or automatic threshold tuning.",
            }
        else:
            if experiment_rows is not None:
                rows = experiment_rows
            else:
                async with OpenAICompatibleClient(agent_settings) as model:
                    if judge_type:
                        async with judge_type(judge_settings) as judge:
                            rows = await evaluate_cases(
                                cases, model, judge=judge, progress=progress
                            )
                    else:
                        rows = await evaluate_cases(cases, model, progress=progress)
            payload["summary"] = summarize(cases, rows, judge_requested=judge_type is not None)
            incomplete = any(row["draft_state"] != "valid" for row in rows) or any(
                score["state"] != "assessed" for row in rows for score in row["judge"].values()
            )
        payload["state"] = "incomplete" if incomplete else "completed"
        print(json.dumps(payload["summary"], indent=2))
        print(
            "Evaluation executed. Low scores are findings, "
            "not execution errors; inspect the report."
        )
        return 1 if incomplete else 0
    finally:
        if payload["state"] == "running":
            payload["state"] = "interrupted_or_failed"
        payload["finished_at"] = datetime.now(UTC).isoformat()
        report.write(payload)
        observability.flush_langfuse()


if __name__ == "__main__":
    eval_main()
