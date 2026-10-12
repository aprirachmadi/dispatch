"""Model transport/contract tests; never contact a real model or print credentials."""

import json

import httpx2
import pytest
from pydantic import ValidationError

from dispatch.agent.config import ModelSettings, load_model_settings
from dispatch.agent.llm import LLMError, OpenAICompatibleClient, parse_output
from dispatch.agent.models import Assessment, Draft

LABELS = {
    "category": "security",
    "priority": "urgent",
    "urgency": "immediate",
    "security_flag": True,
    "rationale": "Credentials entered on a suspicious site.",
}


def completion(content, finish_reason="stop", **message):
    return {
        "id": "synthetic-completion",
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content, **message},
            }
        ],
    }


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:1234/v1", "http://localhost/v1", "http://[::1]/v1"]
)
def test_keyless_local_settings(url):
    settings = ModelSettings(base_url=url, model="synthetic")
    assert settings.api_key.get_secret_value() == ""


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://remote.test/v1",
        "https://user:secret@remote.test/v1",
        "https://remote.test/v1?secret=bad",
        "https://remote.test/v1#bad",
        "file:///tmp/model",
        "https://remote.test:99999/v1",
        "https://remote.test:0/v1",
        "https://remote.test /v1",
    ],
)
def test_untrusted_settings_fail(url):
    with pytest.raises(ValidationError):
        ModelSettings(base_url=url, model="synthetic", api_key="SYNTHETIC-SECRET")


def test_remote_requires_key_and_repr_redacts():
    with pytest.raises(ValidationError):
        ModelSettings(base_url="https://remote.test/v1", model="synthetic")
    settings = ModelSettings(
        base_url="https://remote.test/v1", model="synthetic", api_key="SYNTHETIC-SECRET"
    )
    assert "SYNTHETIC-SECRET" not in repr(settings)


def test_configuration_error_redacts_values(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://SYNTHETIC-SECRET@remote.test/v1")
    monkeypatch.setenv("OPENAI_MODEL", "synthetic")
    with pytest.raises(ValueError) as error:
        load_model_settings()
    assert "SYNTHETIC-SECRET" not in str(error.value)


def test_judge_never_inherits_agent_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("OPENAI_MODEL", "synthetic-agent")
    for key in ("DISPATCH_JUDGE_BASE_URL", "DISPATCH_JUDGE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="DISPATCH_JUDGE_"):
        load_model_settings(judge=True)


@pytest.mark.parametrize(
    "changes",
    [
        {"security_flag": "true"},
        {"security_flag": 1},
        {"category": "other"},
        {"priority": "critical"},
        {"urgency": "soon"},
        {"rationale": " "},
        {"rationale": "x" * 1001},
        {"ticket_id": 999},
        {"status": "closed"},
    ],
)
def test_assessment_rejects_invalid_fields(changes):
    with pytest.raises(LLMError):
        parse_output(json.dumps({**LABELS, **changes}), Assessment)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not JSON",
        '```json\n{"body":"Hello"}\n```',
        "[]",
        '{"body":"Hello","body":"Bye"}',
        '{"body":NaN}',
        '{"body":123}',
        '{"body":" "}',
        json.dumps({"body": "x" * 6001}),
        json.dumps({"body": "Hi", "approve": True}),
        "x" * 65537,
    ],
)
def test_invalid_draft_contract(text):
    with pytest.raises(LLMError):
        parse_output(text, Draft)


@pytest.mark.anyio
@pytest.mark.parametrize("output_mode", ["text", "json_object", "json_schema"])
async def test_real_sdk_request_and_schema_validation(output_mode):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=completion(json.dumps(LABELS)))

    settings = ModelSettings(
        base_url="http://localhost/v1",
        model="test-model",
        output_mode=output_mode,
        token_parameter="max_completion_tokens",
    )
    async with OpenAICompatibleClient(settings, transport=httpx2.MockTransport(handler)) as client:
        result = await client.generate("Trusted instructions", "Untrusted input", Assessment)
        assert result.requires_attention
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/chat/completions"
    assert body["model"] == "test-model"
    assert "tools" not in body and "temperature" not in body
    assert body["max_completion_tokens"] == 2048
    assert ("response_format" in body) == (output_mode != "text")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        completion('{"body":"Hi"}', finish_reason="length"),
        completion(None, refusal="Provider refusal"),
        {"choices": []},
        {},
        {"choices": [{"finish_reason": "stop", "message": None}]},
    ],
)
async def test_invalid_provider_envelopes_are_safe(response):
    settings = ModelSettings(base_url="http://localhost/v1", model="test-model")
    async with OpenAICompatibleClient(
        settings,
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=response)),
    ) as client:
        with pytest.raises(LLMError):
            await client.generate("Trusted", "Synthetic", Draft)


@pytest.mark.anyio
async def test_failure_never_retries_or_leaks_provider_body():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(500, text="SYNTHETIC-SECRET provider body")

    settings = ModelSettings(base_url="http://localhost/v1", model="test-model")
    async with OpenAICompatibleClient(settings, transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(LLMError) as error:
            await client.generate("Trusted", "Synthetic", Draft)
    assert len(calls) == 1 and "SYNTHETIC-SECRET" not in str(error.value)


@pytest.mark.anyio
async def test_input_limit_prevents_network_call():
    def handler(request):
        pytest.fail("Oversized input must not make a request")

    settings = ModelSettings(base_url="http://localhost/v1", model="test-model")
    async with OpenAICompatibleClient(settings, transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(LLMError, match="input exceeded"):
            await client.generate("Trusted", "x" * 60000, Draft)


@pytest.mark.anyio
@pytest.mark.parametrize("effort", [None, "none", "low", "high"])
async def test_reasoning_effort_only_when_explicit(effort):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json=completion('{"body":"Synthetic draft"}'))

    settings = ModelSettings(base_url="http://localhost/v1", model="test", reasoning_effort=effort)
    async with OpenAICompatibleClient(settings, transport=httpx2.MockTransport(handler)) as client:
        await client.generate("Trusted", "Synthetic", Draft)
    if effort is None:
        assert "reasoning_effort" not in bodies[0]
    else:
        assert bodies[0]["reasoning_effort"] == effort


def test_separate_reasoning_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for prefix in ("OPENAI_", "DISPATCH_JUDGE_"):
        monkeypatch.setenv(prefix + "BASE_URL", "http://localhost/v1")
        monkeypatch.setenv(prefix + "MODEL", "synthetic")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
    monkeypatch.setenv("DISPATCH_JUDGE_REASONING_EFFORT", "")
    assert load_model_settings().reasoning_effort == "none"
    assert load_model_settings(judge=True).reasoning_effort is None
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "unsupported")
    with pytest.raises(ValueError, match="REASONING_EFFORT"):
        load_model_settings()
