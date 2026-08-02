"""retain the retired theme-trope revision identifier

Revision ID: 20260802_0010
Revises: 20260729_0009
Create Date: 2026-08-02 13:00:00
"""


revision = "20260802_0010"
down_revision = "20260729_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep databases stamped by the retired revision on a valid history path."""


def downgrade() -> None:
    """The compatibility revision intentionally has no schema operation."""

