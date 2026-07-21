from __future__ import annotations

from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.parsing import clean_text, normalize_text
from app.db.models import (
    Dataset,
    DatasetStatus,
    Story,
    StoryTrope,
    TermKind,
    TermReviewStatus,
    Trope,
    TropeConfirmationStatus,
    UserRole,
)
from app.services.audit import record_audit_event
from app.services.reviews import queue_term_review_item


TITLE_FIELDS = [
    "Story title (Eng)",
    "Story title (French)",
    "Story title (other)",
]


class TropeLookupError(ValueError):
    """Base error for trope lookup operations."""


class TropeLookupNotFoundError(TropeLookupError):
    """Raised when a requested trope does not exist."""


class TropeMutationValidationError(TropeLookupError):
    """Raised when a trope create request is invalid."""


class TropeVersionConflictError(TropeLookupError):
    """Raised when a trope mutation uses a stale version."""


def _get_active_dataset(session: Session) -> Dataset:
    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if active_dataset is None:
        raise TropeLookupNotFoundError("Canonical trope not found.")
    return active_dataset


def _get_active_tropes_by_id(session: Session, dataset_id: str, trope_ids: list[str]) -> dict[str, Trope]:
    tropes = session.scalars(
        select(Trope).where(
            Trope.dataset_id == dataset_id,
            Trope.id.in_(trope_ids),
        )
    ).all()
    tropes_by_id = {trope.id: trope for trope in tropes}
    missing_trope_ids = [trope_id for trope_id in trope_ids if trope_id not in tropes_by_id]
    if missing_trope_ids:
        raise TropeLookupNotFoundError("Canonical trope not found.")
    return tropes_by_id


def _apply_trope_confirmation_status_update(
    session: Session,
    *,
    dataset_id: str,
    trope: Trope,
    expected_version: int,
    confirmation_status: TropeConfirmationStatus,
    actor_user_id: str,
) -> None:
    if expected_version < 1:
        raise TropeMutationValidationError("Expected trope version must be at least 1.")

    if trope.version != expected_version:
        raise TropeVersionConflictError(
            f"Trope version conflict: expected version {expected_version}, current version is {trope.version}."
        )

    if trope.confirmation_status == confirmation_status:
        return

    previous_status = trope.confirmation_status.value
    trope.confirmation_status = confirmation_status
    trope.version += 1
    trope.updated_by_user_id = actor_user_id
    record_audit_event(
        session,
        event_type="trope.confirmation_status_updated",
        actor_user_id=actor_user_id,
        dataset_id=dataset_id,
        subject_table="tropes",
        subject_id=trope.id,
        payload={
            "previous_confirmation_status": previous_status,
            "confirmation_status": trope.confirmation_status.value,
            "version": trope.version,
        },
    )


