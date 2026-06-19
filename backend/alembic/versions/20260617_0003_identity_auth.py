"""identity and session auth tables

Revision ID: 20260617_0003
Revises: 20260617_0002
Create Date: 2026-06-17 14:15:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260617_0003"
down_revision = "20260617_0002"
branch_labels = None
depends_on = None


user_role_enum = sa.Enum("guest", "contributor", "admin", name="userrole", native_enum=False)
user_status_enum = sa.Enum(
    "active",
    "inactive",
    "pending_invite",
    name="userstatus",
    native_enum=False,
)
invite_reset_token_kind_enum = sa.Enum(
    "invite",
    "admin_reset",
    name="inviteresettokenkind",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("role", user_role_enum, nullable=False, server_default="guest"),
        sa.Column("password_hash", sa.String(length=1024), nullable=True),
        sa.Column("status", user_status_enum, nullable=False, server_default="pending_invite"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)
    op.create_index(op.f("ix_users_status"), "users", ["status"], unique=False)

    op.create_table(
        "user_sessions",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("session_token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(length=255), nullable=True),
        sa.Column("user_agent", sa.String(length=1024), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_sessions_expires_at"), "user_sessions", ["expires_at"], unique=False)
    op.create_index(op.f("ix_user_sessions_revoked_at"), "user_sessions", ["revoked_at"], unique=False)
    op.create_index(op.f("ix_user_sessions_session_token_hash"), "user_sessions", ["session_token_hash"], unique=True)
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"], unique=False)

    op.create_table(
        "invite_reset_tokens",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_kind", invite_reset_token_kind_enum, nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_invite_reset_tokens_consumed_at"), "invite_reset_tokens", ["consumed_at"], unique=False)
    op.create_index(op.f("ix_invite_reset_tokens_created_by_user_id"), "invite_reset_tokens", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_invite_reset_tokens_expires_at"), "invite_reset_tokens", ["expires_at"], unique=False)
    op.create_index(op.f("ix_invite_reset_tokens_token_hash"), "invite_reset_tokens", ["token_hash"], unique=True)
    op.create_index(op.f("ix_invite_reset_tokens_token_kind"), "invite_reset_tokens", ["token_kind"], unique=False)
    op.create_index(op.f("ix_invite_reset_tokens_user_id"), "invite_reset_tokens", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_invite_reset_tokens_user_id"), table_name="invite_reset_tokens")
    op.drop_index(op.f("ix_invite_reset_tokens_token_kind"), table_name="invite_reset_tokens")
    op.drop_index(op.f("ix_invite_reset_tokens_token_hash"), table_name="invite_reset_tokens")
    op.drop_index(op.f("ix_invite_reset_tokens_expires_at"), table_name="invite_reset_tokens")
    op.drop_index(op.f("ix_invite_reset_tokens_created_by_user_id"), table_name="invite_reset_tokens")
    op.drop_index(op.f("ix_invite_reset_tokens_consumed_at"), table_name="invite_reset_tokens")
    op.drop_table("invite_reset_tokens")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_session_token_hash"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_revoked_at"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_expires_at"), table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index(op.f("ix_users_status"), table_name="users")
    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
