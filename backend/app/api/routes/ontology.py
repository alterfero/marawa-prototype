from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.api.errors import api_error
from app.db.models import UserRole
from app.services.auth import AuthSessionContext
from app.services.ontology import OntologyConflict, OntologyNotFound, read_graph, set_level, set_link

router = APIRouter(prefix="/ontology", tags=["ontology"])


class OntologyEdit(BaseModel):
    dataset_id: str
    expected_version: int = Field(ge=1)


class LevelEdit(OntologyEdit):
    trope_id: str
    level: int = Field(strict=True, ge=1, le=4)


class LinkEdit(OntologyEdit):
    child_id: str
    parent_id: str
    status: Literal["approved", "rejected"]


@contextmanager
def ontology_errors(session: Session):
    try:
        yield
    except (OntologyConflict, OntologyNotFound, ValueError) as exc:
        session.rollback()
        status = 409 if isinstance(exc, OntologyConflict) else 404 if isinstance(exc, OntologyNotFound) else 422
        raise api_error(status, "ontology_edit" if status != 404 else "ontology_not_found", str(exc)) from exc


@router.get("")
def get_ontology(auth: AuthSessionContext = Depends(require_minimum_role(UserRole.GUEST)),
                 session: Session = Depends(get_db_session)):
    with ontology_errors(session):
        return read_graph(session, review=auth.user.role == UserRole.ADMIN)


@router.put("/level")
def update_level(edit: LevelEdit, auth: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
                 session: Session = Depends(get_db_session)):
    with ontology_errors(session):
        set_level(session, **edit.model_dump(), actor_id=auth.user.id)
    return {"version": edit.expected_version + 1}


@router.put("/link")
def update_link(edit: LinkEdit, auth: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
                session: Session = Depends(get_db_session)):
    with ontology_errors(session):
        set_link(session, **edit.model_dump(), actor_id=auth.user.id)
    return {"version": edit.expected_version + 1}