def ensure_canonical_trope(
    session: Session,
    text: str,
    *,
    actor_user_id: str | None = None,
    actor_role: UserRole | None = None,
) -> tuple[dict, bool]:
    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if active_dataset is None:
        raise TropeMutationValidationError("No active dataset is available.")

    trope_text = clean_text(text)
    marker = normalize_text(trope_text)
    if not marker:
        raise TropeMutationValidationError("Trope text cannot be empty.")

    trope = session.scalar(
        select(Trope).where(
            Trope.dataset_id == active_dataset.id,
            Trope.normalized_text == marker,
        )
    )
    if trope is not None:
        return _serialize_trope_summary(trope), False

    trope = Trope(
        dataset_id=active_dataset.id,
        text=trope_text,
        review_status=TermReviewStatus.PENDING_REVIEW if actor_role == UserRole.CONTRIBUTOR else TermReviewStatus.APPROVED,
        created_by_user_id=actor_user_id,
        updated_by_user_id=actor_user_id,
    )
    session.add(trope)
    try:
        session.flush()
        if actor_role == UserRole.CONTRIBUTOR and actor_user_id:
            queue_term_review_item(
                session,
                dataset_id=active_dataset.id,
                term_kind=TermKind.TROPE,
                subject_id=trope.id,
                actor_user_id=actor_user_id,
                text=trope.text,
            )
        record_audit_event(
            session,
            event_type="trope.created",
            actor_user_id=actor_user_id,
            dataset_id=active_dataset.id,
            subject_table="tropes",
            subject_id=trope.id,
            payload={
                "created": True,
                "review_status": trope.review_status.value,
            },
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        trope = session.scalar(
            select(Trope).where(
                Trope.dataset_id == active_dataset.id,
                Trope.normalized_text == marker,
            )
        )
        if trope is None:
            raise
        return _serialize_trope_summary(trope), False

    return _serialize_trope_summary(trope), True


def get_trope_detail(session: Session, trope_id: str) -> dict:
    active_dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if active_dataset is None:
        raise TropeLookupNotFoundError("Canonical trope not found.")

    trope = session.scalar(
        select(Trope).where(
            Trope.id == trope_id,
            Trope.dataset_id == active_dataset.id,
        )
    )
    if trope is None:
        raise TropeLookupNotFoundError("Canonical trope not found.")

    stories = session.scalars(
        select(Story)
        .join(StoryTrope, StoryTrope.story_id == Story.id)
        .where(
            Story.dataset_id == active_dataset.id,
            StoryTrope.trope_id == trope.id,
        )
        .order_by(
            case((Story.source_row_number.is_(None), 1), else_=0),
            Story.source_row_number,
            Story.created_at,
            Story.id,
        )
    ).all()

    return {
        "id": trope.id,
        "version": trope.version,
        "text": trope.text,
        "confirmation_status": trope.confirmation_status.value,
        "story_count": len(stories),
        "stories": [
            {
                "id": story.id,
                "title": _story_title(story),
                "source_row_number": story.source_row_number,
            }
            for story in stories
        ],
    }


def set_trope_confirmation_status(
    session: Session,
    trope_id: str,
    *,
    expected_version: int,
    confirmation_status: TropeConfirmationStatus,
    actor_user_id: str,
) -> dict:
    active_dataset = _get_active_dataset(session)
    tropes_by_id = _get_active_tropes_by_id(session, active_dataset.id, [trope_id])
    trope = tropes_by_id[trope_id]
    _apply_trope_confirmation_status_update(
        session,
        dataset_id=active_dataset.id,
        trope=trope,
        expected_version=expected_version,
        confirmation_status=confirmation_status,
        actor_user_id=actor_user_id,
    )
    session.flush()
    session.commit()
    return _serialize_trope_summary(trope)


def set_trope_confirmation_statuses(
    session: Session,
    *,
    updates: list[dict[str, object]],
    actor_user_id: str,
) -> list[dict]:
    if not updates:
        raise TropeMutationValidationError("Provide at least one trope confirmation update.")

    trope_ids = [clean_text(str(update.get("trope_id", ""))) for update in updates]
    if any(not trope_id for trope_id in trope_ids):
        raise TropeMutationValidationError("Every trope confirmation update must include a trope_id.")
    if len(set(trope_ids)) != len(trope_ids):
        raise TropeMutationValidationError("Duplicate trope confirmation updates are not allowed.")

    active_dataset = _get_active_dataset(session)
    tropes_by_id = _get_active_tropes_by_id(session, active_dataset.id, trope_ids)

    for update, trope_id in zip(updates, trope_ids, strict=True):
        expected_version = int(update.get("expected_version", 0))
        confirmation_status = update.get("confirmation_status")
        if not isinstance(confirmation_status, TropeConfirmationStatus):
            raise TropeMutationValidationError("Invalid trope confirmation status.")
        _apply_trope_confirmation_status_update(
            session,
            dataset_id=active_dataset.id,
            trope=tropes_by_id[trope_id],
            expected_version=expected_version,
            confirmation_status=confirmation_status,
            actor_user_id=actor_user_id,
        )

    session.flush()
    session.commit()
    return [_serialize_trope_summary(tropes_by_id[trope_id]) for trope_id in trope_ids]


def _story_title(story: Story) -> str:
    fields = story.fields_json or {}
    for field_name in TITLE_FIELDS:
        value = clean_text(fields.get(field_name, ""))
        if value:
            return value
    return story.id


def _serialize_trope_summary(trope: Trope) -> dict:
    return {
        "id": trope.id,
        "version": trope.version,
        "text": trope.text,
        "confirmation_status": trope.confirmation_status.value,
        "story_count": int(trope.cached_story_count or 0),
    }
