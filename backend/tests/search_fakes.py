import numpy as np

from app.core.parsing import normalize_text


class FakeEmbeddingBackend:
    model_name = "fake-sentence-transformers/paraphrase-multilingual-mpnet-base-v2"

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        vectors = [self._vector_for_text(text) for text in texts]
        if not vectors:
            return np.zeros((0, 0), dtype=np.float32)
        matrix = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        return matrix / norms

    def _vector_for_text(self, text: str) -> np.ndarray:
        marker = normalize_text(text)
        if marker == "first trope":
            return np.array([1.0, 0.0, 0.0], dtype=np.float32)
        if marker == "first trope variant":
            return np.array([0.99, 0.11, 0.0], dtype=np.float32)
        if marker == "second trope":
            return np.array([0.0, 1.0, 0.0], dtype=np.float32)
        if marker == "third trope":
            return np.array([0.0, 0.0, 1.0], dtype=np.float32)
        if marker == "wolf":
            return np.array([1.0, 0.0, 0.0], dtype=np.float32)
        if marker == "moon":
            return np.array([0.96, 0.04, 0.0], dtype=np.float32)
        if marker == "river":
            return np.array([0.0, 1.0, 0.0], dtype=np.float32)
        if marker == "sea":
            return np.array([0.0, 0.95, 0.05], dtype=np.float32)
        if "first" in marker:
            return np.array([1.0, 0.0, 0.0], dtype=np.float32)
        if "wolf" in marker:
            return np.array([1.0, 0.0, 0.0], dtype=np.float32)
        if "river" in marker or "sea" in marker:
            return np.array([0.0, 1.0, 0.0], dtype=np.float32)
        return np.array([0.0, 0.0, 1.0], dtype=np.float32)
