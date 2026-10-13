import hashlib
import json
import math

import pytest

from dispatch.mcp_server.services.common import AnalyticsError
from dispatch.mcp_server.services.embeddings import CachedEmbeddings, normalize_vector


class FakeEmbedder:
    identity = "test-model-v1"
    dimensions = 2

    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return [[len(text), 1.0] for text in texts]


def test_cache_is_text_free_and_reused_across_instances(tmp_path):
    embedder = FakeEmbedder()
    path = tmp_path / "vectors.json"
    cached = CachedEmbeddings(embedder, path)
    texts = ["synthetic SECRET body", "second", "second"]
    vectors = cached.encode(texts)
    assert len(embedder.calls[0]) == 2
    assert vectors[1] == vectors[2]
    assert "SECRET" not in path.read_text()
    assert hashlib.sha256(texts[0].encode()).hexdigest() in path.read_text()
    fresh = FakeEmbedder()
    assert CachedEmbeddings(fresh, path).encode(texts) == vectors
    assert fresh.calls == []
    assert all(math.isclose(sum(value * value for value in vector), 1) for vector in vectors)


def test_changed_text_or_model_never_reuses_old_vectors(tmp_path):
    model = FakeEmbedder()
    path = tmp_path / "vectors.json"
    cached = CachedEmbeddings(model, path)
    cached.encode(["original"])
    cached.encode(["changed"])
    assert model.calls == [["original"], ["changed"]]
    other = FakeEmbedder()
    other.identity = "test-model-v2"
    CachedEmbeddings(other, path).encode(["original"])
    assert other.calls == [["original"]]


def test_returned_vectors_do_not_expose_mutable_cache_state(tmp_path):
    cache = CachedEmbeddings(FakeEmbedder(), tmp_path / "vectors.json")
    original = cache.encode(["text"])[0]
    damaged = cache.encode(["text"])[0]
    damaged[0] = 12345.0
    assert cache.encode(["text"])[0] == original


@pytest.mark.parametrize("vector", [[0, 0], [1], [float("nan"), 1], [float("inf"), 1], [True, 1]])
def test_invalid_vectors_rejected(vector):
    with pytest.raises(AnalyticsError):
        normalize_vector(vector, 2)


@pytest.mark.parametrize("content", ["broken json", "null", '{"schema":1}'])
def test_corrupt_cache_fails_with_safe_error(tmp_path, content):
    path = tmp_path / "vectors.json"
    path.write_text(content)
    with pytest.raises(AnalyticsError, match="cache is invalid"):
        CachedEmbeddings(FakeEmbedder(), path).encode(["text"])


def test_atomic_save_failure_preserves_previous_cache(tmp_path, monkeypatch):
    from dispatch.mcp_server.services import embeddings

    path = tmp_path / "vectors.json"
    cache = CachedEmbeddings(FakeEmbedder(), path)
    cache.encode(["original"])
    before = path.read_bytes()

    def fail_replace(*args):
        raise OSError("SECRET path error")

    monkeypatch.setattr(embeddings.os, "replace", fail_replace)
    with pytest.raises(AnalyticsError, match="Cannot save") as error:
        cache.encode(["changed"])
    assert "SECRET" not in str(error.value)
    assert path.read_bytes() == before
    assert [file.name for file in tmp_path.iterdir()] == ["vectors.json"]


def test_cache_entry_limit_is_enforced(tmp_path, monkeypatch):
    from dispatch.mcp_server.services import embeddings

    monkeypatch.setattr(embeddings, "MAX_CACHE_ENTRIES", 2)
    path = tmp_path / "vectors.json"
    cache = CachedEmbeddings(FakeEmbedder(), path)
    cache.encode(["a", "b"])
    cache.encode(["c"])
    assert len(json.loads(path.read_text())["entries"]) == 2


@pytest.mark.parametrize("texts", [[""], ["x" * 10203], ["a"] * 2002])
def test_embedding_input_bounds(texts, tmp_path):
    with pytest.raises(AnalyticsError):
        CachedEmbeddings(FakeEmbedder(), tmp_path / "vectors.json").encode(texts)
