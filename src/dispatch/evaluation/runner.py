"""Frozen-context, no-write offline evals using the same classifier/drafter as live triage."""

from collections.abc import Callable

from dispatch.agent.llm import LLMError, ModelClient
from dispatch.agent.models import Assessment
from dispatch.agent.prompts import classify, draft_response
from dispatch.evaluation.dataset import EvalCase
from dispatch.evaluation.scoring import classification_scores


async def evaluate_cases(
    cases: list[EvalCase],
    model: ModelClient,
    *,
    judge=None,
    progress: Callable | None = None,
) -> list[dict]:
    """Continue across failed cases, retain valid labels when retrieval/drafting fails."""
    rows = []
    for case in cases:
        row = {
            "case_id": case.case_id,
            "input": case.problem.model_dump(mode="json"),
            "historical_context": [item.model_dump(mode="json") for item in case.history],
            "expected": case.expected.model_dump(mode="json"),
            "reference_draft": case.reference_draft.model_dump(mode="json"),
            "assessment": None,
            "draft": None,
            "classification_state": "failed",
            "draft_state": "not_attempted",
            "judge": {},
            "error": None,
        }
        try:
            assessment = await classify(model, case.problem)
            row["assessment"] = assessment.model_dump(mode="json")
            row["classification_state"] = "valid"
            row["draft_state"] = "failed"
            draft = await draft_response(model, case.problem, assessment, case.history)
            row["draft"] = draft.model_dump(mode="json")
            row["draft_state"] = "valid"
            if judge is not None:
                row["judge"] = await judge.score(case, assessment, draft)
        except (LLMError, ValueError) as error:
            row["error"] = str(error) if isinstance(error, LLMError) else "Input contract failed."
        rows.append(row)
        if progress:
            progress(rows)
    return rows


def summarize(cases: list[EvalCase], rows: list[dict], *, judge_requested: bool) -> dict:
    if [case.case_id for case in cases] != [row["case_id"] for row in rows]:
        raise ValueError("Cases and predictions must align exactly")
    predictions = [
        Assessment.model_validate(row["assessment"]) if row["assessment"] else None for row in rows
    ]
    total = len(cases)
    drafts = sum(row["draft_state"] == "valid" for row in rows)
    judge_metrics = {}
    for name in ("correctness_safety", "assessment_consistency"):
        assessed = [
            row["judge"][name]
            for row in rows
            if row["judge"].get(name, {}).get("state") == "assessed"
        ]
        judge_metrics[name] = {
            "assessed": len(assessed),
            "coverage_over_all_cases": len(assessed) / total,
            "missing_draft": total - drafts,
            "unassessed_valid_drafts": drafts - len(assessed) if judge_requested else None,
            "mean_score_assessed_only": sum(item["score"] for item in assessed) / len(assessed)
            if assessed
            else None,
            "provisional_passes": sum(item["passes_provisional_threshold"] for item in assessed),
            "provisional_pass_rate_over_all_cases": (
                sum(item["passes_provisional_threshold"] for item in assessed) / total
            )
            if judge_requested
            else None,
        }
    return {
        "classification": classification_scores([case.expected for case in cases], predictions),
        "draft_completion": {
            "total": total,
            "valid": drafts,
            "missing_or_failed": total - drafts,
            "rate": drafts / total,
        },
        "draft_quality": {
            "judge_requested": judge_requested,
            "metrics": judge_metrics,
            "threshold_status": "provisional_uncalibrated",
        },
        "scope": "Frozen-context generation eval; no MCP mutations. "
        "Live orchestration safety is tested separately.",
    }
