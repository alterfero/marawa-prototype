"""Specificity ratings and dataset-scoped broader trope links."""
from alembic import op
import sqlalchemy as sa

revision = "20261116_0020"
down_revision = "20261023_0019"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tropes", sa.Column("specificity_level", sa.Integer(),
                                      sa.CheckConstraint("specificity_level BETWEEN 1 AND 4")))
    op.add_column("tropes", sa.Column("specificity_source", sa.String(16), sa.CheckConstraint(
        "specificity_source IN ('researcher','curator','provisional','disputed')")))
    op.create_index("uq_tropes_dataset_id_id", "tropes", ["dataset_id", "id"], unique=True)
    op.create_index("ix_tropes_dataset_specificity", "tropes", ["dataset_id", "specificity_level"])
    op.create_table("trope_parents",
        sa.Column("dataset_id", sa.String(36), primary_key=True),
        sa.Column("child_id", sa.String(36), primary_key=True),
        sa.Column("parent_id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id", "child_id"], ["tropes.dataset_id", "tropes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id", "parent_id"], ["tropes.dataset_id", "tropes.id"], ondelete="CASCADE"),
        sa.CheckConstraint("child_id <> parent_id"),
        sa.CheckConstraint("status IN ('inferred','pending','approved','rejected')"),
    )
    op.create_index("ix_trope_parents_dataset_parent", "trope_parents", ["dataset_id", "parent_id"])

    from app.compute.specificity import classify
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, text FROM tropes WHERE dataset_id IN "
                                "(SELECT id FROM datasets WHERE status IN ('active', 'staged'))")).all()
    if rows:
        updates = []
        for trope_id, text in rows:
            level, source = classify(text)
            updates.append({"id": trope_id, "level": level, "source": source})
        conn.execute(sa.text("UPDATE tropes SET specificity_level=:level, specificity_source=:source, "
                             "version=version+1 WHERE id=:id"), updates)
        conn.execute(sa.text("UPDATE datasets SET version=version+1 WHERE status IN ('active', 'staged') "
                             "AND EXISTS (SELECT 1 FROM tropes WHERE tropes.dataset_id=datasets.id)"))

        from app.db.models import AuditEvent
        for dataset_id, version in conn.execute(sa.text(
            "SELECT id, version FROM datasets WHERE status IN ('active', 'staged') "
            "AND EXISTS (SELECT 1 FROM tropes WHERE tropes.dataset_id=datasets.id)"
        )):
            conn.execute(AuditEvent.__table__.insert().values(
                event_type="trope.ontology_initialized", dataset_id=dataset_id,
                payload_json={"dataset_version": version, "migration": revision},
            ))


def downgrade():
    op.drop_table("trope_parents")
    op.drop_index("ix_tropes_dataset_specificity", table_name="tropes")
    op.drop_index("uq_tropes_dataset_id_id", table_name="tropes")
    op.drop_column("tropes", "specificity_source")
    op.drop_column("tropes", "specificity_level")
