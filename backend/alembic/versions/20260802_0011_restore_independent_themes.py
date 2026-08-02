"""restore independent theme assignments after a retired migration

Revision ID: 20260802_0011
Revises: 20260802_0010
Create Date: 2026-08-02 13:10:00
"""

from datetime import datetime, timezone
import uuid

from alembic import op
import sqlalchemy as sa


revision = "20260802_0011"
down_revision = "20260802_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Restore Theme/StoryTheme data from the retired ``slot = theme`` links.

    Fresh databases never receive the retired column, so this migration is a
    no-op for them. Databases that already applied the retired revision regain
    their independent theme records before the temporary links are removed.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    story_trope_columns = {column["name"] for column in inspector.get_columns("story_tropes")}
    if "slot" not in story_trope_columns:
        return

    metadata = sa.MetaData()
    stories = sa.Table("stories", metadata, autoload_with=bind)
    tropes = sa.Table("tropes", metadata, autoload_with=bind)
    story_tropes = sa.Table("story_tropes", metadata, autoload_with=bind)
    themes = sa.Table("themes", metadata, autoload_with=bind)
    story_themes = sa.Table("story_themes", metadata, autoload_with=bind)
    similarity_cache = (
        sa.Table("term_similarity_cache", metadata, autoload_with=bind)
        if "term_similarity_cache" in inspector.get_table_names()
        else None
    )

    now = datetime.now(timezone.utc)
    themes_by_dataset_and_text = {
        (row["dataset_id"], row["normalized_text"]): row["id"]
        for row in bind.execute(
            sa.select(themes.c.id, themes.c.dataset_id, themes.c.normalized_text)
        ).mappings()
    }
    existing_story_themes = {
        (row["story_id"], row["theme_id"])
        for row in bind.execute(sa.select(story_themes.c.story_id, story_themes.c.theme_id)).mappings()
    }
    retired_links = list(
        bind.execute(
            sa.select(
                story_tropes.c.story_id,
                story_tropes.c.trope_id,
                story_tropes.c.position,
                stories.c.dataset_id,
                tropes.c.text,
                tropes.c.normalized_text,
            )
            .select_from(
                story_tropes.join(stories, stories.c.id == story_tropes.c.story_id).join(
                    tropes, tropes.c.id == story_tropes.c.trope_id
                )
            )
            .where(story_tropes.c.slot == "theme")
        ).mappings()
    )
    retired_trope_ids = {row["trope_id"] for row in retired_links}

    for row in retired_links:
        key = (row["dataset_id"], row["normalized_text"])
        theme_id = themes_by_dataset_and_text.get(key)
        if theme_id is None:
            theme_id = str(uuid.uuid4())
            themes_by_dataset_and_text[key] = theme_id
            bind.execute(
                themes.insert().values(
                    id=theme_id,
                    dataset_id=row["dataset_id"],
                    version=1,
                    text=row["text"],
                    normalized_text=row["normalized_text"],
                    confirmation_status="unconfirmed",
                    cached_story_count=0,
                    created_at=now,
                    updated_at=now,
                )
            )

        story_theme_key = (row["story_id"], theme_id)
        if story_theme_key not in existing_story_themes:
            bind.execute(
                story_themes.insert().values(
                    story_id=row["story_id"],
                    theme_id=theme_id,
                    position=row["position"],
                    created_at=now,
                    updated_at=now,
                )
            )
            existing_story_themes.add(story_theme_key)

    bind.execute(themes.update().values(cached_story_count=0))
    for row in bind.execute(
        sa.select(story_themes.c.theme_id, sa.func.count())
        .group_by(story_themes.c.theme_id)
    ).mappings():
        bind.execute(
            themes.update()
            .where(themes.c.id == row["theme_id"])
            .values(cached_story_count=row["count"])
        )

    bind.execute(story_tropes.delete().where(story_tropes.c.slot == "theme"))

    orphaned_trope_ids = set()
    if retired_trope_ids:
        orphaned_trope_ids = {
            row["id"]
            for row in bind.execute(
                sa.select(tropes.c.id).where(
                    tropes.c.id.in_(retired_trope_ids),
                    ~sa.exists(sa.select(sa.literal(1)).where(story_tropes.c.trope_id == tropes.c.id)),
                )
            ).mappings()
        }
    if orphaned_trope_ids:
        if similarity_cache is not None:
            bind.execute(
                similarity_cache.delete().where(
                    similarity_cache.c.term_kind == "trope",
                    sa.or_(
                        similarity_cache.c.source_term_id.in_(orphaned_trope_ids),
                        similarity_cache.c.target_term_id.in_(orphaned_trope_ids),
                    ),
                )
            )
        bind.execute(tropes.delete().where(tropes.c.id.in_(orphaned_trope_ids)))

    primary_key_name = inspector.get_pk_constraint("story_tropes").get("name")
    if primary_key_name is None:
        raise RuntimeError("Could not determine the retired story_tropes primary key name.")

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("story_tropes") as batch_op:
            batch_op.drop_constraint(primary_key_name, type_="primary")
            batch_op.drop_column("slot")
            batch_op.create_primary_key("pk_story_tropes", ["story_id", "trope_id"])
            batch_op.create_unique_constraint("uq_story_tropes_story_id_trope_id", ["story_id", "trope_id"])
    else:
        op.drop_constraint(primary_key_name, "story_tropes", type_="primary")
        op.drop_column("story_tropes", "slot")
        op.create_primary_key("pk_story_tropes", "story_tropes", ["story_id", "trope_id"])
        op.create_unique_constraint("uq_story_tropes_story_id_trope_id", "story_tropes", ["story_id", "trope_id"])


def downgrade() -> None:
    raise RuntimeError("The retired shared theme-trope schema cannot be restored safely.")
