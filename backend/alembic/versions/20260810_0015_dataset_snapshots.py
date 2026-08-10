"""add logical dataset recovery snapshots

Revision ID: 20260810_0015
Revises: 20260803_0014
Create Date: 2026-08-10 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260810_0015"
down_revision = "20260803_0014"
branch_labels = None
depends_on = None


snapshot_status_enum = sa.Enum(
    "creating",
    "ready",
    "failed",
    "deleting",
    name="datasetsnapshotstatus",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "dataset_snapshots",
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", snapshot_status_enum, nullable=False, server_default="creating"),
        sa.Column("reason", sa.String(length=100), nullable=False, server_default="full_rebuild"),
        sa.Column("dataset_id", sa.String(length=36), nullable=True),
        sa.Column("source_job_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("source_dataset_version", sa.Integer(), nullable=True),
        sa.Column("source_filename", sa.String(length=512), nullable=True),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("content_length", sa.Integer(), nullable=True),
        sa.Column("content_format", sa.String(length=100), nullable=False, server_default="marawa_full_csv_gzip"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("story_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trope_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("theme_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("keyword_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sequence", name="uq_dataset_snapshots_sequence"),
        sa.UniqueConstraint("source_job_id", name="uq_dataset_snapshots_source_job_id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(op.f("ix_dataset_snapshots_sequence"), "dataset_snapshots", ["sequence"], unique=False)
    op.create_index(op.f("ix_dataset_snapshots_status"), "dataset_snapshots", ["status"], unique=False)
    op.create_index(op.f("ix_dataset_snapshots_dataset_id"), "dataset_snapshots", ["dataset_id"], unique=False)
    op.create_index(op.f("ix_dataset_snapshots_source_job_id"), "dataset_snapshots", ["source_job_id"], unique=False)
    op.create_index(
        op.f("ix_dataset_snapshots_created_by_user_id"),
        "dataset_snapshots",
        ["created_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_dataset_snapshots_created_by_user_id"), table_name="dataset_snapshots")
    op.drop_index(op.f("ix_dataset_snapshots_source_job_id"), table_name="dataset_snapshots")
    op.drop_index(op.f("ix_dataset_snapshots_dataset_id"), table_name="dataset_snapshots")
    op.drop_index(op.f("ix_dataset_snapshots_status"), table_name="dataset_snapshots")
    op.drop_index(op.f("ix_dataset_snapshots_sequence"), table_name="dataset_snapshots")
    op.drop_table("dataset_snapshots")
