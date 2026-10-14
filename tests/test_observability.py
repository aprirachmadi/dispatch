"""Manual tracing contract tests without contacting Langfuse or model services."""

import json
from contextlib import contextmanager

import httpx2
import pytest

from dispatch import observability
from dispatch.agent.config import ModelSettings
from dispatch.agent.llm import LLMError, OpenAICompatibleClient
from dispatch.agent.models import Draft

ORIGINAL_GET_LANGFUSE = observability.get_langfuse


@pytest.fixture(autouse=True)
def isolated_langfuse(monkeypatch, tmp_path):
    monkeypatch.setattr(observability, "get_langfuse", ORIGINAL_GET_LANGFUSE)
    monkeypatch.chdir(tmp_path)
    for name in ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    ORIGINAL_GET_LANGFUSE.cache_clear()
    yield
    ORIGINAL_GET_LANGFUSE.cache_clear()


def test_disabled_and_partial_configuration(monkeypatch):
    assert observability.get_langfuse() is None
    ORIGINAL_GET_LANGFUSE.cache_clear()
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "SYNTHETIC-SECRET")
    with pytest.raises(ValueError, match="LANGFUSE_PUBLIC_KEY") as error:
        observability.get_langfuse()
    assert "SYNTHETIC-SECRET" not in str(error.value)


def test_bad_url_redacts_credentials(monkeypatch):
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://user:SYNTHETIC-SECRET@localhost:3210")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "synthetic-public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "synthetic-secret")
    with pytest.raises(ValueError, match="Invalid LANGFUSE_BASE_URL") as error:
        observability.get_langfuse()
    assert "SYNTHETIC-SECRET" not in str(error.value)


class Span:
    trace_id = "synthetic-trace"
    id = "synthetic-span"

    def __init__(self):
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


class Client:
    def __init__(self):
        self.calls = []
        self.spans = []

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        self.calls.append(kwargs)
        span = Span()
        self.spans.append(span)
        yield span


@pytest.mark.anyio
@pytest.mark.parametrize(
    "usage, expected",
    [
        (
            {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
            {"input": 20, "output": 5, "total": 25},
        ),
        (
            {
                "prompt_tokens": 729,
                "completion_tokens": 83,
                "total_tokens": 812,
                "prompt_tokens_details": {"cached_tokens": 724},
            },
            {"input": 5, "input_cached_tokens": 724, "output": 83, "total": 812},
        ),
        (
            {
                "prompt_tokens": 20,
                "completion_tokens": 5,
                "total_tokens": 25,
                "prompt_tokens_details": {"cached_tokens": 999},
            },
            {"input": 20, "output": 5, "total": 25},
        ),
        ({"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}, None),
        ({"prompt_tokens": 20, "completion_tokens": None, "total_tokens": None}, {"input": 20}),
        (None, None),
    ],
)
async def test_generation_captures_one_call_final_input_usage_and_actual_model(
    monkeypatch, usage, expected
):
    tracing = Client()
    monkeypatch.setattr(observability, "get_langfuse", lambda: tracing)
    content = json.dumps({"body": "Synthetic draft"})
    response = {
        "id": "synthetic-completion",
        "object": "chat.completion",
        "created": 1,
        "model": "actual-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": usage,
    }
    settings = ModelSettings(base_url="http://localhost/v1", model="requested-model")
    async with OpenAICompatibleClient(
        settings,
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=response)),
    ) as model:
        await model.generate("Instructions", "Synthetic input", Draft)
    assert len(tracing.calls) == 1
    assert tracing.calls[0]["as_type"] == "generation"
    assert "matching this schema" in tracing.calls[0]["input"][0]["content"]
    update = tracing.spans[0].updates[0]
    assert update["model"] == "actual-model"
    assert update["usage_details"] == expected
    assert update["output"]["choices"][0]["message"]["content"] == content


@pytest.mark.anyio
async def test_provider_error_traced_without_response_body(monkeypatch):
    tracing = Client()
    monkeypatch.setattr(observability, "get_langfuse", lambda: tracing)
    settings = ModelSettings(base_url="http://localhost/v1", model="synthetic")
    async with OpenAICompatibleClient(
        settings,
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(500, text="SYNTHETIC-SECRET provider body")
        ),
    ) as model:
        with pytest.raises(LLMError):
            await model.generate("Instructions", "Synthetic input", Draft)
    assert any(update.get("level") == "ERROR" for update in tracing.spans[0].updates)
    assert "SYNTHETIC-SECRET" not in str(tracing.spans[0].updates)
    assert tracing.spans[0].updates[-1]["status_message"] == "InternalServerError"


def test_export_failures_do_not_replace_application_result(monkeypatch):
    class FailingClient:
        def start_as_current_observation(self, **kwargs):
            raise RuntimeError("SYNTHETIC-SECRET")

    monkeypatch.setattr(observability, "get_langfuse", FailingClient)
    with observability.observation("synthetic") as span:
        span.update(output="ok")
    assert span.trace_id is None


def test_exception_context_does_not_receive_raw_error(monkeypatch):
    seen = []

    class Context:
        def __enter__(self):
            return Span()

        def __exit__(self, *args):
            seen.append(args)

    class CustomClient:
        def start_as_current_observation(self, **kwargs):
            return Context()

    monkeypatch.setattr(observability, "get_langfuse", CustomClient)
    with pytest.raises(RuntimeError):
        with observability.observation("synthetic"):
            raise RuntimeError("SYNTHETIC-SECRET")
    assert seen == [(None, None, None)]


def test_real_sdk_groups_nested_calls_and_records_latency_without_sessions(monkeypatch):
    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    client = Langfuse(
        base_url="http://localhost:3210",
        public_key="pk-synthetic-grouping-test",
        secret_key="sk-synthetic-test",
        tracer_provider=TracerProvider(),
        span_exporter=exporter,
    )
    monkeypatch.setattr(observability, "get_langfuse", lambda: client)
    try:
        with observability.observation("request") as root:
            with observability.observation("llm.first", as_type="generation"):
                pass
            with observability.observation("llm.second", as_type="generation"):
                pass
        client.flush()
        spans = exporter.get_finished_spans()
        assert len(spans) == 3
        assert len({span.context.trace_id for span in spans}) == 1
        parent = next(span for span in spans if span.name == "request")
        assert parent.parent is None
        assert root.trace_id == format(parent.context.trace_id, "032x")
        for span in spans:
            assert span.end_time >= span.start_time
            assert not any("session" in key.lower() for key in span.attributes)
            if span.name != "request":
                assert span.parent.span_id == parent.context.span_id
    finally:
        client.shutdown()
