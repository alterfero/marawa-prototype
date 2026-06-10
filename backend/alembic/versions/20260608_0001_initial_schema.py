"""initial schema

Revision ID: 20260608_0001
Revises:
Create Date: 2026-06-08 16:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260608_0001"
down_revision = None
branch_labels = None
depends_on = None


dataset_status_enum = sa.Enum("staged", "active", "archived", "failed", name="datasetstatus", native_enum=False)
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
job_status_enum = sa.Enum("queued", "running", "succeeded", "failed", "cancelled", name="jobstatus", native_enum=False)
term_kind_enum = sa.Enum("trope", "keyword", name="termkind", native_enum=False)


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", dataset_status_enum, nullable=False, server_default="staged"),
        sa.Column("source_filename", sa.String(length=512), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_datasets_single_active",
        "datasets",
        ["status"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "tropes",
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.String(length=512), nullable=False),
        sa.Column("cached_story_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tropes_normalized_text"), "tropes", ["normalized_text"], unique=True)

    op.create_table(
        "keywords",
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.String(length=512), nullable=False),
        sa.Column("cached_story_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_keywords_normalized_text"), "keywords", ["normalized_text"], unique=True)

    op.create_table(
        "stories",
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=True),
        sa.Column("fields_json", sa.JSON(), nullable=False),
        sa.Column("row_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_stories_dataset_id"), "stories", ["dataset_id"], unique=False)
    op.create_index(
        "uq_stories_dataset_source_row_number",
        "stories",
        ["dataset_id", "source_row_number"],
        unique=True,
        sqlite_where=sa.text("source_row_number IS NOT NULL"),
    )

    op.create_table(
        "jobs",
        sa.Column("dataset_id", sa.String(length=36), nullable=True),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("status", job_status_enum, nullable=False, server_default="queued"),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_dataset_id"), "jobs", ["dataset_id"], unique=False)
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"], unique=False)

    op.create_table(
        "story_tropes",
        sa.Column("story_id", sa.String(length=36), nullable=False),
        sa.Column("trope_id", sa.String(length=36), nullable=False),
        sa.Column("origin", story_trope_origin_enum, nullable=False, server_default="csv_import"),
        sa.Column("status", assignment_status_enum, nullable=False, server_default="pending"),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trope_id"], ["tropes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("story_id", "trope_id"),
        sa.UniqueConstraint("story_id", "trope_id", name="uq_story_tropes_story_id_trope_id"),
    )

    op.create_table(
        "story_keywords",
        sa.Column("story_id", sa.String(length=36), nullable=False),
        sa.Column("keyword_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["keyword_id"], ["keywords.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("story_id", "keyword_id"),
        sa.UniqueConstraint("story_id", "keyword_id", name="uq_story_keywords_story_id_keyword_id"),
    )

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
    op.create_index(
        "uq_term_embeddings_trope_model",
        "term_embeddings",
        ["trope_id", "model_name"],
        unique=True,
        sqlite_where=sa.text("trope_id IS NOT NULL"),
    )
    op.create_index(
        "uq_term_embeddings_keyword_model",
        "term_embeddings",
        ["keyword_id", "model_name"],
        unique=True,
        sqlite_where=sa.text("keyword_id IS NOT NULL"),
    )

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


def downgrade() -> None:
    op.drop_index(op.f("ix_term_similarity_cache_term_kind"), table_name="term_similarity_cache")
    op.drop_index(op.f("ix_term_similarity_cache_artifact_version"), table_name="term_similarity_cache")
    op.drop_table("term_similarity_cache")
    op.drop_index(op.f("ix_term_embeddings_artifact_version"), table_name="term_embeddings")
    op.drop_index("uq_term_embeddings_keyword_model", table_name="term_embeddings")
    op.drop_index("uq_term_embeddings_trope_model", table_name="term_embeddings")
    op.drop_index(op.f("ix_term_embeddings_term_kind"), table_name="term_embeddings")
    op.drop_table("term_embeddings")
    op.drop_table("story_keywords")
    op.drop_table("story_tropes")
    op.drop_index(op.f("ix_jobs_status"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_dataset_id"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("uq_stories_dataset_source_row_number", table_name="stories")
    op.drop_index(op.f("ix_stories_dataset_id"), table_name="stories")
    op.drop_table("stories")
    op.drop_index(op.f("ix_keywords_normalized_text"), table_name="keywords")
    op.drop_table("keywords")
    op.drop_index(op.f("ix_tropes_normalized_text"), table_name="tropes")
    op.drop_table("tropes")
    op.drop_index("uq_datasets_single_active", table_name="datasets")
    op.drop_table("datasets")
