"""Load local configuration without requiring secrets for the mock backend."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./data/dispatch.db"


def load_settings() -> Settings:
    """Read .env from the working directory; environment variables take precedence."""
    load_dotenv(dotenv_path=".env", override=False)
    return Settings(database_url=os.getenv("DISPATCH_DATABASE_URL", Settings().database_url))
