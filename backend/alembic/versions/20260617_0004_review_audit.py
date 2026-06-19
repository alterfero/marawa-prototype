"""review workflow and audit trail

Revision ID: 20260617_0004
Revises: 20260617_0003
Create Date: 2026-06-17 17:05:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260617_0004"
down_revision = "20260617_0003"
branch_labels = None
depends_on = None


term_review_status_enum = sa.Enum(
    "approved",
    "pending_review",
    "rejected",
    name="termreviewstatus",
    native_enum=False,
)
review_status_enum = sa.Enum(
    "pending",
    "approved",
    "rejected",
    name="reviewstatus",
    native_enum=False,
)
review_type_enum = sa.Enum(
    "story_created",
    "story_updated",
    "trope_pending",
    "keyword_pending",
    name="reviewtype",
    native_enum=False,
)


def upgrade() -> None:
    with op.batch_alter_table("tropes", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column("review_status", term_review_status_enum, nullable=False, server_default="approved")
        )
        batch_op.add_column(sa.Column("created_by_user_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("updated_by_user_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key("fk_tropes_created_by_user_id_users", "users", ["created_by_user_id"], ["id"], ondelete="SET NULL")
        batch_op.create_foreign_key("fk_tropes_updated_by_user_id_users", "users", ["updated_by_user_id"], ["id"], ondelete="SET NULL")
        batch_op.create_index("ix_tropes_review_status", ["review_status"], unique=False)
        batch_op.create_index("ix_tropes_created_by_user_id", ["created_by_user_id"], unique=False)
        batch_op.create_index("ix_tropes_updated_by_user_id", ["updated_by_user_id"], unique=False)

    with op.batch_alter_table("keywords", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column("review_status", term_review_status_enum, nullable=False, server_default="approved")
        )
        batch_op.add_column(sa.Column("created_by_user_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("updated_by_user_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_keywords_created_by_user_id_users",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_keywords_updated_by_user_id_users",
            "users",
            ["updated_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_keywords_review_status", ["review_status"], unique=False)
        batch_op.create_index("ix_keywords_created_by_user_id", ["created_by_user_id"], unique=False)
        batch_op.create_index("ix_keywords_updated_by_user_id", ["updated_by_user_id"], unique=False)

    op.create_table(
        "review_items",
        sa.Column("dataset_id", sa.String(length=36), nullable=True),
        sa.Column("review_type", review_type_enum, nullable=False),
        sa.Column("subject_table", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("status", review_status_enum, nullable=False, server_default="pending"),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("resolved_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_review_items_dataset_id"), "review_items", ["dataset_id"], unique=False)
    op.create_index(op.f("ix_review_items_review_type"), "review_items", ["review_type"], unique=False)
    op.create_index(op.f("ix_review_items_subject_table"), "review_items", ["subject_table"], unique=False)
    op.create_index(op.f("ix_review_items_subject_id"), "review_items", ["subject_id"], unique=False)
    op.create_index(op.f("ix_review_items_status"), "review_items", ["status"], unique=False)
    op.create_index(op.f("ix_review_items_created_by_user_id"), "review_items", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_review_items_resolved_by_user_id"), "review_items", ["resolved_by_user_id"], unique=False)
    op.create_index(op.f("ix_review_items_resolved_at"), "review_items", ["resolved_at"], unique=False)

    op.create_table(
        "audit_events",
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("dataset_id", sa.String(length=36), nullable=True),
        sa.Column("subject_table", sa.String(length=64), nullable=True),
        sa.Column("subject_id", sa.String(length=36), nullable=True),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_events_event_type"), "audit_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_audit_events_actor_user_id"), "audit_events", ["actor_user_id"], unique=False)
    op.create_index(op.f("ix_audit_events_dataset_id"), "audit_events", ["dataset_id"], unique=False)
    op.create_index(op.f("ix_audit_events_subject_table"), "audit_events", ["subject_table"], unique=False)
    op.create_index(op.f("ix_audit_events_subject_id"), "audit_events", ["subject_id"], unique=False)
    op.create_index(op.f("ix_audit_events_request_id"), "audit_events", ["request_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_events_request_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_subject_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_subject_table"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_dataset_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_actor_user_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_event_type"), table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index(op.f("ix_review_items_resolved_at"), table_name="review_items")
    op.drop_index(op.f("ix_review_items_resolved_by_user_id"), table_name="review_items")
    op.drop_index(op.f("ix_review_items_created_by_user_id"), table_name="review_items")
    op.drop_index(op.f("ix_review_items_status"), table_name="review_items")
    op.drop_index(op.f("ix_review_items_subject_id"), table_name="review_items")
    op.drop_index(op.f("ix_review_items_subject_table"), table_name="review_items")
    op.drop_index(op.f("ix_review_items_review_type"), table_name="review_items")
    op.drop_index(op.f("ix_review_items_dataset_id"), table_name="review_items")
    op.drop_table("review_items")

    with op.batch_alter_table("keywords", recreate="auto") as batch_op:
        batch_op.drop_index("ix_keywords_updated_by_user_id")
        batch_op.drop_index("ix_keywords_created_by_user_id")
        batch_op.drop_index("ix_keywords_review_status")
        batch_op.drop_constraint("fk_keywords_updated_by_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_keywords_created_by_user_id_users", type_="foreignkey")
        batch_op.drop_column("updated_by_user_id")
        batch_op.drop_column("created_by_user_id")
        batch_op.drop_column("review_status")

    with op.batch_alter_table("tropes", recreate="auto") as batch_op:
        batch_op.drop_index("ix_tropes_updated_by_user_id")
        batch_op.drop_index("ix_tropes_created_by_user_id")
        batch_op.drop_index("ix_tropes_review_status")
        batch_op.drop_constraint("fk_tropes_updated_by_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_tropes_created_by_user_id_users", type_="foreignkey")
        batch_op.drop_column("updated_by_user_id")
        batch_op.drop_column("created_by_user_id")
        batch_op.drop_column("review_status")
