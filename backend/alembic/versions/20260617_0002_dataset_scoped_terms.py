"""dataset-scoped canonical terms

Revision ID: 20260617_0002
Revises: 20260608_0001
Create Date: 2026-06-17 11:30:00
"""

from __future__ import annotations

from collections import defaultdict
import uuid

from alembic import op
import sqlalchemy as sa


revision = "20260617_0002"
down_revision = "20260608_0001"
branch_labels = None
depends_on = None


story_trope_origin_enum = sa.Enum(
    "csv_import",
    "semantic_suggestion",
    "human_entered",
    "human_approved",
    "merge",
    name="storytropeorigin",
    native_enum=False,
)
assignment_status_enum = sa.Enum("pending", "validated", name="assignmentstatus", native_enum=False)
term_kind_enum = sa.Enum("trope", "keyword", name="termkind", native_enum=False)


def _create_scoped_term_tables() -> tuple[sa.Table, sa.Table, sa.Table, sa.Table]:
    tropes = op.create_table(
        "tropes_scoped",
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.String(length=512), nullable=False),
        sa.Column("cached_story_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    keywords = op.create_table(
        "keywords_scoped",
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.String(length=512), nullable=False),
        sa.Column("cached_story_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    story_tropes = op.create_table(
        "story_tropes_scoped",
        sa.Column("story_id", sa.String(length=36), nullable=False),
        sa.Column("trope_id", sa.String(length=36), nullable=False),
        sa.Column("origin", story_trope_origin_enum, nullable=False, server_default="csv_import"),
        sa.Column("status", assignment_status_enum, nullable=False, server_default="pending"),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trope_id"], ["tropes_scoped.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("story_id", "trope_id"),
        sa.UniqueConstraint("story_id", "trope_id", name="uq_story_tropes_scoped_story_id_trope_id"),
    )
    story_keywords = op.create_table(
        "story_keywords_scoped",
        sa.Column("story_id", sa.String(length=36), nullable=False),
        sa.Column("keyword_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["keyword_id"], ["keywords_scoped.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("story_id", "keyword_id"),
        sa.UniqueConstraint("story_id", "keyword_id", name="uq_story_keywords_scoped_story_id_keyword_id"),
    )
    return tropes, keywords, story_tropes, story_keywords


def _create_global_term_tables() -> tuple[sa.Table, sa.Table, sa.Table, sa.Table]:
    tropes = op.create_table(
        "tropes_global",
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.String(length=512), nullable=False),
        sa.Column("cached_story_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    keywords = op.create_table(
        "keywords_global",
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.String(length=512), nullable=False),
        sa.Column("cached_story_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    story_tropes = op.create_table(
        "story_tropes_global",
        sa.Column("story_id", sa.String(length=36), nullable=False),
        sa.Column("trope_id", sa.String(length=36), nullable=False),
        sa.Column("origin", story_trope_origin_enum, nullable=False, server_default="csv_import"),
        sa.Column("status", assignment_status_enum, nullable=False, server_default="pending"),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trope_id"], ["tropes_global.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("story_id", "trope_id"),
        sa.UniqueConstraint("story_id", "trope_id", name="uq_story_tropes_global_story_id_trope_id"),
    )
    story_keywords = op.create_table(
        "story_keywords_global",
        sa.Column("story_id", sa.String(length=36), nullable=False),
        sa.Column("keyword_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["keyword_id"], ["keywords_global.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("story_id", "keyword_id"),
        sa.UniqueConstraint("story_id", "keyword_id", name="uq_story_keywords_global_story_id_keyword_id"),
    )
    return tropes, keywords, story_tropes, story_keywords


def _recreate_term_artifact_tables() -> None:
    op.create_table(
        "term_embeddings",
        sa.Column("term_kind", term_kind_enum, nullable=False),
        sa.Column("trope_id", sa.String(length=36), nullable=True),
        sa.Column("keyword_id", sa.String(length=36), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("vector_dimensions", sa.Integer(), nullable=True),
        sa.Column("vector_blob", sa.LargeBinary(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(trope_id IS NOT NULL AND keyword_id IS NULL) OR (trope_id IS NULL AND keyword_id IS NOT NULL)",
            name="ck_term_embeddings_exactly_one_term",
        ),
        sa.ForeignKeyConstraint(["keyword_id"], ["keywords.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trope_id"], ["tropes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_term_embeddings_artifact_version"), "term_embeddings", ["artifact_version"], unique=False)
    op.create_index(op.f("ix_term_embeddings_term_kind"), "term_embeddings", ["term_kind"], unique=False)
    op.create_index("uq_term_embeddings_trope_model", "term_embeddings", ["trope_id", "model_name"], unique=True)
    op.create_index("uq_term_embeddings_keyword_model", "term_embeddings", ["keyword_id", "model_name"], unique=True)

    op.create_table(
        "term_similarity_cache",
        sa.Column("term_kind", term_kind_enum, nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_term_id", sa.String(length=36), nullable=False),
        sa.Column("target_term_id", sa.String(length=36), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_term_id <> target_term_id", name="ck_term_similarity_cache_distinct_pair"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "term_kind",
            "model_name",
            "source_term_id",
            "target_term_id",
            name="uq_term_similarity_cache_lookup",
        ),
    )
    op.create_index(
        op.f("ix_term_similarity_cache_artifact_version"),
        "term_similarity_cache",
        ["artifact_version"],
        unique=False,
    )
    op.create_index(op.f("ix_term_similarity_cache_term_kind"), "term_similarity_cache", ["term_kind"], unique=False)


def _drop_term_artifact_tables() -> None:
    op.drop_table("term_similarity_cache")
    op.drop_table("term_embeddings")


def _fallback_dataset_id(bind: sa.Connection, datasets: sa.Table) -> str | None:
    active_dataset_id = bind.execute(
        sa.select(datasets.c.id).where(datasets.c.status == "active").limit(1)
    ).scalar_one_or_none()
    if active_dataset_id is not None:
        return active_dataset_id
    return bind.execute(
        sa.select(datasets.c.id).order_by(datasets.c.created_at.desc(), datasets.c.id.desc()).limit(1)
    ).scalar_one_or_none()


def _populate_scoped_terms(
    bind: sa.Connection,
    *,
    datasets: sa.Table,
    stories: sa.Table,
    old_tropes: sa.Table,
    old_keywords: sa.Table,
    old_story_tropes: sa.Table,
    old_story_keywords: sa.Table,
    new_tropes: sa.Table,
    new_keywords: sa.Table,
    new_story_tropes: sa.Table,
    new_story_keywords: sa.Table,
) -> None:
    fallback_dataset_id = _fallback_dataset_id(bind, datasets)

    trope_ids_by_dataset_term: dict[tuple[str, str], str] = {}
    trope_story_counts: defaultdict[str, int] = defaultdict(int)
    trope_rows = bind.execute(
        sa.select(
            stories.c.dataset_id.label("dataset_id"),
            old_tropes.c.id.label("old_term_id"),
            old_tropes.c.text,
            old_tropes.c.normalized_text,
            old_tropes.c.created_at.label("term_created_at"),
            old_tropes.c.updated_at.label("term_updated_at"),
            old_story_tropes.c.story_id,
            old_story_tropes.c.origin,
            old_story_tropes.c.status,
            old_story_tropes.c.position,
            old_story_tropes.c.created_at.label("link_created_at"),
            old_story_tropes.c.updated_at.label("link_updated_at"),
        )
        .select_from(
            old_story_tropes.join(stories, stories.c.id == old_story_tropes.c.story_id).join(
                old_tropes, old_tropes.c.id == old_story_tropes.c.trope_id
            )
        )
        .order_by(stories.c.dataset_id, old_tropes.c.normalized_text, old_story_tropes.c.story_id)
    ).mappings()
    for row in trope_rows:
        key = (row["dataset_id"], row["old_term_id"])
        trope_id = trope_ids_by_dataset_term.get(key)
        if trope_id is None:
            trope_id = str(uuid.uuid4())
            trope_ids_by_dataset_term[key] = trope_id
            bind.execute(
                new_tropes.insert().values(
                    id=trope_id,
                    dataset_id=row["dataset_id"],
                    text=row["text"],
                    normalized_text=row["normalized_text"],
                    cached_story_count=0,
                    created_at=row["term_created_at"],
                    updated_at=row["term_updated_at"],
                )
            )
        bind.execute(
            new_story_tropes.insert().values(
                story_id=row["story_id"],
                trope_id=trope_id,
                origin=row["origin"],
                status=row["status"],
                position=row["position"],
                created_at=row["link_created_at"],
                updated_at=row["link_updated_at"],
            )
        )
        trope_story_counts[trope_id] += 1

    orphan_tropes = bind.execute(
        sa.select(
            old_tropes.c.id,
            old_tropes.c.text,
            old_tropes.c.normalized_text,
            old_tropes.c.cached_story_count,
            old_tropes.c.created_at,
            old_tropes.c.updated_at,
        ).where(
            ~sa.exists(
                sa.select(sa.literal(1)).where(old_story_tropes.c.trope_id == old_tropes.c.id)
            )
        )
    ).mappings()
    if fallback_dataset_id is not None:
        for row in orphan_tropes:
            bind.execute(
                new_tropes.insert().values(
                    id=str(uuid.uuid4()),
                    dataset_id=fallback_dataset_id,
                    text=row["text"],
                    normalized_text=row["normalized_text"],
                    cached_story_count=int(row["cached_story_count"] or 0),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )

    for trope_id, story_count in trope_story_counts.items():
        bind.execute(
            new_tropes.update().where(new_tropes.c.id == trope_id).values(cached_story_count=story_count)
        )

    keyword_ids_by_dataset_term: dict[tuple[str, str], str] = {}
    keyword_story_counts: defaultdict[str, int] = defaultdict(int)
    keyword_rows = bind.execute(
        sa.select(
            stories.c.dataset_id.label("dataset_id"),
            old_keywords.c.id.label("old_term_id"),
            old_keywords.c.text,
            old_keywords.c.normalized_text,
            old_keywords.c.created_at.label("term_created_at"),
            old_keywords.c.updated_at.label("term_updated_at"),
            old_story_keywords.c.story_id,
            old_story_keywords.c.position,
            old_story_keywords.c.created_at.label("link_created_at"),
            old_story_keywords.c.updated_at.label("link_updated_at"),
        )
        .select_from(
            old_story_keywords.join(stories, stories.c.id == old_story_keywords.c.story_id).join(
                old_keywords, old_keywords.c.id == old_story_keywords.c.keyword_id
            )
        )
        .order_by(stories.c.dataset_id, old_keywords.c.normalized_text, old_story_keywords.c.story_id)
    ).mappings()
    for row in keyword_rows:
        key = (row["dataset_id"], row["old_term_id"])
        keyword_id = keyword_ids_by_dataset_term.get(key)
        if keyword_id is None:
            keyword_id = str(uuid.uuid4())
            keyword_ids_by_dataset_term[key] = keyword_id
            bind.execute(
                new_keywords.insert().values(
                    id=keyword_id,
                    dataset_id=row["dataset_id"],
                    text=row["text"],
                    normalized_text=row["normalized_text"],
                    cached_story_count=0,
                    created_at=row["term_created_at"],
                    updated_at=row["term_updated_at"],
                )
            )
        bind.execute(
            new_story_keywords.insert().values(
                story_id=row["story_id"],
                keyword_id=keyword_id,
                position=row["position"],
                created_at=row["link_created_at"],
                updated_at=row["link_updated_at"],
            )
        )
        keyword_story_counts[keyword_id] += 1

    orphan_keywords = bind.execute(
        sa.select(
            old_keywords.c.id,
            old_keywords.c.text,
            old_keywords.c.normalized_text,
            old_keywords.c.cached_story_count,
            old_keywords.c.created_at,
            old_keywords.c.updated_at,
        ).where(
            ~sa.exists(
                sa.select(sa.literal(1)).where(old_story_keywords.c.keyword_id == old_keywords.c.id)
            )
        )
    ).mappings()
    if fallback_dataset_id is not None:
        for row in orphan_keywords:
            bind.execute(
                new_keywords.insert().values(
                    id=str(uuid.uuid4()),
                    dataset_id=fallback_dataset_id,
                    text=row["text"],
                    normalized_text=row["normalized_text"],
                    cached_story_count=int(row["cached_story_count"] or 0),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )

    for keyword_id, story_count in keyword_story_counts.items():
        bind.execute(
            new_keywords.update().where(new_keywords.c.id == keyword_id).values(cached_story_count=story_count)
        )


def _populate_global_terms(
    bind: sa.Connection,
    *,
    scoped_tropes: sa.Table,
    scoped_keywords: sa.Table,
    scoped_story_tropes: sa.Table,
    scoped_story_keywords: sa.Table,
    new_tropes: sa.Table,
    new_keywords: sa.Table,
    new_story_tropes: sa.Table,
    new_story_keywords: sa.Table,
) -> None:
    global_trope_ids: dict[str, str] = {}
    trope_story_counts: defaultdict[str, int] = defaultdict(int)
    trope_rows = bind.execute(
        sa.select(
            scoped_tropes.c.id.label("scoped_term_id"),
            scoped_tropes.c.text,
            scoped_tropes.c.normalized_text,
            scoped_tropes.c.created_at.label("term_created_at"),
            scoped_tropes.c.updated_at.label("term_updated_at"),
            scoped_story_tropes.c.story_id,
            scoped_story_tropes.c.origin,
            scoped_story_tropes.c.status,
            scoped_story_tropes.c.position,
            scoped_story_tropes.c.created_at.label("link_created_at"),
            scoped_story_tropes.c.updated_at.label("link_updated_at"),
        )
        .select_from(scoped_story_tropes.join(scoped_tropes, scoped_tropes.c.id == scoped_story_tropes.c.trope_id))
        .order_by(scoped_tropes.c.normalized_text, scoped_story_tropes.c.story_id)
    ).mappings()
    for row in trope_rows:
        trope_id = global_trope_ids.get(row["normalized_text"])
        if trope_id is None:
            trope_id = str(uuid.uuid4())
            global_trope_ids[row["normalized_text"]] = trope_id
            bind.execute(
                new_tropes.insert().values(
                    id=trope_id,
                    text=row["text"],
                    normalized_text=row["normalized_text"],
                    cached_story_count=0,
                    created_at=row["term_created_at"],
                    updated_at=row["term_updated_at"],
                )
            )
        bind.execute(
            new_story_tropes.insert().values(
                story_id=row["story_id"],
                trope_id=trope_id,
                origin=row["origin"],
                status=row["status"],
                position=row["position"],
                created_at=row["link_created_at"],
                updated_at=row["link_updated_at"],
            )
        )
        trope_story_counts[trope_id] += 1

    orphan_tropes = bind.execute(
        sa.select(
            scoped_tropes.c.text,
            scoped_tropes.c.normalized_text,
            scoped_tropes.c.cached_story_count,
            scoped_tropes.c.created_at,
            scoped_tropes.c.updated_at,
        ).where(
            ~sa.exists(
                sa.select(sa.literal(1)).where(scoped_story_tropes.c.trope_id == scoped_tropes.c.id)
            )
        )
    ).mappings()
    for row in orphan_tropes:
        if row["normalized_text"] in global_trope_ids:
            continue
        trope_id = str(uuid.uuid4())
        global_trope_ids[row["normalized_text"]] = trope_id
        bind.execute(
            new_tropes.insert().values(
                id=trope_id,
                text=row["text"],
                normalized_text=row["normalized_text"],
                cached_story_count=int(row["cached_story_count"] or 0),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )

    for trope_id, story_count in trope_story_counts.items():
        bind.execute(
            new_tropes.update().where(new_tropes.c.id == trope_id).values(cached_story_count=story_count)
        )

    global_keyword_ids: dict[str, str] = {}
    keyword_story_counts: defaultdict[str, int] = defaultdict(int)
    keyword_rows = bind.execute(
        sa.select(
            scoped_keywords.c.id.label("scoped_term_id"),
            scoped_keywords.c.text,
            scoped_keywords.c.normalized_text,
            scoped_keywords.c.created_at.label("term_created_at"),
            scoped_keywords.c.updated_at.label("term_updated_at"),
            scoped_story_keywords.c.story_id,
            scoped_story_keywords.c.position,
            scoped_story_keywords.c.created_at.label("link_created_at"),
            scoped_story_keywords.c.updated_at.label("link_updated_at"),
        )
        .select_from(
            scoped_story_keywords.join(scoped_keywords, scoped_keywords.c.id == scoped_story_keywords.c.keyword_id)
        )
        .order_by(scoped_keywords.c.normalized_text, scoped_story_keywords.c.story_id)
    ).mappings()
    for row in keyword_rows:
        keyword_id = global_keyword_ids.get(row["normalized_text"])
        if keyword_id is None:
            keyword_id = str(uuid.uuid4())
            global_keyword_ids[row["normalized_text"]] = keyword_id
            bind.execute(
                new_keywords.insert().values(
                    id=keyword_id,
                    text=row["text"],
                    normalized_text=row["normalized_text"],
                    cached_story_count=0,
                    created_at=row["term_created_at"],
                    updated_at=row["term_updated_at"],
                )
            )
        bind.execute(
            new_story_keywords.insert().values(
                story_id=row["story_id"],
                keyword_id=keyword_id,
                position=row["position"],
                created_at=row["link_created_at"],
                updated_at=row["link_updated_at"],
            )
        )
        keyword_story_counts[keyword_id] += 1

    orphan_keywords = bind.execute(
        sa.select(
            scoped_keywords.c.text,
            scoped_keywords.c.normalized_text,
            scoped_keywords.c.cached_story_count,
            scoped_keywords.c.created_at,
            scoped_keywords.c.updated_at,
        ).where(
            ~sa.exists(
                sa.select(sa.literal(1)).where(scoped_story_keywords.c.keyword_id == scoped_keywords.c.id)
            )
        )
    ).mappings()
    for row in orphan_keywords:
        if row["normalized_text"] in global_keyword_ids:
            continue
        keyword_id = str(uuid.uuid4())
        global_keyword_ids[row["normalized_text"]] = keyword_id
        bind.execute(
            new_keywords.insert().values(
                id=keyword_id,
                text=row["text"],
                normalized_text=row["normalized_text"],
                cached_story_count=int(row["cached_story_count"] or 0),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )

    for keyword_id, story_count in keyword_story_counts.items():
        bind.execute(
            new_keywords.update().where(new_keywords.c.id == keyword_id).values(cached_story_count=story_count)
        )


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    datasets = sa.Table("datasets", metadata, autoload_with=bind)
    stories = sa.Table("stories", metadata, autoload_with=bind)
    old_tropes = sa.Table("tropes", metadata, autoload_with=bind)
    old_keywords = sa.Table("keywords", metadata, autoload_with=bind)
    old_story_tropes = sa.Table("story_tropes", metadata, autoload_with=bind)
    old_story_keywords = sa.Table("story_keywords", metadata, autoload_with=bind)

    new_tropes, new_keywords, new_story_tropes, new_story_keywords = _create_scoped_term_tables()
    _populate_scoped_terms(
        bind,
        datasets=datasets,
        stories=stories,
        old_tropes=old_tropes,
        old_keywords=old_keywords,
        old_story_tropes=old_story_tropes,
        old_story_keywords=old_story_keywords,
        new_tropes=new_tropes,
        new_keywords=new_keywords,
        new_story_tropes=new_story_tropes,
        new_story_keywords=new_story_keywords,
    )

    _drop_term_artifact_tables()
    op.drop_table("story_keywords")
    op.drop_table("story_tropes")
    op.drop_table("keywords")
    op.drop_table("tropes")

    op.rename_table("tropes_scoped", "tropes")
    op.rename_table("keywords_scoped", "keywords")
    op.rename_table("story_tropes_scoped", "story_tropes")
    op.rename_table("story_keywords_scoped", "story_keywords")

    op.create_index(op.f("ix_tropes_dataset_id"), "tropes", ["dataset_id"], unique=False)
    op.create_index("uq_tropes_dataset_normalized_text", "tropes", ["dataset_id", "normalized_text"], unique=True)
    op.create_index(op.f("ix_keywords_dataset_id"), "keywords", ["dataset_id"], unique=False)
    op.create_index(
        "uq_keywords_dataset_normalized_text",
        "keywords",
        ["dataset_id", "normalized_text"],
        unique=True,
    )

    _recreate_term_artifact_tables()


def downgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    scoped_tropes = sa.Table("tropes", metadata, autoload_with=bind)
    scoped_keywords = sa.Table("keywords", metadata, autoload_with=bind)
    scoped_story_tropes = sa.Table("story_tropes", metadata, autoload_with=bind)
    scoped_story_keywords = sa.Table("story_keywords", metadata, autoload_with=bind)

    new_tropes, new_keywords, new_story_tropes, new_story_keywords = _create_global_term_tables()
    _populate_global_terms(
        bind,
        scoped_tropes=scoped_tropes,
        scoped_keywords=scoped_keywords,
        scoped_story_tropes=scoped_story_tropes,
        scoped_story_keywords=scoped_story_keywords,
        new_tropes=new_tropes,
        new_keywords=new_keywords,
        new_story_tropes=new_story_tropes,
        new_story_keywords=new_story_keywords,
    )

    _drop_term_artifact_tables()
    op.drop_index("uq_keywords_dataset_normalized_text", table_name="keywords")
    op.drop_index(op.f("ix_keywords_dataset_id"), table_name="keywords")
    op.drop_index("uq_tropes_dataset_normalized_text", table_name="tropes")
    op.drop_index(op.f("ix_tropes_dataset_id"), table_name="tropes")
    op.drop_table("story_keywords")
    op.drop_table("story_tropes")
    op.drop_table("keywords")
    op.drop_table("tropes")

    op.rename_table("tropes_global", "tropes")
    op.rename_table("keywords_global", "keywords")
    op.rename_table("story_tropes_global", "story_tropes")
    op.rename_table("story_keywords_global", "story_keywords")

    op.create_index(op.f("ix_tropes_normalized_text"), "tropes", ["normalized_text"], unique=True)
    op.create_index(op.f("ix_keywords_normalized_text"), "keywords", ["normalized_text"], unique=True)

    _recreate_term_artifact_tables()
