from sqlalchemy import select

from app.db import (
    Dataset,
    DatasetStatus,
    Keyword,
    Story,
    StoryKeyword,
    StoryTrope,
    TermEmbedding,
    TermKind,
    TermSimilarityCache,
    Trope,
    build_engine,
    build_session_factory,
    initialize_database,
)
from app.services.dataset import upload_dataset_csv
from app.services.search_service import SearchService
from tests.search_fakes import FakeEmbeddingBackend


def make_csv_bytes(rows: list[dict[str, str]], csv_columns: list[str]) -> bytes:
    import csv
    import io

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=csv_columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def test_rebuild_job_computes_term_embeddings_and_trope_similarity_cache(tmp_path) -> None:
    from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD

    db_path = tmp_path / "search-service.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    search_service = SearchService(embedding_backend=FakeEmbeddingBackend())

    row_one = {column: "" for column in CSV_COLUMNS}
    row_one["Story title (Eng)"] = "Story One"
    row_one[TROPE_FIELD] = "§§ first trope\n§§ first trope variant"
    row_one[KEYWORD_FIELD] = "wolf ; moon"

    row_two = {column: "" for column in CSV_COLUMNS}
    row_two["Story title (Eng)"] = "Story Two"
    row_two[TROPE_FIELD] = "§§ second trope\n§§ third trope"
    row_two[KEYWORD_FIELD] = "river ; sea"

    with session_factory() as session:
        dataset, _ = upload_dataset_csv(
            session,
            make_csv_bytes([row_one, row_two], CSV_COLUMNS),
            source_filename="search.csv",
        )
        dataset.status = DatasetStatus.ACTIVE
        result = search_service.rebuild_embeddings(session)
        session.commit()

        trope_embeddings = session.scalars(
            select(TermEmbedding)
            .where(TermEmbedding.term_kind == TermKind.TROPE)
            .order_by(TermEmbedding.trope_id)
        ).all()
        keyword_embeddings = session.scalars(
            select(TermEmbedding)
            .where(TermEmbedding.term_kind == TermKind.KEYWORD)
            .order_by(TermEmbedding.keyword_id)
        ).all()
        similarity_cache = session.scalars(
            select(TermSimilarityCache)
            .where(TermSimilarityCache.term_kind == TermKind.TROPE)
            .order_by(TermSimilarityCache.source_term_id, TermSimilarityCache.target_term_id)
        ).all()

    assert dataset.id is not None
    assert result["model_name"] == FakeEmbeddingBackend.model_name
    assert result["artifact_version"] == 1
    assert result["tropes_indexed"] == 4
    assert result["keywords_indexed"] == 4
    assert len(trope_embeddings) == 4
    assert len(keyword_embeddings) == 4
    assert all(embedding.vector_dimensions == 3 for embedding in trope_embeddings + keyword_embeddings)
    assert all(embedding.artifact_version == 1 for embedding in trope_embeddings + keyword_embeddings)
    assert len(similarity_cache) == 2
    assert {entry.similarity_score > 0.9 for entry in similarity_cache} == {True}


def test_search_service_returns_similar_terms_with_explanation(tmp_path) -> None:
    from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD

    db_path = tmp_path / "search-query.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    search_service = SearchService(embedding_backend=FakeEmbeddingBackend())

    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = "Story"
    row[TROPE_FIELD] = "§§ first trope\n§§ first trope variant\n§§ second trope"
    row[KEYWORD_FIELD] = "wolf ; moon ; river"

    with session_factory() as session:
        dataset, _ = upload_dataset_csv(session, make_csv_bytes([row], CSV_COLUMNS), source_filename="terms.csv")
        dataset.status = DatasetStatus.ACTIVE
        search_service.rebuild_embeddings(session)
        session.commit()

        trope_results = search_service.search_terms(session, TermKind.TROPE, "first trope", limit=3)
        keyword_results = search_service.search_terms(session, TermKind.KEYWORD, "wolf", limit=3)

    assert trope_results["artifact_version"] == 1
    assert trope_results["items"][0]["text"] == "first trope"
    assert trope_results["items"][0]["explanation"]["matched_query_exactly"] is True
    assert trope_results["items"][1]["text"] == "first trope variant"
    assert trope_results["items"][1]["explanation"]["cache_hit"] is True
    assert trope_results["items"][1]["explanation"]["near_duplicate"] is True

    assert keyword_results["items"][0]["text"] == "wolf"
    assert keyword_results["items"][1]["text"] == "moon"
    assert keyword_results["items"][0]["explanation"]["model_name"] == FakeEmbeddingBackend.model_name


def test_search_service_falls_back_to_lexical_matches_when_embeddings_are_missing(tmp_path) -> None:
    from app.core.csv_schema import CSV_COLUMNS, TROPE_FIELD

    db_path = tmp_path / "search-fallback.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    search_service = SearchService(embedding_backend=FakeEmbeddingBackend())

    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = "Story"
    row[TROPE_FIELD] = "§§ first trope\n§§ first trope variant\n§§ second trope"

    with session_factory() as session:
        dataset, _ = upload_dataset_csv(session, make_csv_bytes([row], CSV_COLUMNS), source_filename="fallback.csv")
        dataset.status = DatasetStatus.ACTIVE
        session.commit()

        trope_results = search_service.search_terms(session, TermKind.TROPE, "first trope", limit=3)

    assert trope_results["artifact_version"] is None
    assert trope_results["items"][0]["text"] == "first trope"
    assert trope_results["items"][0]["explanation"]["method"] == "lexical_fallback"
    assert trope_results["items"][0]["explanation"]["matched_query_exactly"] is True
    assert trope_results["items"][1]["text"] == "first trope variant"
