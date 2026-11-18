"""Keep trope ontology identities stable across dataset exports and restores."""
from alembic import op
import sqlalchemy as sa

revision = "20261116_0021"
down_revision = "20261116_0020"
branch_labels = None
depends_on = None


def upgrade():
    # Adding a column in place preserves assignment and embedding foreign keys on SQLite.
    op.add_column("tropes", sa.Column("ontology_id", sa.String(36), nullable=False, server_default=""))
    op.execute("UPDATE tropes SET ontology_id=id")
    op.create_index("uq_tropes_dataset_ontology_id", "tropes", ["dataset_id", "ontology_id"], unique=True)
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("tropes", "ontology_id", server_default=None)


def downgrade():
    op.drop_index("uq_tropes_dataset_ontology_id", table_name="tropes")
    op.drop_column("tropes", "ontology_id")
