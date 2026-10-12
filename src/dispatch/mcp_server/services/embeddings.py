"""Pinned CPU embeddings and an atomic, text-free vector cache; no generative AI."""

import hashlib
import json
import math
import os
import tempfile
import threading
from pathlib import Path
from typing import Protocol

from dispatch.mcp_server.services.common import AnalyticsError

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_REPO = "Qdrant/bge-small-en-v1.5-onnx-Q"
MODEL_REVISION = "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
MODEL_IDENTITY = f"{MODEL_REPO}@{MODEL_REVISION}:fastembed-0.9:problem-v1"
MODEL_DIMENSIONS = 384
MAX_CACHE_ENTRIES = 4096
MAX_CACHE_BYTES = 64 * 1024 * 1024


class Embedder(Protocol):
    identity: str
    dimensions: int

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Return one fixed-dimension vector per text, with no external text transmission."""
        ...


class FastEmbedder:
    """Lazy-loaded pinned ONNX model: public artifact download only, local CPU inference."""

    identity = MODEL_IDENTITY
    dimensions = MODEL_DIMENSIONS

    def __init__(self, model_cache: Path):
        self.model_cache = model_cache
        self._model = None

    def encode(self, texts: list[str]) -> list[list[float]]:
        try:
            if self._model is None:
                from fastembed import TextEmbedding
                from huggingface_hub import snapshot_download
                from huggingface_hub.errors import LocalEntryNotFoundError

                arguments = dict(
                    repo_id=MODEL_REPO,
                    revision=MODEL_REVISION,
                    cache_dir=self.model_cache,
                    token=False,
                    endpoint="https://huggingface.co",
                    allow_patterns=["*.json", "vocab.txt", "model_optimized.onnx"],
                )
                try:
                    snapshot = snapshot_download(**arguments, local_files_only=True)
                except LocalEntryNotFoundError:
                    snapshot = snapshot_download(**arguments)
                self._model = TextEmbedding(
                    model_name=MODEL_NAME,
                    specific_model_path=snapshot,
                    local_files_only=True,
                    providers=["CPUExecutionProvider"],
                    threads=2,
                )
            return [
                [float(value) for value in vector]
                for vector in self._model.embed(texts, batch_size=32)
            ]
        except Exception:
            raise AnalyticsError(
                "Local embedding model unavailable; check the pinned model cache/network. "
                "No keyword-search fallback was used."
            ) from None


def normalize_vector(vector: list[float], dimensions: int) -> list[float]:
    if len(vector) != dimensions or any(
        type(value) not in (int, float) or abs(value) > 1e100 or not math.isfinite(value)
        for value in vector
    ):
        raise AnalyticsError("Embedding vector has invalid dimensions or non-finite values.")
    norm = math.sqrt(math.fsum(value * value for value in vector))
    if not math.isfinite(norm) or norm == 0:
        raise AnalyticsError("Embedding vector has an invalid zero/non-finite norm.")
    return [value / norm for value in vector]


class CachedEmbeddings:
    """Cache by immutable model identity and exact text hash, with bounded JSON storage.

    A shared lock serializes inference/cache writes within one server. Atomic replace
    avoids partial JSON; concurrent processes may lose cache hits, never PSA records.
    """

    def __init__(self, embedder: Embedder, path: Path):
        self.embedder = embedder
        self.path = path
        self._lock = threading.Lock()
        self._entries: dict[str, list[float]] | None = None

    @property
    def identity(self) -> str:
        return self.embedder.identity

    def _load(self) -> dict[str, list[float]]:
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > MAX_CACHE_BYTES:
                raise ValueError("Oversized cache")
            with self.path.open() as file:
                data = json.load(file)
            if data["schema"] != 1 or data["model"] != self.identity:
                return {}  # Another model's vectors must never be reused.
            if data["dimensions"] != self.embedder.dimensions:
                raise ValueError("Cache dimension mismatch")
            entries = data["entries"]
            if not isinstance(entries, dict) or len(entries) > MAX_CACHE_ENTRIES:
                raise ValueError("Invalid cache entries")
            for key, vector in entries.items():
                if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
                    raise ValueError("Invalid text hash")
                normalize_vector(vector, self.embedder.dimensions)
                if not math.isclose(
                    math.fsum(value * value for value in vector), 1.0, abs_tol=1e-6
                ):
                    raise ValueError("Cached vector is not normalized")
            return entries
        except (OSError, ValueError, TypeError, KeyError):
            raise AnalyticsError(
                "Embedding cache is invalid/unreadable; remove the generated cache."
            ) from None

    def _save(self, entries: dict[str, list[float]]) -> None:
        data = {
            "schema": 1,
            "model": self.identity,
            "dimensions": self.embedder.dimensions,
            "entries": entries,
        }
        payload = json.dumps(data, separators=(",", ":"), allow_nan=False).encode()
        if len(payload) > MAX_CACHE_BYTES:
            raise AnalyticsError("Embedding cache exceeded its 64 MiB limit.")
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as file:
                temporary = file.name
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise AnalyticsError(
                "Cannot save embedding cache; check its directory/permissions."
            ) from None
        finally:
            if temporary is not None and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass  # Keep the original safe error if cleanup itself is unavailable.

    def encode(self, texts: list[str]) -> list[list[float]]:
        if len(texts) > 2001:
            raise AnalyticsError("Embedding batch exceeds 2001 texts.")
        if any(not isinstance(text, str) or not text or len(text) > 10_202 for text in texts):
            raise AnalyticsError("Embedding input must be bounded, nonempty ticket text.")
        with self._lock:
            if self._entries is None:
                self._entries = self._load()
            entries = dict(self._entries)
            keys = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
            missing = {
                key: text for key, text in zip(keys, texts, strict=True) if key not in entries
            }
            if missing:
                vectors = self.embedder.encode(list(missing.values()))
                if len(vectors) != len(missing):
                    raise AnalyticsError("Embedding model returned the wrong number of vectors.")
                for key, vector in zip(missing, vectors, strict=True):
                    entries[key] = normalize_vector(vector, self.embedder.dimensions)
            result = [list(entries[key]) for key in keys]  # Callers cannot mutate cached vectors.
            for key in keys:
                entries[key] = entries.pop(key)  # Retain most recently used texts in memory.
            while len(entries) > MAX_CACHE_ENTRIES:
                del entries[next(iter(entries))]
            if missing:
                self._save(entries)
            self._entries = entries
            return result
