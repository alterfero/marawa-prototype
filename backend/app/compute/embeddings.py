from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Protocol

import numpy as np


DEFAULT_EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"

# Transformers temporarily patches torch's parameter registration while loading
# weights. Overlapping constructors can leave another model's parameters on meta,
# even when the constructors belong to different backend instances.
_MODEL_LOAD_LOCK = Lock()


class EmbeddingBackend(Protocol):
    model_name: str

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        ...


class SentenceTransformerBackend:
    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL_NAME, *, cache_dir: str | None = None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None

    def _load_model(self):
        with _MODEL_LOAD_LOCK:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                cache_dir = None
                if self.cache_dir:
                    Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
                    cache_dir = self.cache_dir

                # The pinned Transformers version honors low_cpu_mem_usage=False;
                # 4.51+ ignores it. Keep initialization serialized as well because
                # Transformers also changes global weight-initialization hooks.
                self._model = SentenceTransformer(
                    self.model_name,
                    cache_folder=cache_dir,
                    device="cpu",
                    model_kwargs={
                        "device_map": None,
                        "low_cpu_mem_usage": False,
                    },
                )
        return self._model

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        model = self._load_model()
        matrix = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(matrix, dtype=np.float32)


@lru_cache
def get_default_embedding_backend(
    model_name: str = DEFAULT_EMBEDDING_MODEL_NAME,
    cache_dir: str | None = None,
) -> SentenceTransformerBackend:
    return SentenceTransformerBackend(model_name=model_name, cache_dir=cache_dir)
