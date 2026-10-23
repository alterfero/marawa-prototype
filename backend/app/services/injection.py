"""Durable preview, duplicate review, and atomic application of CSV injections."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.csv_schema import CSV_COLUMNS, DATE_OF_RECORDING_FIELD, MARAWA_STORY_METADATA_FIELD, MARAWA_TERM_CATALOG_FIELD, TROPE_FIELD, THEME_FIELD, KEYWORD_FIELD
from app.core.dates import parse_year_interval
from app.core.injection import TITLE_FIELDS, TERM_FIELDS, duplicate_keys, inspect_csv, prepare_injection_titles
from app.core.parsing import normalize_text, serialize_tropes, serialize_themes, serialize_keywords
from app.db.models import CSVInjection, Dataset, DatasetStatus, Story, StoryCompleteness, StoryTropeOrigin, AssignmentStatus, ReviewType, TermKind, TermReviewStatus, TropeConfirmationStatus, ThemeConfirmationStatus, KeywordConfirmationStatus, StoryTrope, StoryTheme, StoryKeyword, Trope, Theme, Keyword
from app.services.audit import record_audit_event
from app.services.csv_io import CSVImportValidationError, _CSVImportRow, _parse_full_export_catalog, _parse_full_story_metadata, _row_hash, _full_story_metadata, _ordered_trope_links, _ordered_theme_links, _ordered_keyword_links
from app.services.jobs import queue_job
from app.services.maintenance import get_dataset_maintenance_status
from app.services.reviews import queue_story_field_review_item, queue_story_trope_review_item, queue_story_keyword_review_item, queue_term_review_item


_INJECTION_VALIDATION_VERSION = 4


class InjectionError(ValueError):
    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _active_dataset(session: Session) -> Dataset:
    dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if dataset is None:
        raise InjectionError("Create and rebuild an initial dataset before injecting a CSV.")
    maintenance = get_dataset_maintenance_status(session)
    if maintenance["active"]:
        raise InjectionError(maintenance["message"])
    return dataset


def _story_fields(story: Story) -> dict:
    fields = {c: story.fields_json.get(c, "") for c in CSV_COLUMNS}
    fields[TROPE_FIELD] = serialize_tropes([l.trope.text for l in _ordered_trope_links(story)])
    fields[THEME_FIELD] = serialize_themes([l.theme.text for l in _ordered_theme_links(story)])
    fields[KEYWORD_FIELD] = serialize_keywords([l.keyword.text for l in _ordered_keyword_links(story)])
    return fields


def _stories(session, dataset_id):
    return list(session.scalars(select(Story).where(Story.dataset_id == dataset_id).options(
        selectinload(Story.trope_links).selectinload(StoryTrope.trope),
        selectinload(Story.theme_links).selectinload(StoryTheme.theme),
        selectinload(Story.keyword_links).selectinload(StoryKeyword.keyword),
    ).order_by(Story.created_at, Story.id)).all())


def preview_injection(session: Session, content: bytes, *, source_filename: str | None = None) -> dict:
    dataset = _active_dataset(session)
    version = dataset.version
    parsed = inspect_csv(content)
    catalog = None
    # Imported source numbers have no meaning in the receiving dataset. Ignore
    # them even when missing or malformed; all other full-export metadata is checked.
    metadata_rows = [
        _CSVImportRow(
            r["row_number"], r["fields"],
            {**r["metadata"], "source_row_number": None} if r["metadata"] is not None else None,
            r["catalog"],
        )
        for r in parsed.rows
    ]
    if parsed.is_full and metadata_rows:
        try:
            catalog = _parse_full_export_catalog(metadata_rows)
        except CSVImportValidationError as exc:
            parsed.issue("error", "term_catalog", str(exc), "Restore the term catalog on the first story row from a full export, including all assigned terms.", row=parsed.rows[0], column=MARAWA_TERM_CATALOG_FIELD)
    stories = _stories(session, dataset.id)
    index = defaultdict(dict)
    used_titles = set()
    reserved_titles = {
        normalize_text(row["fields"][column])
        for row in parsed.rows for column in TITLE_FIELDS if row["fields"][column]
    }
    for story in stories:
        fields = _story_fields(story)
        used_titles.update(normalize_text(fields[column]) for column in TITLE_FIELDS if fields[column])
        candidate = {"key": f"story:{story.id}", "kind": "database", "story_id": story.id, "row_number": None,
                     "version": story.version, "fields": fields, "metadata": _full_story_metadata(story, _ordered_trope_links(story))}
        for key in duplicate_keys(fields):
            index[key][candidate["key"]] = candidate
    plan_rows = []
    for row, metadata_row in zip(parsed.rows, metadata_rows):
        terms = {collection: splitter(row["fields"][column]) for collection, column, splitter in TERM_FIELDS}
        assignments = [{"origin": "csv_import", "status": "validated"} for _ in terms["tropes"]]
        if parsed.is_full:
            try:
                _, _, _, pairs = _parse_full_story_metadata(metadata_row, terms["tropes"])
                assignments = [{"origin": origin.value, "status": status.value} for origin, status in pairs]
            except CSVImportValidationError as exc:
                parsed.issue("error", "story_metadata", str(exc), "Correct the metadata schema, completeness, and ordered trope assignments to match the story cells, or restore a fresh export.", row=row, column=MARAWA_STORY_METADATA_FIELD)
            if catalog is not None:
                for collection, column, _ in TERM_FIELDS:
                    for term in terms[collection]:
                        if normalize_text(term) not in catalog[collection]:
                            parsed.issue("error", "term_missing_from_catalog", f"Assigned {collection[:-1]} {term!r} is absent from the term catalog.", "Restore this term in the catalog with valid statuses, or correct the assignment cell.", row=row, column=column, value=term)
        candidates = {}
        input_titles = {column: row["fields"][column] for column in TITLE_FIELDS}
        keys = duplicate_keys(row["fields"])
        for key in keys:
            for candidate_key, candidate in index[key].items():
                if candidate_key not in candidates:
                    candidates[candidate_key] = {**candidate, "reasons": ["Same normalized title and abstract"]}
        row["candidates"] = list(candidates.values())
        row["fields"] = prepare_injection_titles(
            row["fields"], candidates=row["candidates"], used_titles=used_titles, reserved_titles=reserved_titles,
        )
        used_titles.update(normalize_text(row["fields"][column]) for column in TITLE_FIELDS if row["fields"][column])
        row["completeness"] = StoryCompleteness.INCOMPLETE.value
        if row["metadata"] is not None:
            row["metadata"] = {**row["metadata"], "completeness": StoryCompleteness.INCOMPLETE.value}
        row["terms"] = terms
        row["assignments"] = assignments
        plan_rows.append(row)
        candidate = {"key": f'row:{row["row_number"]}', "kind": "file", "story_id": None, "row_number": row["row_number"], "version": None, "fields": row["fields"], "metadata": row["metadata"], "input_titles": input_titles}
        # Match both the original CSV title and its effective numbered title.
        for key in dict.fromkeys([*keys, *duplicate_keys(row["fields"])]):
            index[key][candidate["key"]] = candidate
    report = {
        "format": "full" if parsed.is_full else "legacy", "source_filename": source_filename,
        "dataset_version": version, "row_count": parsed.row_count,
        "error_count": sum(i["severity"] == "error" for i in parsed.issues),
        "warning_count": sum(i["severity"] == "warning" for i in parsed.issues),
        "duplicate_count": sum(bool(r["candidates"]) for r in plan_rows),
        "issues": parsed.issues,
        "rows": [{k: r[k] for k in ("row_number", "line_start", "line_end", "fields", "metadata", "candidates")} for r in plan_rows],
    }
    draft = CSVInjection(dataset_id=dataset.id, dataset_version=version, source_filename=source_filename,
                         plan_json={"rows": plan_rows, "catalog": catalog, "validation_version": _INJECTION_VALIDATION_VERSION}, report_json=report)
    session.add(draft)
    session.commit()
    return {"id": draft.id, **report}


def _require_current_review(draft: CSVInjection) -> None:
    if draft.result_json is None and draft.plan_json.get("validation_version") != _INJECTION_VALIDATION_VERSION:
        raise InjectionError("Injection validation rules have changed. Select the CSV and check it again.")


def _without_obsolete_warnings(report: dict) -> dict:
    """Keep saved reviews usable when their only warnings were term statuses."""
    issues = [issue for issue in report["issues"] if issue["code"] != "existing_term_status"]
    return {
        **report,
        "issues": issues,
        "warning_count": sum(issue["severity"] == "warning" for issue in issues),
    }


def get_injection(session: Session, injection_id: str) -> dict:
    draft = session.get(CSVInjection, injection_id)
    if draft is None:
        raise InjectionError("Injection review not found.", status_code=404)
    _require_current_review(draft)
    return {"id": draft.id, **_without_obsolete_warnings(draft.report_json), "result": draft.result_json}


def _review_snapshot(story: Story) -> dict:
    return {
        "fields": _story_fields(story),
        "tropes": {link.trope.id: {
            "id": link.trope.id, "text": link.trope.text, "position": link.position,
            "origin": link.origin.value, "status": link.status.value,
        } for link in _ordered_trope_links(story)},
        "keywords": {link.keyword.id: {
            "id": link.keyword.id, "text": link.keyword.text, "position": link.position,
        } for link in _ordered_keyword_links(story)},
    }


def _queue_injected_story_review(session, *, story, before, actor_user_id, injection_id, created_theme_ids):
    """Review final injected content with the same reversible items as contributor edits."""
    current = _review_snapshot(story)
    previous = before or {"fields": {}, "tropes": {}, "keywords": {}}
    common = {
        "dataset_id": story.dataset_id, "story": story, "actor_user_id": actor_user_id,
        "review_type": ReviewType.STORY_CREATED if before is None else ReviewType.STORY_UPDATED,
    }
    items = []
    for column in CSV_COLUMNS:
        if column in {TROPE_FIELD, KEYWORD_FIELD}:
            continue
        old_value = previous["fields"].get(column, "")
        value = current["fields"].get(column, "")
        if value or old_value:
            items.append(queue_story_field_review_item(
                session, **common, field_name=column, previous_value=old_value, current_value=value,
                include_unchanged=True, created_theme_ids=created_theme_ids if column == THEME_FIELD else None,
            ))
    for collection, kind, queue in (
        ("tropes", "trope", queue_story_trope_review_item),
        ("keywords", "keyword", queue_story_keyword_review_item),
    ):
        old_terms, terms = previous[collection], current[collection]
        for term_id, term in old_terms.items():
            if term_id not in terms:
                items.append(queue(session, **common, assignment_action="deleted",
                                   position=term["position"], **{f"previous_{kind}": term}))
        for term_id, term in terms.items():
            if term_id not in old_terms:
                items.append(queue(session, **common, assignment_action="added",
                                   position=term["position"], **{f"current_{kind}": term}))
            elif kind == "trope" and any(term[key] != old_terms[term_id][key] for key in ("origin", "status")):
                items.append(queue(session, **common, assignment_action="validated", position=term["position"],
                                   previous_trope=old_terms[term_id], current_trope=term))
    if not items:
        # A row may become empty after ignored recording dates are cleared.
        items.append(queue_story_field_review_item(
            session, **common, field_name="Story title (Eng)", previous_value="", current_value="",
            include_unchanged=True,
        ))
    for item in items:
        item.metadata_json = {**item.metadata_json, "injection_id": injection_id}


def apply_injection(session: Session, injection_id: str, *, decisions: list[dict], acknowledge_warnings: bool, actor_user_id: str | None = None) -> dict:
    """All writes, the result, audit event, and rebuild request commit together."""
    try:
        return _apply_injection(session, injection_id, decisions=decisions, acknowledge_warnings=acknowledge_warnings, actor_user_id=actor_user_id)
    except Exception:
        session.rollback()
        raise


def _apply_injection(session, injection_id, *, decisions, acknowledge_warnings, actor_user_id):
    draft = session.scalar(select(CSVInjection).where(CSVInjection.id == injection_id).with_for_update().execution_options(populate_existing=True))
    if draft is None:
        raise InjectionError("Injection review not found.", status_code=404)
    if draft.result_json is not None:
        return draft.result_json
    _require_current_review(draft)
    report = _without_obsolete_warnings(draft.report_json)
    if report["error_count"]:
        raise InjectionError("Correct all CSV errors and check the file again before injecting.", status_code=400)
    if report["warning_count"] and not acknowledge_warnings:
        raise InjectionError("Acknowledge the validation warnings before injecting.", status_code=400)
    dataset = _active_dataset(session)
    if dataset.id != draft.dataset_id or dataset.version != draft.dataset_version:
        raise InjectionError("The dataset changed after this review. Check the CSV again and review the updated duplicates.")
    rows = draft.plan_json["rows"]
    by_row = {d["row_number"]: d for d in decisions}
    duplicate_rows = {r["row_number"] for r in rows if r["candidates"]}
    if len(by_row) != len(decisions) or set(by_row) != duplicate_rows:
        raise InjectionError("Choose a resolution for every duplicate entry, with no repeated or extra decisions.", status_code=400)
    for row in rows:
        decision = by_row.get(row["row_number"])
        if decision and (decision["action"] not in {"keep_both", "keep_original", "keep_injected"} or
                         (decision["action"] != "keep_both" and decision.get("target_key") not in {c["key"] for c in row["candidates"]})):
            raise InjectionError(f'Choose a valid original and resolution for data row {row["row_number"]}.', status_code=400)
    # Compare-and-swap guards simultaneous applications of the same or different reviews.
    claimed = session.execute(update(Dataset).where(Dataset.id == draft.dataset_id, Dataset.version == draft.dataset_version,
                                                   Dataset.status == DatasetStatus.ACTIVE).values(version=Dataset.version + 1))
    if claimed.rowcount != 1:
        raise InjectionError("The dataset changed during application. Check the CSV again.")
    caches = {}
    catalog = draft.plan_json["catalog"]
    new_term_count = 0
    new_terms = []
    for collection, model in (("tropes", Trope), ("themes", Theme), ("keywords", Keyword)):
        cache = {t.normalized_text: t for t in session.scalars(select(model).where(model.dataset_id == dataset.id))}
        desired = dict(catalog[collection]) if catalog else {}
        for row in rows:
            decision = by_row.get(row["row_number"], {})
            if decision.get("action") == "keep_original":
                continue
            for term in row["terms"][collection]:
                desired.setdefault(normalize_text(term), {"text": term})
        for marker, item in desired.items():
            if marker not in cache:
                attributes = dict(item)
                if "confirmation_status" in attributes:
                    enum = {"tropes": TropeConfirmationStatus, "themes": ThemeConfirmationStatus, "keywords": KeywordConfirmationStatus}[collection]
                    attributes["confirmation_status"] = enum(attributes["confirmation_status"])
                if collection in {"tropes", "keywords"}:
                    attributes["review_status"] = TermReviewStatus.PENDING_REVIEW
                cache[marker] = model(dataset_id=dataset.id, created_by_user_id=actor_user_id, **attributes)
                session.add(cache[marker])
                new_terms.append((collection, cache[marker]))
                new_term_count += 1
        caches[collection] = cache
    session.flush()
    for collection, term in new_terms:
        if collection in {"tropes", "keywords"}:
            item = queue_term_review_item(
                session, dataset_id=dataset.id, term_kind=TermKind.TROPE if collection == "tropes" else TermKind.KEYWORD,
                subject_id=term.id, actor_user_id=actor_user_id, text=term.text,
            )
            item.metadata_json = {**item.metadata_json, "injection_id": draft.id}
    created_theme_ids = [term.id for collection, term in new_terms if collection == "themes"]
    next_source_number = (
        session.scalar(select(func.max(Story.source_row_number)).where(Story.dataset_id == dataset.id)) or 0
    ) + 1
    resolved = {}
    touched_story_ids = set()
    story_reviews = {}
    counts = {"added": 0, "updated": 0, "skipped": 0}
    changes = []
    for row in rows:
        number = row["row_number"]
        decision = by_row.get(number, {"action": "keep_both"})
        action = decision["action"]
        original = None
        if action != "keep_both":
            target = decision["target_key"]
            original = resolved.get(target) if target.startswith("row:") else session.scalar(
                select(Story).where(Story.id == target.removeprefix("story:")).with_for_update()
                .execution_options(populate_existing=True)
            )
            if original is None or original.dataset_id != dataset.id:
                raise InjectionError("An original story is no longer available. Check the CSV again.")
            if target.startswith("story:") and original.id not in touched_story_ids:
                expected = next(c["version"] for c in row["candidates"] if c["key"] == target)
                if original.version != expected:
                    raise InjectionError("An original story changed after review. Check the CSV again.")
        if action == "keep_original":
            resolved[f"row:{number}"] = original
            counts["skipped"] += 1
            continue
        story = original
        before = _review_snapshot(story) if story is not None and story.id not in story_reviews else None
        if story is None:
            story = Story(dataset_id=dataset.id, source_row_number=next_source_number)
            next_source_number += 1
            session.add(story)
            counts["added"] += 1
        else:
            changes.append({"story_id": story.id, "before_fields": _story_fields(story), "before_metadata": _full_story_metadata(story, _ordered_trope_links(story))})
            story.version += 1
            story.trope_links.clear()
            story.theme_links.clear()
            story.keyword_links.clear()
            session.flush()
            counts["updated"] += 1
        fields = dict(row["fields"])
        fields[TROPE_FIELD] = serialize_tropes(row["terms"]["tropes"])
        fields[THEME_FIELD] = serialize_themes(row["terms"]["themes"])
        fields[KEYWORD_FIELD] = serialize_keywords(row["terms"]["keywords"])
        story.fields_json = fields
        story.row_hash = _row_hash(fields)
        story.completeness = StoryCompleteness.INCOMPLETE
        interval = parse_year_interval(fields[DATE_OF_RECORDING_FIELD])
        story.recording_year_start = interval.year1 if interval else None
        story.recording_year_end = interval.year2 if interval else None
        for position, term in enumerate(row["terms"]["tropes"]):
            story.trope_links.append(StoryTrope(trope=caches["tropes"][normalize_text(term)], position=position, origin=StoryTropeOrigin(row["assignments"][position]["origin"]), status=AssignmentStatus(row["assignments"][position]["status"])))
        for position, term in enumerate(row["terms"]["themes"]):
            story.theme_links.append(StoryTheme(theme=caches["themes"][normalize_text(term)], position=position))
        for position, term in enumerate(row["terms"]["keywords"]):
            story.keyword_links.append(StoryKeyword(keyword=caches["keywords"][normalize_text(term)], position=position))
        session.flush()
        story_reviews.setdefault(story.id, (story, before))
        resolved[f"row:{number}"] = story
        touched_story_ids.add(story.id)
    for story, before in story_reviews.values():
        _queue_injected_story_review(
            session, story=story, before=before, actor_user_id=actor_user_id,
            injection_id=draft.id, created_theme_ids=[link.theme_id for link in story.theme_links if link.theme_id in created_theme_ids],
        )
    for collection, link, key in (("tropes", StoryTrope, StoryTrope.trope_id), ("themes", StoryTheme, StoryTheme.theme_id), ("keywords", StoryKeyword, StoryKeyword.keyword_id)):
        counts_by_term = dict(session.execute(select(key, func.count()).join(Story).where(Story.dataset_id == dataset.id).group_by(key)).all())
        for term in caches[collection].values():
            term.cached_story_count = counts_by_term.get(term.id, 0)
    changed = counts["added"] or counts["updated"] or new_term_count
    job = queue_job(session, job_type="full_rebuild", dataset_id=dataset.id, payload={"reason": "csv_injection", "injection_id": draft.id}) if changed else None
    if not changed:
        session.execute(update(Dataset).where(Dataset.id == dataset.id).values(version=draft.dataset_version))
    result = {"id": draft.id, "status": "applied", **counts, "new_terms": new_term_count,
              "dataset_version": draft.dataset_version + int(bool(changed)), "job_id": job.id if job else None}
    draft.result_json = result
    record_audit_event(session, event_type="dataset.injected", actor_user_id=actor_user_id, dataset_id=dataset.id,
                       subject_table="csv_injections", subject_id=draft.id,
                       payload={**result, "source_filename": draft.source_filename, "decisions": decisions, "replaced_stories": changes})
    session.commit()
    return result
