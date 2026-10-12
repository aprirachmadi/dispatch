"""Golden labels stay out of prompts; draft failures never inflate classification accuracy."""

import json
from pathlib import Path

import pytest

from dispatch.agent.llm import LLMError
from dispatch.agent.models import Assessment, Draft, Labels
from dispatch.evaluation.dataset import CalibrationCase, load_dataset
from dispatch.evaluation.runner import evaluate_cases, summarize
from dispatch.evaluation.scoring import classification_scores

ROOT = Path(__file__).resolve().parents[1]


def cases():
    return load_dataset(ROOT / "evals" / "triage.jsonl")[0]


def test_dataset_is_40_synthetic_cases_with_all_labels_and_traps():
    data, digest = load_dataset(ROOT / "evals" / "triage.jsonl")
    assert len(data) == 40 and len(digest) == 64
    assert {case.expected.category.value for case in data} == {
        "email",
        "network",
        "hardware",
        "security",
        "onboarding",
        "alerts",
    }
    assert {case.expected.priority.value for case in data} == {"low", "medium", "high", "urgent"}
    assert {case.expected.urgency.value for case in data} == {"routine", "same_day", "immediate"}
    assert sum("duplicate_pair" in case.tags for case in data) == 2
    assert any("prompt_injection" in case.tags for case in data)
    assert any("casual_urgent" in case.tags and case.expected.security_flag for case in data)
    assert all(
        history.resolved_at < case.problem.created_at for case in data for history in case.history
    )


def test_calibration_fixtures_are_separate_and_known_good_bad():
    data, _ = load_dataset(ROOT / "evals" / "judge_calibration.jsonl", CalibrationCase)
    assert len(data) == 6
    assert sum(case.expected_quality_pass for case in data) == 2
    assert {case.case.case_id for case in data}.isdisjoint({case.case_id for case in cases()})


@pytest.mark.parametrize("content", ["", "not json", "{}", b"\xff", "x" * 1_048_577])
def test_bad_dataset_fails_visibly(tmp_path, content):
    path = tmp_path / "bad.jsonl"
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    with pytest.raises(ValueError):
        load_dataset(path)


def test_duplicate_or_future_context_rejected(tmp_path):
    path = tmp_path / "bad.jsonl"
    case = cases()[1]
    path.write_text(case.model_dump_json() + "\n" + case.model_dump_json())
    with pytest.raises(ValueError):
        load_dataset(path)
    payload = case.model_dump(mode="json")
    payload["history"][0]["resolved_at"] = payload["problem"]["created_at"]
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_dataset(path)


def test_field_scores_exact_match_missing_denominator_and_critical_recall():
    target = Labels(category="security", priority="urgent", urgency="immediate", security_flag=True)
    wrong = Labels(category="security", priority="high", urgency="immediate", security_flag=False)
    report = classification_scores([target, target, target], [target, wrong, None])
    assert report["fields"]["category"]["accuracy"] == 2 / 3
    assert report["fields"]["priority"]["accuracy"] == 1 / 3
    assert report["exact_match_accuracy"] == 1 / 3
    assert report["critical_case_recall"]["security"]["recall"] == 1 / 3
    assert report["critical_case_recall"]["urgent"]["recall"] == 1 / 3
    assert report["invalid_or_failed"] == 1
    assert report["fields"]["priority"]["confusion_matrix"]["urgent"]["missing"] == 1


def test_macro_f1_all_declared_classes_and_absent_positive_recall():
    data = cases()
    labels = [case.expected for case in data]
    report = classification_scores(labels, labels)
    assert all(item["macro_f1"] == 1 for item in report["fields"].values())
    normal = [
        case.expected
        for case in data
        if not case.expected.security_flag and case.expected.priority != "urgent"
    ]
    report = classification_scores(normal, normal)
    assert report["critical_case_recall"]["urgent"]["recall"] is None
    assert report["critical_case_recall"]["security"]["recall"] is None


class PredictionModel:
    def __init__(self, case, failure=None):
        self.case = case
        self.failure = failure
        self.calls = []

    async def generate(self, system, user, schema):
        self.calls.append((system, user))
        if schema is Assessment:
            if self.failure == "classification":
                raise LLMError("Synthetic classification failure.")
            return Assessment(
                **self.case.expected.model_dump(), rationale="Synthetic test fixture."
            )
        if self.failure == "draft":
            raise LLMError("Synthetic invalid draft.")
        return Draft(
            body="Synthetic generated draft, deliberately different from the gold reference."
        )


@pytest.mark.anyio
async def test_references_never_enter_generation_and_draft_scoring_separate():
    case = cases()[0]
    model = PredictionModel(case, failure="draft")
    rows = await evaluate_cases([case], model)
    assert case.reference_draft.body not in "\n".join(user for _, user in model.calls)
    assert "expected" not in json.loads(model.calls[0][1])
    report = summarize([case], rows, judge_requested=False)
    assert report["classification"]["exact_match_accuracy"] == 1
    assert report["draft_completion"]["valid"] == 0
    assert report["draft_completion"]["missing_or_failed"] == 1


@pytest.mark.anyio
async def test_classifier_failure_counted_and_drafting_not_attempted():
    case = cases()[0]
    model = PredictionModel(case, failure="classification")
    rows = await evaluate_cases([case], model)
    assert len(model.calls) == 1 and rows[0]["draft_state"] == "not_attempted"
    report = summarize([case], rows, judge_requested=True)
    assert report["classification"]["exact_match_accuracy"] == 0
    assert report["draft_quality"]["metrics"]["correctness_safety"]["missing_draft"] == 1


@pytest.mark.anyio
async def test_judge_failure_is_unassessed_not_silent_pass():
    case = cases()[0]

    class UnavailableJudge:
        async def score(self, *args):
            return {
                name: {"state": "unassessed", "score": None}
                for name in ("correctness_safety", "assessment_consistency")
            }

    rows = await evaluate_cases([case], PredictionModel(case), judge=UnavailableJudge())
    report = summarize([case], rows, judge_requested=True)
    assert report["classification"]["exact_match_accuracy"] == 1
    assert report["draft_completion"]["valid"] == 1
    quality = report["draft_quality"]["metrics"]["correctness_safety"]
    assert quality["assessed"] == 0 and quality["unassessed_valid_drafts"] == 1
    assert quality["provisional_pass_rate_over_all_cases"] == 0


@pytest.mark.anyio
async def test_low_judge_score_does_not_change_classification():
    case = cases()[0]

    class BadDraftJudge:
        async def score(self, *args):
            return {
                name: {"state": "assessed", "score": 0.1, "passes_provisional_threshold": False}
                for name in ("correctness_safety", "assessment_consistency")
            }

    rows = await evaluate_cases([case], PredictionModel(case), judge=BadDraftJudge())
    report = summarize([case], rows, judge_requested=True)
    assert report["classification"]["exact_match_accuracy"] == 1
    assert (
        report["draft_quality"]["metrics"]["correctness_safety"]["mean_score_assessed_only"] == 0.1
    )
