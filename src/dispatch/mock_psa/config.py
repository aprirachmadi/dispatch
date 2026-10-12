"""Load local configuration without requiring secrets for the mock backend."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from dispatch.settings import parse_read_only


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./data/dispatch.db"
    read_only: bool = True

    def __post_init__(self) -> None:
        if type(self.read_only) is not bool:
            raise ValueError("read_only must be a boolean")


def load_settings() -> Settings:
    """Read .env from the working directory; environment variables take precedence."""
    load_dotenv(dotenv_path=".env", override=False)
    return Settings(
        database_url=os.getenv("DISPATCH_DATABASE_URL", Settings().database_url),
        read_only=parse_read_only(os.getenv("DISPATCH_READ_ONLY", "true")),
    )
