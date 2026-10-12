"""Trusted process configuration; tools and ticket text cannot choose the PSA URL."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from dispatch.settings import parse_read_only


@dataclass(frozen=True)
class MCPSettings:
    psa_base_url: str = "http://127.0.0.1:8000"
    read_only: bool = True
    audit_path: Path = Path("data/audit.jsonl")

    def __post_init__(self) -> None:
        if type(self.read_only) is not bool:
            raise ValueError("read_only must be a boolean")
        try:
            url = urlsplit(self.psa_base_url)
            valid_port = url.port is None or 1 <= url.port <= 65535
            valid = (
                url.scheme in {"http", "https"}
                and url.hostname
                and valid_port
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "DISPATCH_PSA_BASE_URL must be an HTTP(S) URL "
                "without credentials, query, or fragment."
            )


def load_settings() -> MCPSettings:
    """Read the working-directory .env; existing environment variables take precedence."""
    load_dotenv(dotenv_path=".env", override=False)
    return MCPSettings(
        psa_base_url=os.getenv("DISPATCH_PSA_BASE_URL", MCPSettings().psa_base_url),
        read_only=parse_read_only(os.getenv("DISPATCH_READ_ONLY", "true")),
        audit_path=Path(os.getenv("DISPATCH_AUDIT_PATH", "data/audit.jsonl")),
    )
