from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AuditEvent


def record_audit_event(
    session: Session,
    *,
    event_type: str,
    actor_user_id: str | None = None,
    dataset_id: str | None = None,
    subject_table: str | None = None,
    subject_id: str | None = None,
    request_id: str | None = None,
    payload: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        actor_user_id=actor_user_id,
        dataset_id=dataset_id,
        subject_table=subject_table,
        subject_id=subject_id,
        request_id=request_id,
        payload_json=payload or {},
    )
    session.add(event)
    session.flush()
    return event
