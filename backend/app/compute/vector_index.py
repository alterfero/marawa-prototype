from __future__ import annotations

import numpy as np


def normalize_matrix(matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return matrix.astype(np.float32, copy=False)
    normalized = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(normalized, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return normalized / norms


def vector_to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def blob_to_vector(blob: bytes | None, dimension: int | None) -> np.ndarray:
    if not blob or not dimension:
        return np.zeros((0,), dtype=np.float32)
    vector = np.frombuffer(blob, dtype=np.float32)
    if vector.size != dimension:
        raise ValueError("Stored vector size does not match its declared dimension.")
    return vector


def cosine_similarity(query_vector: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return np.zeros((0,), dtype=np.float32)
    query = np.asarray(query_vector, dtype=np.float32)
    if query.ndim != 1:
        raise ValueError("Query vector must be one-dimensional.")
    return np.asarray(matrix, dtype=np.float32) @ query
