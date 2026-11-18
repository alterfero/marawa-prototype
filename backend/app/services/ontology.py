"""Curation of the active dataset's specificity levels and generalizations."""
from __future__ import annotations

from collections import Counter, defaultdict
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, aliased

from app.compute.specificity import classify
from app.core.ontology import ACTIVE_LINKS, candidate_links, validate_graph
from app.db.models import Dataset, DatasetStatus, Trope, TropeParent
from app.services.audit import record_audit_event


class OntologyConflict(ValueError):
    pass


class OntologyNotFound(ValueError):
    pass


def _active(session: Session) -> Dataset:
    dataset = session.scalar(select(Dataset).where(Dataset.status == DatasetStatus.ACTIVE))
    if dataset is None:
        raise OntologyNotFound("There is no active dataset.")
    return dataset


def _claim_version(session: Session, dataset_id: str, version: int) -> None:
    # UPDATE is the first write and serializes graph validation in both SQLite and PostgreSQL.
    result = session.execute(update(Dataset).where(
        Dataset.id == dataset_id, Dataset.version == version, Dataset.status == DatasetStatus.ACTIVE,
    ).values(version=Dataset.version + 1))
    if result.rowcount != 1:
        raise OntologyConflict("The active dataset changed. Reload the ontology and try again.")


def _trope(session: Session, dataset_id: str, trope_id: str) -> Trope:
    trope = session.scalar(select(Trope).where(Trope.id == trope_id, Trope.dataset_id == dataset_id).with_for_update())
    if trope is None:
        raise OntologyNotFound("Trope not found in this dataset.")
    return trope


def _edges(session: Session, dataset_id: str) -> list[tuple[str, str, str]]:
    return [tuple(row) for row in session.execute(select(
        TropeParent.child_id, TropeParent.parent_id, TropeParent.status
    ).where(TropeParent.dataset_id == dataset_id))]


def _validate(session: Session, dataset_id: str) -> None:
    session.flush()
    levels = dict(session.execute(select(Trope.id, Trope.specificity_level)
                                  .where(Trope.dataset_id == dataset_id)).all())
    validate_graph(levels, _edges(session, dataset_id))


def related_ontology_ids(session: Session, trope: Trope, allowed_levels: list[int]) -> set[str]:
    """Follow parents and children separately so a shared parent never implies an equivalent trope."""
    child, parent = aliased(Trope), aliased(Trope)
    edges = session.execute(select(TropeParent.child_id, TropeParent.parent_id)
        .join(child, child.id == TropeParent.child_id).join(parent, parent.id == TropeParent.parent_id)
        .where(TropeParent.dataset_id == trope.dataset_id, TropeParent.status.in_(ACTIVE_LINKS),
               child.specificity_level.in_(allowed_levels), parent.specificity_level.in_(allowed_levels)))
    parents, children = defaultdict(set), defaultdict(set)
    for c, p in edges:
        parents[c].add(p)
        children[p].add(c)
    relatives = set()
    for direction in (parents, children):
        visited = {trope.id}
        todo = [trope.id]
        while todo:
            following = direction[todo.pop()] - visited
            visited.update(following)
            todo.extend(following)
        relatives.update(visited)
    return relatives


def read_graph(session: Session, *, review: bool = False) -> dict:
    dataset = _active(session)
    rows = session.execute(select(
        Trope.id, Trope.ontology_id, Trope.text, Trope.specificity_level, Trope.specificity_source,
        Trope.confirmation_status,
    ).where(Trope.dataset_id == dataset.id).order_by(Trope.normalized_text))
    nodes = [dict(row._mapping) for row in rows]
    edges = _edges(session, dataset.id)
    children = {child for child, _, status in edges if status in ACTIVE_LINKS}
    return {
        "dataset_id": dataset.id, "version": dataset.version,
        "root": {"id": "root", "specificity_level": 0, "text": "Something happens."},
        "root_children": [node["id"] for node in nodes if node["id"] not in children],
        "tropes": nodes,
        "links": [{"child_id": c, "parent_id": p, "status": st} for c, p, st in edges
                  if st in ACTIVE_LINKS or (review and st == "pending")],
    }


def set_level(session: Session, *, dataset_id: str, expected_version: int, trope_id: str,
              level: int, actor_id: str) -> None:
    _claim_version(session, dataset_id, expected_version)
    trope = _trope(session, dataset_id, trope_id)
    if type(level) is not int or not 1 <= level <= 4:
        raise ValueError("Story tropes use specificity levels 1 through 4.")
    before = [trope.specificity_level, trope.specificity_source]
    trope.specificity_level, trope.specificity_source = level, "curator"
    trope.version += 1
    _validate(session, dataset_id)
    record_audit_event(session, event_type="trope.specificity_updated", dataset_id=dataset_id,
                       actor_user_id=actor_id, subject_table="tropes", subject_id=trope_id,
                       payload={"previous": before, "level": level, "dataset_version": expected_version + 1})
    session.commit()


