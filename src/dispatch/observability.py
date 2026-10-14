"""Optional manual Langfuse instrumentation; no sessions or automatic SDK hooks."""

import logging
import os
from contextlib import contextmanager
from functools import lru_cache
from urllib.parse import urlsplit

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_langfuse():
    """Use project API credentials, or disable tracing when no configuration is present."""
    load_dotenv(dotenv_path=".env", override=False)
    names = ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    values = [os.getenv(name, "").strip() for name in names]
    if not any(values):
        return None
    if not all(values):
        raise ValueError(
            "Langfuse requires LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY and "
            "LANGFUSE_SECRET_KEY project API credentials. See .env.example."
        )
    try:
        url = urlsplit(values[0])
        valid = (
            url.scheme in {"http", "https"}
            and url.hostname
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and not any(c.isspace() for c in values[0])
        )
        if not valid or (url.port is not None and not 1 <= url.port <= 65535):
            raise ValueError
    except ValueError:
        raise ValueError(
            "Invalid LANGFUSE_BASE_URL; use an HTTP(S) URL without credentials, query or fragment."
        ) from None
    from langfuse import Langfuse

    return Langfuse(base_url=values[0], public_key=values[1], secret_key=values[2])


class Observation:
    """Keep exporter failures from changing application behavior."""

    def __init__(self, span=None):
        self.span = span
        self.error_recorded = False

    @property
    def trace_id(self):
        return self.span.trace_id if self.span is not None else None

    @property
    def id(self):
        return self.span.id if self.span is not None else None

    def update(self, **kwargs):
        if kwargs.get("level") == "ERROR":
            self.error_recorded = True
        if self.span is not None:
            try:
                self.span.update(**kwargs)
            except Exception:
                logger.warning("Langfuse observation update failed.")


@contextmanager
def observation(name: str, *, as_type: str = "span", **kwargs):
    """Nest in the current request/experiment context and capture only safe error types."""
    client = get_langfuse()
    context = None
    span = Observation()
    if client is not None:
        try:
            context = client.start_as_current_observation(name=name, as_type=as_type, **kwargs)
            span = Observation(context.__enter__())
        except Exception:
            context = None
            logger.warning("Langfuse observation creation failed.")
    try:
        yield span
    except BaseException as error:
        # Provider exception strings may contain credentials, endpoint URLs or response bodies.
        if not span.error_recorded:
            span.update(level="ERROR", status_message=type(error).__name__)
        raise
    finally:
        if context is not None:
            try:
                context.__exit__(None, None, None)
            except Exception:
                logger.warning("Langfuse observation export failed.")


def flush_langfuse():
    """Flush pending telemetry before short-lived CLI connections finish."""
    client = get_langfuse()
    if client is not None:
        try:
            client.flush()
        except Exception:
            logger.warning("Langfuse flush failed.")
