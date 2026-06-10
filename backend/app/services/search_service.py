from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.compute.embeddings import DEFAULT_EMBEDDING_MODEL_NAME, EmbeddingBackend, get_default_embedding_backend
from app.compute.vector_index import blob_to_vector, cosine_similarity, normalize_matrix, vector_to_blob
from app.core.parsing import normalize_text
from app.db.models import (
    Dataset,
    DatasetStatus,
    Job,
    Keyword,
    Story,
    StoryKeyword,
    StoryTrope,
    TermEmbedding,
    TermKind,
    TermSimilarityCache,
    Trope,
)


NEAR_DUPLICATE_THRESHOLD = 0.9


@dataclass
class SearchTermRecord:
    id: str
    text: str
    normalized_text: str
    story_count: int


class SearchService:
    def __init__(
        self,
        embedding_backend: EmbeddingBackend | None = None,
        *,
        model_name: str = DEFAULT_EMBEDDING_MODEL_NAME,
        embedding_cache_dir: str | None = None,
        near_duplicate_threshold: float = NEAR_DUPLICATE_THRESHOLD,
    ) -> None:
        self.embedding_backend = embedding_backend or get_default_embedding_backend(
            model_name,
            cache_dir=embedding_cache_dir,
        )
        self.model_name = getattr(self.embedding_backend, "model_name", model_name)
        self.near_duplicate_threshold = near_duplicate_threshold

    def handle_full_rebuild_job(self, session: Session, job: Job) -> dict[str, Any]:
        return self.rebuild_embeddings(session)

    def rebuild_embeddings(self, session: Session) -> dict[str, Any]:
        active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
        dataset_id = active_dataset.id if active_dataset is not None else None
        dataset_version = active_dataset.version if active_dataset is not None else None
        trope_terms = self._load_active_terms(session, TermKind.TROPE)
        keyword_terms = self._load_active_terms(session, TermKind.KEYWORD)
        artifact_version = self._next_artifact_version(session)

        trope_summary = self._rebuild_term_embeddings(session, TermKind.TROPE, trope_terms, artifact_version)
        keyword_summary = self._rebuild_term_embeddings(session, TermKind.KEYWORD, keyword_terms, artifact_version)
        session.flush()
        cache_summary = self._refresh_trope_similarity_cache(session, trope_terms, artifact_version)

        session.flush()
        return {
            "message": "Embedding rebuild completed.",
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "model_name": self.model_name,
            "artifact_version": artifact_version,
            "tropes_indexed": trope_summary["indexed_count"],
            "keywords_indexed": keyword_summary["indexed_count"],
            "near_duplicate_pairs": cache_summary["pair_count"],
        }

    def search_terms(self, session: Session, term_kind: TermKind, query: str, *, limit: int = 10) -> dict[str, Any]:
        active_terms = self._load_active_terms(session, term_kind)
        if not active_terms:
            return {
                "items": [],
                "model_name": self.model_name,
                "artifact_version": None,
            }

        active_term_ids = [term.id for term in active_terms]
        embeddings = list(
            session.scalars(
                select(TermEmbedding)
                .where(
                    TermEmbedding.term_kind == term_kind,
                    TermEmbedding.model_name == self.model_name,
                    or_(
                        TermEmbedding.trope_id.in_(active_term_ids) if term_kind == TermKind.TROPE else False,
                        TermEmbedding.keyword_id.in_(active_term_ids) if term_kind == TermKind.KEYWORD else False,
                    ),
                )
                .order_by(TermEmbedding.artifact_version.desc(), TermEmbedding.id.asc())
            ).all()
        )
        if not embeddings:
            return self._lexical_fallback_result(active_terms, query, limit=limit)

        latest_artifact_version = max(embedding.artifact_version for embedding in embeddings)
        latest_embeddings = [
            embedding for embedding in embeddings if embedding.artifact_version == latest_artifact_version
        ]
        if not latest_embeddings:
            return self._lexical_fallback_result(active_terms, query, limit=limit)

        embedding_by_term_id = {
            (embedding.trope_id if term_kind == TermKind.TROPE else embedding.keyword_id): embedding
            for embedding in latest_embeddings
        }
        ordered_terms = [term for term in active_terms if term.id in embedding_by_term_id]
        if not ordered_terms:
            return self._lexical_fallback_result(active_terms, query, limit=limit)

        matrix = np.vstack(
            [
                blob_to_vector(
                    embedding_by_term_id[term.id].vector_blob,
                    embedding_by_term_id[term.id].vector_dimensions,
                )
                for term in ordered_terms
            ]
        ).astype(np.float32)
        try:
            query_vector = self.embedding_backend.encode_texts([query])
        except Exception:
            return self._lexical_fallback_result(active_terms, query, limit=limit)
        if query_vector.size == 0:
            return self._lexical_fallback_result(active_terms, query, limit=limit)

        scores = cosine_similarity(query_vector[0], matrix)
        top_indices = np.argsort(scores)[::-1][:limit]

        query_marker = normalize_text(query)
        exact_term = next((term for term in ordered_terms if term.normalized_text == query_marker), None)
        cache_map: dict[str, TermSimilarityCache] = {}
        if term_kind == TermKind.TROPE and exact_term is not None:
            cache_entries = session.scalars(
                select(TermSimilarityCache).where(
                    TermSimilarityCache.term_kind == TermKind.TROPE,
                    TermSimilarityCache.model_name == self.model_name,
                    TermSimilarityCache.artifact_version == latest_artifact_version,
                    TermSimilarityCache.source_term_id == exact_term.id,
                )
            ).all()
            cache_map = {entry.target_term_id: entry for entry in cache_entries}

        items = []
        for index in top_indices.tolist():
            term = ordered_terms[index]
            embedding = embedding_by_term_id[term.id]
            cache_entry = cache_map.get(term.id)
            items.append(
                {
                    "id": term.id,
                    "text": term.text,
                    "story_count": term.story_count,
                    "score": float(scores[index]),
                    "explanation": {
                        "method": "cosine_similarity",
                        "model_name": self.model_name,
                        "artifact_version": latest_artifact_version,
                        "vector_dimension": embedding.vector_dimensions,
                        "cache_hit": cache_entry is not None,
                        "matched_query_exactly": exact_term is not None and term.id == exact_term.id,
                        "near_duplicate": cache_entry is not None,
                    },
                }
            )

        return {
            "items": items,
            "model_name": self.model_name,
            "artifact_version": latest_artifact_version,
        }

    def get_trope_pairwise_similarities(
        self,
        session: Session,
        trope_ids: list[str],
        *,
        minimum_score: float = 0.0,
    ) -> dict[tuple[str, str], float]:
        ordered_trope_ids = list(dict.fromkeys(trope_id for trope_id in trope_ids if trope_id))
        if len(ordered_trope_ids) < 2:
            return {}

        artifact_version = session.scalar(
            select(func.max(TermEmbedding.artifact_version)).where(
                TermEmbedding.term_kind == TermKind.TROPE,
                TermEmbedding.model_name == self.model_name,
            )
        )
        if artifact_version is None:
            return {}

        embeddings = list(
            session.scalars(
                select(TermEmbedding).where(
                    TermEmbedding.term_kind == TermKind.TROPE,
                    TermEmbedding.model_name == self.model_name,
                    TermEmbedding.artifact_version == artifact_version,
                    TermEmbedding.trope_id.in_(ordered_trope_ids),
                )
            ).all()
        )
        embedding_by_trope_id = {
            embedding.trope_id: embedding
            for embedding in embeddings
            if embedding.trope_id is not None
        }
        available_trope_ids = [trope_id for trope_id in ordered_trope_ids if trope_id in embedding_by_trope_id]
        if len(available_trope_ids) < 2:
            return {}

        matrix = np.vstack(
            [
                blob_to_vector(
                    embedding_by_trope_id[trope_id].vector_blob,
                    embedding_by_trope_id[trope_id].vector_dimensions,
                )
                for trope_id in available_trope_ids
            ]
        ).astype(np.float32)

        similarities: dict[tuple[str, str], float] = {}
        for index, source_trope_id in enumerate(available_trope_ids):
            scores = cosine_similarity(matrix[index], matrix)
            for target_index in range(index + 1, len(available_trope_ids)):
                score = float(scores[target_index])
                if score < minimum_score:
                    continue
                target_trope_id = available_trope_ids[target_index]
                similarities[(source_trope_id, target_trope_id)] = score

        return similarities

    def _lexical_fallback_result(
        self,
        terms: list[SearchTermRecord],
        query: str,
        *,
        limit: int,
    ) -> dict[str, Any]:
        query_marker = normalize_text(query)
        if not query_marker:
            return {
                "items": [],
                "model_name": self.model_name,
                "artifact_version": None,
            }

        query_tokens = set(query_marker.split())
        items = []
        for term in terms:
            score = self._lexical_score(query_marker, query_tokens, term)
            if score <= 0:
                continue
            items.append(
                {
                    "id": term.id,
                    "text": term.text,
                    "story_count": term.story_count,
                    "score": float(score),
                    "explanation": {
                        "method": "lexical_fallback",
                        "model_name": self.model_name,
                        "artifact_version": 0,
                        "vector_dimension": None,
                        "cache_hit": False,
                        "matched_query_exactly": term.normalized_text == query_marker,
                        "near_duplicate": False,
                    },
                }
            )

        items.sort(
            key=lambda item: (
                -item["score"],
                -item["story_count"],
                item["text"].lower(),
                item["id"],
            )
        )
        return {
            "items": items[:limit],
            "model_name": self.model_name,
            "artifact_version": None,
        }

    def _lexical_score(
        self,
        query_marker: str,
        query_tokens: set[str],
        term: SearchTermRecord,
    ) -> float:
        term_marker = term.normalized_text
        term_tokens = set(term_marker.split())
        if not term_marker:
            return 0.0

        exact_match = term_marker == query_marker
        prefix_match = bool(query_marker) and term_marker.startswith(query_marker)
        contains_match = bool(query_marker) and query_marker in term_marker
        reverse_contains_match = bool(term_marker) and term_marker in query_marker
        token_overlap = len(query_tokens & term_tokens)
        token_coverage = token_overlap / max(len(query_tokens), 1)
        jaccard = token_overlap / max(len(query_tokens | term_tokens), 1)

        score = 0.0
        if exact_match:
            score = max(score, 1.0)
        if prefix_match:
            score = max(score, 0.94)
        if contains_match:
            score = max(score, 0.9)
        if reverse_contains_match:
            score = max(score, 0.82)
        if token_overlap:
            score = max(score, 0.7 * token_coverage + 0.3 * jaccard)

        return round(float(score), 6)

    def _load_active_terms(self, session: Session, term_kind: TermKind) -> list[SearchTermRecord]:
        active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
        if active_dataset is None:
            return []

        if term_kind == TermKind.TROPE:
            rows = session.execute(
                select(
                    Trope.id,
                    Trope.text,
                    Trope.normalized_text,
                    func.count(func.distinct(Story.id)).label("story_count"),
                )
                .select_from(Trope)
                .join(StoryTrope, StoryTrope.trope_id == Trope.id)
                .join(Story, Story.id == StoryTrope.story_id)
                .where(Story.dataset_id == active_dataset.id)
                .group_by(Trope.id, Trope.text, Trope.normalized_text)
                .order_by(Trope.text.asc(), Trope.id.asc())
            ).all()
            return [
                SearchTermRecord(
                    id=row.id,
                    text=row.text,
                    normalized_text=row.normalized_text,
                    story_count=int(row.story_count),
                )
                for row in rows
            ]

        rows = session.execute(
            select(
                Keyword.id,
                Keyword.text,
                Keyword.normalized_text,
                func.count(func.distinct(Story.id)).label("story_count"),
            )
            .select_from(Keyword)
            .join(StoryKeyword, StoryKeyword.keyword_id == Keyword.id)
            .join(Story, Story.id == StoryKeyword.story_id)
            .where(Story.dataset_id == active_dataset.id)
            .group_by(Keyword.id, Keyword.text, Keyword.normalized_text)
            .order_by(Keyword.text.asc(), Keyword.id.asc())
        ).all()
        return [
            SearchTermRecord(
                id=row.id,
                text=row.text,
                normalized_text=row.normalized_text,
                story_count=int(row.story_count),
            )
            for row in rows
        ]

    def _next_artifact_version(self, session: Session) -> int:
        current = session.scalar(
            select(func.max(TermEmbedding.artifact_version)).where(TermEmbedding.model_name == self.model_name)
        )
        return int(current or 0) + 1

    def _rebuild_term_embeddings(
        self,
        session: Session,
        term_kind: TermKind,
        terms: list[SearchTermRecord],
        artifact_version: int,
    ) -> dict[str, int]:
        session.execute(
            delete(TermEmbedding).where(
                TermEmbedding.term_kind == term_kind,
                TermEmbedding.model_name == self.model_name,
            )
        )

        if not terms:
            return {"indexed_count": 0}

        matrix = normalize_matrix(self.embedding_backend.encode_texts([term.text for term in terms]))
        vector_dimension = int(matrix.shape[1]) if matrix.ndim == 2 else 0

        for term, vector in zip(terms, matrix, strict=True):
            session.add(
                TermEmbedding(
                    term_kind=term_kind,
                    trope_id=term.id if term_kind == TermKind.TROPE else None,
                    keyword_id=term.id if term_kind == TermKind.KEYWORD else None,
                    model_name=self.model_name,
                    artifact_version=artifact_version,
                    vector_dimensions=vector_dimension,
                    vector_blob=vector_to_blob(vector),
                )
            )

        return {"indexed_count": len(terms)}

    def _refresh_trope_similarity_cache(
        self,
        session: Session,
        trope_terms: list[SearchTermRecord],
        artifact_version: int,
    ) -> dict[str, int]:
        session.execute(
            delete(TermSimilarityCache).where(
                TermSimilarityCache.term_kind == TermKind.TROPE,
                TermSimilarityCache.model_name == self.model_name,
            )
        )

        if len(trope_terms) < 2:
            return {"pair_count": 0}

        embeddings = list(
            session.scalars(
                select(TermEmbedding).where(
                    TermEmbedding.term_kind == TermKind.TROPE,
                    TermEmbedding.model_name == self.model_name,
                    TermEmbedding.artifact_version == artifact_version,
                )
            ).all()
        )
        embedding_by_trope_id = {embedding.trope_id: embedding for embedding in embeddings if embedding.trope_id is not None}
        ordered_terms = [term for term in trope_terms if term.id in embedding_by_trope_id]
        if len(ordered_terms) < 2:
            return {"pair_count": 0}

        matrix = np.vstack(
            [
                blob_to_vector(
                    embedding_by_trope_id[term.id].vector_blob,
                    embedding_by_trope_id[term.id].vector_dimensions,
                )
                for term in ordered_terms
            ]
        ).astype(np.float32)
        pair_count = 0
        for index, source_term in enumerate(ordered_terms):
            scores = cosine_similarity(matrix[index], matrix)
            for target_index, score in enumerate(scores.tolist()):
                if target_index == index or score < self.near_duplicate_threshold:
                    continue
                target_term = ordered_terms[target_index]
                session.add(
                    TermSimilarityCache(
                        term_kind=TermKind.TROPE,
                        model_name=self.model_name,
                        artifact_version=artifact_version,
                        source_term_id=source_term.id,
                        target_term_id=target_term.id,
                        similarity_score=float(score),
                        metadata_json={
                            "reason": "near_duplicate_trope",
                            "threshold": self.near_duplicate_threshold,
                        },
                    )
                )
                pair_count += 1
        return {"pair_count": pair_count}
