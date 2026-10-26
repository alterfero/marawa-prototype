from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from app.compute.embeddings import SentenceTransformerBackend


def test_sentence_transformer_backend_disables_meta_device_loading(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeSentenceTransformer:
        def __init__(self, model_name: str, **kwargs: object) -> None:
            captured["model_name"] = model_name
            captured.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    cache_dir = tmp_path / "model-cache"

    backend = SentenceTransformerBackend("test-model", cache_dir=str(cache_dir))

    model = backend._load_model()

    assert isinstance(model, FakeSentenceTransformer)
    assert cache_dir.is_dir()
    assert captured == {
        "model_name": "test-model",
        "cache_folder": str(cache_dir),
        "device": "cpu",
        "model_kwargs": {
            "device_map": None,
            "low_cpu_mem_usage": False,
        },
    }


@pytest.mark.parametrize("shared_backend", [True, False])
def test_model_initialization_is_serialized(monkeypatch, shared_backend) -> None:
    first_loading = Event()
    overlapping_load = Event()
    release_first = Event()
    start = Barrier(3)
    state_lock = Lock()
    calls = 0

    class FakeSentenceTransformer:
        def __init__(self, *args, **kwargs) -> None:
            nonlocal calls
            with state_lock:
                calls += 1
                first_call = calls == 1
            if first_call:
                first_loading.set()
                assert release_first.wait(timeout=5)
            else:
                overlapping_load.set()

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    first = SentenceTransformerBackend("first-model")
    second = first if shared_backend else SentenceTransformerBackend("second-model")

    def load(backend):
        start.wait(timeout=5)
        return backend._load_model()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(load, backend) for backend in (first, second)]
        start.wait(timeout=5)
        try:
            assert first_loading.wait(timeout=5)
            assert not overlapping_load.wait(timeout=0.2)
        finally:
            release_first.set()
        models = [future.result(timeout=5) for future in futures]

    assert calls == (1 if shared_backend else 2)
    assert (models[0] is models[1]) == shared_backend


def test_failed_model_load_can_be_retried(monkeypatch) -> None:
    calls = 0

    class FakeSentenceTransformer:
        def __init__(self, *args, **kwargs) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("Model download failed")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    backend = SentenceTransformerBackend("test-model")
    with pytest.raises(RuntimeError, match="Model download failed"):
        backend._load_model()
    assert backend._model is None
    model = backend._load_model()
    assert backend._load_model() is model
    assert calls == 2


@pytest.mark.skipif(
    os.environ.get("MARAWA_TEST_REAL_EMBEDDINGS") != "1",
    reason="Set MARAWA_TEST_REAL_EMBEDDINGS=1 to load the real embedding model.",
)
def test_real_model_loads_without_meta_initialization() -> None:
    backend = SentenceTransformerBackend()
    start = Barrier(2)

    def encode():
        start.wait(timeout=30)
        return backend.encode_texts(["A hero crosses the sea.", "Un héros traverse la mer."])

    # Check the library behavior, not just the kwargs our wrapper supplies.
    # Transformers 4.51+ ignores low_cpu_mem_usage=False and fails this check.
    with patch(
        "transformers.modeling_utils.init_empty_weights",
        side_effect=AssertionError("CPU model loading must not enter a meta-device context"),
        create=True,
    ):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(encode) for _ in range(2)]
            matrices = [future.result(timeout=180) for future in futures]

    assert matrices[0].shape == (2, 768)
    assert np.isfinite(matrices[0]).all()
    np.testing.assert_allclose(np.linalg.norm(matrices[0], axis=1), 1, atol=1e-6)
    np.testing.assert_allclose(matrices[0], matrices[1], atol=1e-6)
