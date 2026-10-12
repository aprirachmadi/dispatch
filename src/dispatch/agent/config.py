"""Explicit, trusted endpoint configuration; no model/provider is selected implicitly."""

import os
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    timeout_seconds: int = Field(default=120, ge=10, le=300)
    max_tokens: int = Field(default=2048, ge=256, le=8192)
    token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    output_mode: Literal["text", "json_object", "json_schema"] = "text"

    @model_validator(mode="after")
    def trusted_endpoint(self):
        try:
            url = urlsplit(self.base_url)
            local = url.hostname in {"localhost", "127.0.0.1", "::1"}
            valid = (
                url.scheme in {"http", "https"}
                and url.hostname
                and (url.port is None or 1 <= url.port <= 65535)
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
                and (url.scheme == "https" or local)
                and not any(character.isspace() for character in self.base_url)
            )
        except ValueError:
            valid = False
            local = False
        if not valid:
            raise ValueError(
                "Use HTTPS, or HTTP on loopback, without URL credentials/query/fragment"
            )
        if not local and not self.api_key.get_secret_value().strip():
            raise ValueError("A remote model endpoint requires an explicit API key")
        if not self.model.strip():
            raise ValueError("A model name is required")
        return self


def load_model_settings(*, judge: bool = False) -> ModelSettings:
    """Load separate agent/judge settings; never silently fall back to another provider."""
    load_dotenv(dotenv_path=".env", override=False)
    prefix = "DISPATCH_JUDGE_" if judge else "OPENAI_"
    values = {
        "base_url": os.getenv(prefix + "BASE_URL", ""),
        "model": os.getenv(prefix + "MODEL", ""),
        "api_key": os.getenv(prefix + "API_KEY", ""),
        "timeout_seconds": os.getenv(prefix + "TIMEOUT_SECONDS", "120"),
        "max_tokens": os.getenv(prefix + "MAX_TOKENS", "2048"),
        "token_parameter": os.getenv(prefix + "TOKEN_PARAMETER", "max_tokens"),
        "output_mode": os.getenv(prefix + "OUTPUT_MODE", "text"),
    }
    try:
        return ModelSettings(**values)
    except ValueError:
        # Do not expose Pydantic's input values: they can include credentials.
        raise ValueError(
            f"Invalid {prefix} configuration. Set BASE_URL and MODEL; check API_KEY, "
            "timeouts, token limit, TOKEN_PARAMETER and OUTPUT_MODE. See .env.example."
        ) from None
