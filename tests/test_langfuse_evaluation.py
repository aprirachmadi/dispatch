"""Dataset upserts and score mapping preserve the offline evaluation contracts."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from dispatch.evaluation.dataset import CalibrationCase, load_dataset
from dispatch.evaluation.langfuse import import_dataset, row_evaluations

ROOT = Path(__file__).resolve().parents[1]


class DatasetStore:
    def __init__(self):
        self.items = {}

    def create_dataset(self, **kwargs):
        pass

    def create_dataset_item(self, **kwargs):
        item = SimpleNamespace(**kwargs)
        self.items[item.id] = item
        return item


def test_import_is_idempotent_preserves_gold_and_updates_same_case():
    cases, digest = load_dataset(ROOT / "evals/triage.jsonl")
    store = DatasetStore()
    first = import_dataset(store, cases, name="dispatch/triage", digest=digest)
    second = import_dataset(store, cases, name="dispatch/triage", digest=digest)
    assert len(store.items) == 40
    assert [item.id for item in first] == [item.id for item in second]
    assert first[0].input["problem"] == cases[0].problem.model_dump(mode="json")
    assert first[0].expected_output["classification"] == cases[0].expected.model_dump(mode="json")
    assert first[0].expected_output["response_requirements"] == cases[0].response_requirements
    updated = cases[0].model_copy(update={"response_requirements": ["Revised expected step"]})
    revised = import_dataset(store, [updated], name="dispatch/triage", digest="revised")
    assert revised[0].id == first[0].id
    assert len(store.items) == 40
    assert revised[0].expected_output["response_requirements"] == ["Revised expected step"]


def test_calibration_import_keeps_fixture_draft_and_expected_passes():
    cases, digest = load_dataset(ROOT / "evals/judge_calibration.jsonl", CalibrationCase)
    items = import_dataset(DatasetStore(), cases, name="dispatch/calibration", digest=digest)
    assert items[0].input["draft"] == cases[0].draft.model_dump(mode="json")
    assert items[0].expected_output["expected_quality_pass"] == cases[0].expected_quality_pass


def test_unassessed_metrics_do_not_become_zero_or_pass_scores():
    case = load_dataset(ROOT / "evals/triage.jsonl")[0][0]
    row = {
        "assessment": case.expected.model_dump(mode="json"),
        "expected": case.expected.model_dump(mode="json"),
        "draft_state": "failed",
        "judge": {
            "correctness_safety": {"state": "unassessed", "score": None},
            "assessment_consistency": {
                "state": "assessed",
                "score": 0.7,
                "reason": "Minor omission",
            },
        },
    }
    scores = {item.name: item for item in row_evaluations(output=row)}
    assert "correctness_safety" not in scores
    assert scores["correctness_safety_assessed"].value == 0
    assert scores["assessment_consistency"].value == 0.7
    assert scores["assessment_consistency"].comment == "Minor omission"
    assert scores["classification_exact_match"].value == 1
    assert scores["draft_completion"].value == 0


@pytest.mark.parametrize("failure", [None, "link", "scores", "aggregate"])
def test_experiment_reuses_runner_and_preserves_case_order(monkeypatch, failure):
    import asyncio

    from dispatch.agent.models import Assessment, Draft
    from dispatch.evaluation.langfuse import run_experiment

    cases, digest = load_dataset(ROOT / "evals/triage.jsonl")
    cases = cases[:2]

    class FakeModel:
        def __init__(self, settings):
            self.case = cases[0]
            self.failure = None
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def generate(self, system, user, schema):
            import json

            if schema is Assessment:
                problem = json.loads(user)
                self.case = next(
                    case for case in cases if case.problem.subject == problem["subject"]
                )
            if schema is Assessment:
                return Assessment(**self.case.expected.model_dump(), rationale="Test fixture")
            return Draft(body="Synthetic unsent draft for the evaluation test.")

    class Experiments(DatasetStore):
        def run_experiment(self, **kwargs):
            assert kwargs["max_concurrency"] == 1
            assert kwargs["run_name"] == "test-run"

            async def execute():
                results = []
                for item in kwargs["data"]:
                    output = await kwargs["task"](item=item)
                    scores = kwargs["evaluators"][0](output=output)
                    assert any(score.name == "classification_exact_match" for score in scores)
                    results.append(
                        SimpleNamespace(
                            evaluations=[] if failure == "scores" else scores,
                            trace_id="trace",
                            dataset_run_id=None if failure == "link" else "run",
                        )
                    )
                return results

            results = asyncio.run(execute())
            return SimpleNamespace(
                item_results=results,
                run_evaluations=[]
                if failure == "aggregate"
                else kwargs["run_evaluators"][0](item_results=results),
                dataset_run_id="run",
                dataset_run_url="http://example/run",
                experiment_id="experiment",
            )

    monkeypatch.setattr("dispatch.evaluation.langfuse.OpenAICompatibleClient", FakeModel)
    client = Experiments()
    items = import_dataset(client, cases, name="test", digest=digest)
    payload = {
        key: "test"
        for key in (
            "dataset_sha256",
            "prompt_version",
            "agent_model",
            "judge_model",
            "judge_rubric_version",
            "judge_threshold_status",
        )
    }
    payload["run_id"] = "test-run"
    progress = []
    if failure:
        with pytest.raises(ValueError, match="Langfuse experiment"):
            run_experiment(
                client,
                cases,
                items=items,
                payload=payload,
                agent_settings=SimpleNamespace(model="synthetic"),
                judge_settings=None,
                judge_type=None,
                progress=progress.append,
            )
        return
    rows = run_experiment(
        client,
        cases,
        items=items,
        payload=payload,
        agent_settings=SimpleNamespace(model="synthetic"),
        judge_settings=None,
        judge_type=None,
        progress=progress.append,
    )
    assert [row["case_id"] for row in rows] == [case.case_id for case in cases]
    assert len(progress) == 2
    assert payload["langfuse"]["dataset_run_id"] == "run"
