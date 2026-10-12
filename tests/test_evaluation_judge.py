"""Optional actual GEval/SDK integration with deterministic protocol responses, no real LLM."""

import importlib.util
import json
from pathlib import Path

import httpx2
import pytest

from dispatch.agent.config import ModelSettings
from dispatch.agent.llm import OpenAICompatibleClient
from dispatch.agent.models import Assessment, Draft
from dispatch.evaluation.dataset import load_dataset

deepeval_spec = importlib.util.find_spec("deepeval")

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        deepeval_spec is None or deepeval_spec.origin is None,
        reason="Optional: poetry install --with evals",
    ),
]


def completion(content):
    return {
        "id": "synthetic-judge",
        "object": "chat.completion",
        "created": 1,
        "model": "synthetic",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


@pytest.mark.parametrize("scores,expected", [([9, 8], "assessed"), ([11, -1], "unassessed")])
async def test_geval_uses_explicit_compatible_endpoint_and_separate_metrics(scores, expected):
    from dispatch.evaluation.judge import DraftJudge

    requests = []

    def handler(request):
        score = scores[len(requests)]
        requests.append(json.loads(request.content))
        return httpx2.Response(
            200, json=completion(json.dumps({"score": score, "reason": "Synthetic judge fixture."}))
        )

    settings = ModelSettings(base_url="http://localhost/v1", model="explicit-test-judge")
    client = OpenAICompatibleClient(settings, transport=httpx2.MockTransport(handler))
    case = load_dataset(Path(__file__).resolve().parents[1] / "evals" / "triage.jsonl")[0][0]
    assessment = Assessment(**case.expected.model_dump(), rationale="Synthetic")
    async with DraftJudge(settings, client=client) as judge:
        result = await judge.score(
            case, assessment, Draft(body="A different generated draft for review.")
        )
    assert len(requests) == 2
    assert all(request["model"] == "explicit-test-judge" for request in requests)
    assert all(metric["state"] == expected for metric in result.values())
    assert case.reference_draft.body in requests[0]["messages"][1]["content"]
    assert case.reference_draft.body not in requests[1]["messages"][1]["content"]
    if expected == "assessed":
        assert result["correctness_safety"]["score"] == 0.9
        assert result["assessment_consistency"]["score"] == 0.8


async def test_geval_failure_remains_unassessed_and_is_not_retried():
    from dispatch.evaluation.judge import DraftJudge

    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(500, text="SYNTHETIC PRIVATE PROVIDER BODY")

    settings = ModelSettings(base_url="http://localhost/v1", model="explicit-test-judge")
    client = OpenAICompatibleClient(settings, transport=httpx2.MockTransport(handler))
    case = load_dataset(Path(__file__).resolve().parents[1] / "evals" / "triage.jsonl")[0][0]
    async with DraftJudge(settings, client=client) as judge:
        result = await judge.score(
            case,
            Assessment(**case.expected.model_dump(), rationale="Synthetic"),
            Draft(body="Synthetic draft"),
        )
    assert len(calls) == 2
    assert all(metric["state"] == "unassessed" for metric in result.values())
    assert "PRIVATE PROVIDER" not in json.dumps(result)
