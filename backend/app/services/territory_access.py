"""Shared service-layer checks and catalog queries for story territories."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.parsing import clean_text
from app.core.territories import normalize_territories, territory_key
from app.db.models import Dataset, DatasetStatus, Story, UserRole


def _allowed_territory_keys(actor_territories: Iterable[object] | None) -> set[str]:
    return {
        key
        for key in (territory_key(territory) for territory in normalize_territories(actor_territories))
        if key
    }


def _territory_labels_by_key(session: Session, *, dataset_id: str) -> dict[str, str]:
    """Return one canonical display label for each territory in a dataset."""
    territories_by_key: dict[str, str] = {}
    for fields in session.scalars(
        select(Story.fields_json)
        .where(Story.dataset_id == dataset_id)
        .order_by(Story.source_row_number, Story.created_at, Story.id)
    ):
        territory = clean_text((fields or {}).get("territory", ""))
        key = territory_key(territory)
        if key:
            territories_by_key.setdefault(key, territory)
    return territories_by_key


def _sorted_territory_labels(territories_by_key: Mapping[str, str]) -> list[str]:
    return sorted(territories_by_key.values(), key=lambda territory: (territory_key(territory), territory))


def list_available_territories(session: Session) -> list[str]:
    """Return all distinct, non-empty territories in the active dataset."""
    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if active_dataset is None:
        return []
    return _sorted_territory_labels(_territory_labels_by_key(session, dataset_id=active_dataset.id))


def list_available_territories_for_actor(
    session: Session,
    *,
    actor_role: UserRole | None,
    actor_territories: Iterable[object] | None,
) -> list[str]:
    """Return active-dataset territory choices visible to the current actor."""
    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if active_dataset is None:
        return []

    territories_by_key = _territory_labels_by_key(session, dataset_id=active_dataset.id)
    if actor_role == UserRole.CONTRIBUTOR:
        allowed_territory_keys = _allowed_territory_keys(actor_territories)
        territories_by_key = {
            key: label
            for key, label in territories_by_key.items()
            if key in allowed_territory_keys
        }
    return _sorted_territory_labels(territories_by_key)


def find_existing_territory_label(
    session: Session,
    *,
    dataset_id: str,
    requested_territory: object,
) -> str | None:
    """Resolve a requested territory to the dataset's canonical display label."""
    requested_key = territory_key(requested_territory)
    if not requested_key:
        return None
    return _territory_labels_by_key(session, dataset_id=dataset_id).get(requested_key)


def can_edit_story_fields(
    fields: Mapping[str, object] | None,
    *,
    actor_role: UserRole | None,
    actor_territories: Iterable[object] | None,
) -> bool:
    """Return whether a caller may edit a story; reading is not territory restricted."""
    if actor_role == UserRole.ADMIN:
        return True
    if actor_role != UserRole.CONTRIBUTOR:
        return False

    allowed_territory_keys = _allowed_territory_keys(actor_territories)
    story_territory_key = territory_key((fields or {}).get("territory", ""))
    return bool(story_territory_key and story_territory_key in allowed_territory_keys)
