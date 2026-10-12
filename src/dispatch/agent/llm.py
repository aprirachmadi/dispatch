"""Minimal Chat Completions client; no tool execution, streaming, or hidden retries."""

import asyncio
import json
from typing import Protocol, TypeVar

import httpx2
from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from dispatch.agent.config import ModelSettings

T = TypeVar("T", bound=BaseModel)
MAX_PROMPT_CHARACTERS = 60_000
MAX_RESPONSE_CHARACTERS = 65_536


class LLMError(Exception):
    """Safe model failure, without provider bodies, prompts, credentials, or URLs."""


class ModelClient(Protocol):
    async def generate(self, system: str, user: str, schema: type[T]) -> T: ...


def parse_output[T: BaseModel](text: str, schema: type[T]) -> T:
    """Require one JSON object; reject duplicate keys, nonfinite numbers, and fences."""

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Nonfinite JSON number")

    try:
        if len(text) > MAX_RESPONSE_CHARACTERS:
            raise ValueError("Response too large")
        parsed = json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid_constant)
        if not isinstance(parsed, dict):
            raise ValueError("Expected an object")
        # JSON validation accepts enum strings but still enforces strict booleans/extra fields.
        return schema.model_validate_json(json.dumps(parsed))
    except (ValueError, ValidationError, RecursionError):
        raise LLMError("Model output was not valid for the required JSON schema.") from None


class OpenAICompatibleClient:
    def __init__(self, settings: ModelSettings, *, transport=None):
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.api_key.get_secret_value() or "local-demo-no-auth",
            base_url=settings.base_url.rstrip("/") + "/",
            max_retries=0,
            timeout=settings.timeout_seconds,
            http_client=httpx2.AsyncClient(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                timeout=settings.timeout_seconds,
            ),
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.client.close()

    async def generate(self, system: str, user: str, schema: type[T]) -> T:
        output_schema = schema.model_json_schema()
        system += "\nReturn ONLY one JSON object matching this schema:\n" + json.dumps(
            output_schema
        )
        if len(system) + len(user) > MAX_PROMPT_CHARACTERS:
            raise LLMError("Model input exceeded the configured application limit.")
        options = {self.settings.token_parameter: self.settings.max_tokens}
        if self.settings.reasoning_effort is not None:
            options["reasoning_effort"] = self.settings.reasoning_effort
        if self.settings.output_mode == "json_object":
            options["response_format"] = {"type": "json_object"}
        elif self.settings.output_mode == "json_schema":
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "strict": True, "schema": output_schema},
            }
        try:
            async with asyncio.timeout(self.settings.timeout_seconds):
                response = await self.client.chat.completions.create(
                    model=self.settings.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    **options,
                )
            if (
                not isinstance(getattr(response, "choices", None), list)
                or len(response.choices) != 1
            ):
                raise LLMError("Model returned an unexpected number of choices.")
            choice = response.choices[0]
            if (
                getattr(choice, "finish_reason", None) != "stop"
                or getattr(choice, "message", None) is None
                or choice.message.refusal
                or choice.message.tool_calls
                or choice.message.function_call
                or not isinstance(choice.message.content, str)
            ):
                raise LLMError("Model refused, truncated, or returned an unsupported response.")
            return parse_output(choice.message.content, schema)
        except (OpenAIError, httpx2.HTTPError, TimeoutError):
            raise LLMError(
                "Model request failed; check endpoint, model, credentials and timeout."
            ) from None