def set_link(session: Session, *, dataset_id: str, expected_version: int, child_id: str,
             parent_id: str, status: str, actor_id: str) -> None:
    _claim_version(session, dataset_id, expected_version)
    if child_id == parent_id or status not in ("approved", "rejected"):
        raise ValueError("Choose a different broader trope and a valid link decision.")
    child = _trope(session, dataset_id, child_id)
    parent = _trope(session, dataset_id, parent_id)
    link = session.get(TropeParent, (dataset_id, child_id, parent_id))
    before = link.status if link else None
    if link is None:
        if status == "rejected":
            raise OntologyNotFound("This link no longer exists.")
        session.add(TropeParent(dataset_id=dataset_id, child_id=child_id, parent_id=parent_id, status=status))
    else:
        link.status = status
    child.version += 1
    parent.version += 1
    _validate(session, dataset_id)
    record_audit_event(session, event_type="trope.ontology_link_updated", dataset_id=dataset_id,
                       actor_user_id=actor_id, subject_table="tropes", subject_id=child_id,
                       payload={"parent_id": parent_id, "previous": before, "status": status, "dataset_version": expected_version + 1})
    session.commit()


def incident_links(trope_id):
    return or_(TropeParent.child_id == trope_id, TropeParent.parent_id == trope_id)


def review_reworded_links(session: Session, trope_id: str) -> None:
    session.execute(update(TropeParent).where(
        incident_links(trope_id), TropeParent.status.in_(ACTIVE_LINKS)
    ).values(status="pending"))


def transfer_merge_links(session: Session, source_id: str, target_id: str) -> None:
    """Save possible generalizations for review when a curation action merges two tropes."""
    links = session.scalars(select(TropeParent).where(
        incident_links(source_id), TropeParent.status != "rejected"
    )).all()
    for link in links:
        child = target_id if link.child_id == source_id else link.child_id
        parent = target_id if link.parent_id == source_id else link.parent_id
        if child != parent and session.get(TropeParent, (link.dataset_id, child, parent)) is None:
            session.add(TropeParent(dataset_id=link.dataset_id, child_id=child, parent_id=parent, status="pending"))


def populate_ontology(session: Session, dataset_id: str) -> dict:
    # Acquire a dataset write lock before reading graph state, including on SQLite.
    session.execute(update(Dataset).where(Dataset.id == dataset_id).values(version=Dataset.version))
    terms = {t.normalized_text: t for t in session.scalars(select(Trope).where(Trope.dataset_id == dataset_id))}
    filled = 0
    for t in terms.values():
        if t.specificity_level is None:
            t.specificity_level, t.specificity_source = classify(t.text)
            t.version += 1
            filled += 1
    edges = _edges(session, dataset_id)
    decisions = {(c, p) for c, p, _ in edges}
    # An existing curator path in the reverse direction can invalidate an inferred edge.
    adjacency: dict[str, set[str]] = {}
    for child, parent, status in edges:
        if status in ACTIVE_LINKS:
            adjacency.setdefault(child, set()).add(parent)
    added = Counter()
    for (child_text, parent_text), status in candidate_links(set(terms)).items():
        child, parent = terms[child_text], terms[parent_text]
        if (child.id, parent.id) in decisions or parent.specificity_level > child.specificity_level:
            continue
        if status == "inferred":
            todo = [parent.id]
            visited = set()
            while todo:
                node = todo.pop()
                if node not in visited:
                    visited.add(node)
                    todo.extend(adjacency.get(node, ()))
            if child.id in visited:
                status = "pending"
            else:
                adjacency.setdefault(child.id, set()).add(parent.id)
        session.add(TropeParent(dataset_id=dataset_id, child_id=child.id, parent_id=parent.id, status=status))
        added[status] += 1
    if filled or added:
        session.execute(update(Dataset).where(Dataset.id == dataset_id).values(version=Dataset.version + 1))
        version = session.scalar(select(Dataset.version).where(Dataset.id == dataset_id))
        record_audit_event(session, event_type="trope.ontology_initialized", dataset_id=dataset_id,
                           payload={"levels_filled": filled, "links_added": dict(added), "dataset_version": version})
    session.flush()
    return {"trope_count": len(terms), "levels_filled": filled, "links_added": dict(added),
            "levels": dict(Counter(t.specificity_level for t in terms.values())),
            "sources": dict(Counter(t.specificity_source for t in terms.values()))}
