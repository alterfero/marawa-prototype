from __future__ import annotations

import sys
from types import SimpleNamespace

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
