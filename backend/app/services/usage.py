"""Visitor identity, atomic hourly recording, reporting, and CSV history."""

from collections.abc import Iterator
import csv
from datetime import datetime, timedelta
import hashlib
import hmac
from io import StringIO
from itertools import groupby
import json
import re
import secrets

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import UsageActivity, User
from app.services.auth import ensure_utc, utc_now


USAGE_COOKIE_NAME = "marawa_usage"
USAGE_COOKIE_MAX_AGE = 365 * 24 * 3600
ANONYMOUS = "anonymous"
HOUR = timedelta(hours=1)
MAX_RANGE = timedelta(days=31)


def browser_identity(settings: Settings, cookie: str | None) -> tuple[str, str]:
    """Return a signed browser cookie and a non-reversible database identifier."""
    def signature(value: str) -> str:
        return hmac.new(settings.session_secret.encode(), f"usage:{value}".encode(), hashlib.sha256).hexdigest()

    value, _, signed = (cookie or "").partition(".")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", value)
        or not re.fullmatch(r"[0-9a-f]{64}", signed)
        or not hmac.compare_digest(signature(value), signed)
    ):
        value = secrets.token_hex(32)
    return f"{value}.{signature(value)}", hashlib.sha256(value.encode()).hexdigest()


def record_activity(
    session: Session,
    *,
    browser_key: str,
    user: User | None,
    now: datetime | None = None,
) -> None:
    now = ensure_utc(now or utc_now())
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else postgres_insert
    statement = insert(UsageActivity).values(
        hour_start=now.replace(minute=0, second=0, microsecond=0),
        browser_key=browser_key,
        identity_key=user.id if user else ANONYMOUS,
        display_name=user.display_name if user else None,
        email=user.email if user else None,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.execute(statement.on_conflict_do_update(
        index_elements=["hour_start", "browser_key", "identity_key"],
        set_={
            "first_seen_at": case(
                (statement.excluded.first_seen_at < UsageActivity.first_seen_at, statement.excluded.first_seen_at),
                else_=UsageActivity.first_seen_at,
            ),
            "last_seen_at": case(
                (statement.excluded.last_seen_at > UsageActivity.last_seen_at, statement.excluded.last_seen_at),
                else_=UsageActivity.last_seen_at,
            ),
        },
    ))
    session.commit()


def validate_range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    if start is None and end is None:
        end = utc_now().replace(minute=0, second=0, microsecond=0) + HOUR
        start = end - timedelta(days=7)
    if start is None or end is None:
        raise ValueError("Provide both start and end.")
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Start and end must include a timezone.")
    start, end = ensure_utc(start), ensure_utc(end)
    if any(value.minute or value.second or value.microsecond for value in (start, end)):
        raise ValueError("Start and end must fall on UTC hour boundaries.")
    if end <= start or end - start > MAX_RANGE:
        raise ValueError("Select a range longer than zero and no longer than 31 days.")
    return start, end


def _empty_hour(hour: datetime) -> dict:
    return {"hour_start": hour.isoformat(), "total_visitors": 0, "registered_users": [], "unregistered_guests": 0}


def _recorded_hours(session: Session, start: datetime | None, end: datetime | None) -> Iterator[dict]:
    statement = select(UsageActivity).order_by(
        UsageActivity.hour_start, UsageActivity.first_seen_at, UsageActivity.browser_key, UsageActivity.identity_key,
    )
    if start is not None:
        statement = statement.where(UsageActivity.hour_start >= start)
    if end is not None:
        statement = statement.where(UsageActivity.hour_start < end)
    records = session.scalars(statement.execution_options(yield_per=1000))
    for hour, entries in groupby(records, key=lambda entry: ensure_utc(entry.hour_start)):
        users: dict[str, dict] = {}
        anonymous_browsers: set[str] = set()
        authenticated_browsers: set[str] = set()
        for entry in entries:
            if entry.identity_key == ANONYMOUS:
                anonymous_browsers.add(entry.browser_key)
            else:
                authenticated_browsers.add(entry.browser_key)
                users.setdefault(entry.identity_key, {
                    "id": entry.identity_key, "display_name": entry.display_name, "email": entry.email,
                })
        guests = len(anonymous_browsers - authenticated_browsers)
        yield {
            "hour_start": hour.isoformat(),
            "total_visitors": len(users) + guests,
            "registered_users": sorted(users.values(), key=lambda user: (user["display_name"].casefold(), user["email"], user["id"])),
            "unregistered_guests": guests,
        }


def usage_hours(session: Session, start: datetime | None, end: datetime | None) -> Iterator[dict]:
    cursor = start
    for hour in _recorded_hours(session, start, end):
        recorded_at = datetime.fromisoformat(hour["hour_start"])
        if cursor is not None:
            while cursor < recorded_at:
                yield _empty_hour(cursor)
                cursor += HOUR
            cursor = recorded_at + HOUR
        yield hour
    if cursor is not None and end is not None:
        while cursor < end:
            yield _empty_hour(cursor)
            cursor += HOUR


def get_usage_report(session: Session, start: datetime, end: datetime) -> dict:
    first = session.scalar(select(UsageActivity.first_seen_at).order_by(
        UsageActivity.hour_start, UsageActivity.first_seen_at,
    ).limit(1))
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "first_recorded_at": ensure_utc(first).isoformat() if first else None,
        "generated_at": utc_now().isoformat(),
        "hours": list(usage_hours(session, start, end)),
    }


def export_usage_csv(
    session_factory: sessionmaker[Session], start: datetime | None, end: datetime | None,
) -> Iterator[str]:
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["hour_start_utc", "hour_end_utc", "total_visitors", "registered_user_count", "unregistered_guests", "registered_users_json"])
    yield buffer.getvalue()
    with session_factory() as session:
        for hour in usage_hours(session, start, end):
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow([
                hour["hour_start"],
                (datetime.fromisoformat(hour["hour_start"]) + HOUR).isoformat(),
                hour["total_visitors"],
                len(hour["registered_users"]),
                hour["unregistered_guests"],
                json.dumps(hour["registered_users"], ensure_ascii=False),
            ])
            yield buffer.getvalue()
