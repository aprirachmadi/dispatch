"""Langfuse datasets and experiments over the existing local evaluation contracts."""

from uuid import NAMESPACE_URL, uuid5

from langfuse import Evaluation

from dispatch.agent.llm import OpenAICompatibleClient
from dispatch.evaluation.dataset import CalibrationCase
from dispatch.evaluation.runner import evaluate_cases, summarize
from dispatch.evaluation.scoring import LABEL_VALUES


def item_id(dataset_name, case_id):
    return str(uuid5(NAMESPACE_URL, f"dispatch:{dataset_name}:{case_id}"))


def import_dataset(client, cases, *, name, digest):
    """Stable project-wide item IDs make reimports upserts, including revised cases."""
    client.create_dataset(
        name=name,
        description="Dispatch synthetic frozen-context golden evaluation set",
        metadata={"dataset_sha256": digest, "source": "dispatch", "synthetic": True},
    )
    items = []
    for fixture in cases:
        calibration = isinstance(fixture, CalibrationCase)
        case = fixture.case if calibration else fixture
        expected = {
            "classification": case.expected.model_dump(mode="json"),
            "reference_draft": case.reference_draft.model_dump(mode="json"),
            "response_requirements": case.response_requirements,
        }
        inputs = {
            "problem": case.problem.model_dump(mode="json"),
            "history": [entry.model_dump(mode="json") for entry in case.history],
        }
        if calibration:
            inputs.update(
                assessment=fixture.assessment.model_dump(mode="json"),
                draft=fixture.draft.model_dump(mode="json"),
            )
            expected.update(
                expected_quality_pass=fixture.expected_quality_pass,
                expected_consistency_pass=fixture.expected_consistency_pass,
            )
        items.append(
            client.create_dataset_item(
                dataset_name=name,
                id=item_id(name, case.case_id),
                input=inputs,
                expected_output=expected,
                metadata={
                    "case_id": case.case_id,
                    "tags": case.tags,
                    "dataset_sha256": digest,
                    "synthetic": True,
                },
            )
        )
    return items


def row_evaluations(*, output, **kwargs):
    """Export existing scores; missing judge outputs remain unassessed, never zero/pass."""
    evaluations = []
    if "draft_state" in output:
        prediction = output["assessment"] or {}
        target = output["expected"]
        matches = []
        for field in LABEL_VALUES:
            match = prediction.get(field) == target[field]
            matches.append(match)
            evaluations.append(Evaluation(name=f"classification_{field}", value=int(match)))
        evaluations.extend(
            [
                Evaluation(name="classification_exact_match", value=int(all(matches))),
                Evaluation(name="draft_completion", value=int(output["draft_state"] == "valid")),
            ]
        )
    for name, result in output["judge"].items():
        if result["state"] == "assessed":
            evaluations.append(
                Evaluation(
                    name=name,
                    value=result["score"],
                    comment=result.get("reason"),
                    metadata={"threshold_status": "provisional_uncalibrated"},
                )
            )
    for name, agreement in output.get("agrees_with_fixture", {}).items():
        if agreement is not None:
            evaluations.append(Evaluation(name=f"{name}_fixture_agreement", value=int(agreement)))
    return evaluations


def run_experiment(
    client, cases, *, items, payload, agent_settings, judge_settings, judge_type, progress
):
    """Create clients in the SDK task loop; keep existing sequential case behavior."""
    by_id = {item.id: case for item, case in zip(items, cases, strict=True)}
    rows = []

    async def task(*, item):
        fixture = by_id[item.id]
        if isinstance(fixture, CalibrationCase):
            async with judge_type(judge_settings) as judge:
                scores = await judge.score(fixture.case, fixture.assessment, fixture.draft)
            expected = {
                "correctness_safety": fixture.expected_quality_pass,
                "assessment_consistency": fixture.expected_consistency_pass,
            }
            row = {
                "case_id": fixture.case.case_id,
                "judge": scores,
                "expected_pass": expected,
                "agrees_with_fixture": {
                    name: score.get("passes_provisional_threshold") == expected[name]
                    if score["state"] == "assessed"
                    else None
                    for name, score in scores.items()
                },
            }
        else:
            async with OpenAICompatibleClient(agent_settings) as model:
                if judge_type:
                    async with judge_type(judge_settings) as judge:
                        row = (await evaluate_cases([fixture], model, judge=judge))[0]
                else:
                    row = (await evaluate_cases([fixture], model))[0]
        rows.append(row)
        progress(list(rows))
        return row

    def aggregate(*, item_results, **kwargs):
        if agent_settings is None:
            return [
                Evaluation(
                    name=f"{name}_fixture_agreement_rate",
                    value=sum(row["agrees_with_fixture"][name] is True for row in rows)
                    / len(cases),
                )
                for name in ("correctness_safety", "assessment_consistency")
            ]
        summary = summarize(cases, rows, judge_requested=judge_type is not None)
        values = {
            "classification_exact_match_accuracy": summary["classification"][
                "exact_match_accuracy"
            ],
            "draft_completion_rate": summary["draft_completion"]["rate"],
        }
        for field, scores in summary["classification"]["fields"].items():
            values[f"classification_{field}_accuracy"] = scores["accuracy"]
            values[f"classification_{field}_macro_f1"] = scores["macro_f1"]
        for name, scores in summary["classification"]["critical_case_recall"].items():
            if scores["recall"] is not None:
                values[f"{name}_recall"] = scores["recall"]
        if judge_type is not None:
            for name, scores in summary["draft_quality"]["metrics"].items():
                values[f"{name}_coverage"] = scores["coverage_over_all_cases"]
                values[f"{name}_provisional_pass_rate"] = scores[
                    "provisional_pass_rate_over_all_cases"
                ]
                if scores["mean_score_assessed_only"] is not None:
                    values[f"{name}_mean_assessed"] = scores["mean_score_assessed_only"]
        return [Evaluation(name=name, value=value) for name, value in values.items()]

    result = client.run_experiment(
        name="Dispatch judge calibration" if agent_settings is None else "Dispatch benchmark",
        run_name=payload["run_id"],
        data=items,
        task=task,
        evaluators=[row_evaluations],
        run_evaluators=[aggregate],
        max_concurrency=1,
        metadata={
            key: str(payload[key])
            for key in (
                "dataset_sha256",
                "prompt_version",
                "agent_model",
                "judge_model",
                "judge_rubric_version",
                "judge_threshold_status",
            )
        },
    )
    payload["langfuse"] = {
        "dataset_run_id": result.dataset_run_id,
        "dataset_run_url": result.dataset_run_url,
        "experiment_id": result.experiment_id,
    }
    if len(rows) != len(cases) or len(result.item_results) != len(cases):
        raise ValueError("Langfuse experiment did not complete all evaluation items")
    if not result.dataset_run_id:
        raise ValueError("Langfuse experiment dataset association failed; inspect the local report")
    for row, item_result in zip(rows, result.item_results, strict=True):
        expected_scores = {score.name: score.value for score in row_evaluations(output=row)}
        actual_scores = {score.name: score.value for score in item_result.evaluations}
        if (
            not item_result.trace_id
            or item_result.dataset_run_id != result.dataset_run_id
            or actual_scores != expected_scores
        ):
            raise ValueError("Langfuse experiment item tracing or score mapping failed")
    expected_run_scores = {score.name: score.value for score in aggregate(item_results=[])}
    if {score.name: score.value for score in result.run_evaluations} != expected_run_scores:
        raise ValueError("Langfuse experiment aggregate score mapping failed")
    return rows
