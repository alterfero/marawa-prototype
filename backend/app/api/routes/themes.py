from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.api.errors import api_error
from app.db.models import ThemeConfirmationStatus, UserRole
from app.services.auth import AuthSessionContext
from app.services.themes import (
    ThemeDeletionConflictError,
    ThemeLookupNotFoundError,
    ThemeMutationValidationError,
    ThemeVersionConflictError,
    delete_theme,
    ensure_canonical_theme,
    get_theme_detail,
    list_canonical_themes,
    merge_unconfirmed_theme,
    set_theme_confirmation_status,
    update_theme_text,
)


class ThemeListItemResponse(BaseModel):
    id: str
    version: int
    text: str
    confirmation_status: ThemeConfirmationStatus
    story_count: int
    story_ids: list[str] = Field(default_factory=list)


class CreateThemeRequest(BaseModel):
    text: str = Field(min_length=1)


class CreateThemeResponse(BaseModel):
    theme: ThemeListItemResponse
    created: bool


class ThemeStorySummaryResponse(BaseModel):
    id: str
    title: str
    source_row_number: int | None


class ThemeDetailResponse(ThemeListItemResponse):
    stories: list[ThemeStorySummaryResponse]


class UpdateThemeRequest(BaseModel):
    expected_theme_version: int = Field(ge=1)
    text: str = Field(min_length=1)


class UpdateThemeConfirmationRequest(BaseModel):
    expected_theme_version: int = Field(ge=1)
    confirmation_status: ThemeConfirmationStatus


class UpdateThemeResponse(BaseModel):
    theme: ThemeListItemResponse


class UpdateThemeConfirmationResponse(BaseModel):
    theme: ThemeListItemResponse


class DeleteThemeResponse(BaseModel):
    deleted_theme_id: str
    affected_story_count: int
    dataset_version: int


class MergeThemeRequest(BaseModel):
    expected_source_theme_version: int = Field(ge=1)
    target_theme_id: str


class MergeThemeResponse(BaseModel):
    source_theme_id: str
    target_theme: ThemeListItemResponse
    affected_story_count: int
    dataset_version: int


router = APIRouter(prefix="/themes", tags=["themes"])


@router.get("", response_model=list[ThemeListItemResponse])
def read_themes(
    unused_only: bool = Query(default=False),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=5000),
    include_story_ids: bool = Query(default=False),
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
) -> list[ThemeListItemResponse]:
    return [
        ThemeListItemResponse(**item)
        for item in list_canonical_themes(
            session,
            unused_only=unused_only,
            query=q,
            limit=limit,
            include_story_ids=include_story_ids,
        )
    ]


@router.post("", response_model=CreateThemeResponse)
def create_canonical_theme(
    payload: CreateThemeRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.CONTRIBUTOR)),
    session: Session = Depends(get_db_session),
) -> CreateThemeResponse:
    try:
        theme, created = ensure_canonical_theme(session, payload.text, actor_user_id=auth_context.user.id)
    except ThemeMutationValidationError as exc:
        raise api_error(400, "theme_mutation_invalid", str(exc)) from exc
    return CreateThemeResponse(theme=ThemeListItemResponse(**theme), created=created)


@router.get("/{theme_id}", response_model=ThemeDetailResponse)
def read_theme_detail(
    theme_id: str,
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
) -> ThemeDetailResponse:
    try:
        return ThemeDetailResponse(**get_theme_detail(session, theme_id))
    except ThemeLookupNotFoundError as exc:
        raise api_error(404, "theme_not_found", str(exc)) from exc


@router.put("/{theme_id}", response_model=UpdateThemeResponse)
def update_canonical_theme(
    theme_id: str,
    payload: UpdateThemeRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> UpdateThemeResponse:
    try:
        theme = update_theme_text(
            session,
            theme_id,
            expected_version=payload.expected_theme_version,
            text=payload.text,
            actor_user_id=auth_context.user.id,
        )
    except ThemeLookupNotFoundError as exc:
        raise api_error(404, "theme_not_found", str(exc)) from exc
    except ThemeVersionConflictError as exc:
        raise api_error(409, "theme_version_conflict", str(exc)) from exc
    except ThemeMutationValidationError as exc:
        raise api_error(400, "theme_mutation_invalid", str(exc)) from exc
    return UpdateThemeResponse(theme=ThemeListItemResponse(**theme))


@router.put("/{theme_id}/confirmation", response_model=UpdateThemeConfirmationResponse)
def update_theme_confirmation(
    theme_id: str,
    payload: UpdateThemeConfirmationRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> UpdateThemeConfirmationResponse:
    try:
        theme = set_theme_confirmation_status(
            session,
            theme_id,
            expected_version=payload.expected_theme_version,
            confirmation_status=payload.confirmation_status,
            actor_user_id=auth_context.user.id,
        )
    except ThemeLookupNotFoundError as exc:
        raise api_error(404, "theme_not_found", str(exc)) from exc
    except ThemeVersionConflictError as exc:
        raise api_error(409, "theme_version_conflict", str(exc)) from exc
    except ThemeMutationValidationError as exc:
        raise api_error(400, "theme_mutation_invalid", str(exc)) from exc
    return UpdateThemeConfirmationResponse(theme=ThemeListItemResponse(**theme))


@router.post("/{theme_id}/merge", response_model=MergeThemeResponse)
def merge_theme(
    theme_id: str,
    payload: MergeThemeRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> MergeThemeResponse:
    try:
        dataset, summary = merge_unconfirmed_theme(
            session,
            theme_id,
            target_theme_id=payload.target_theme_id,
            expected_source_version=payload.expected_source_theme_version,
            actor_user_id=auth_context.user.id,
        )
    except ThemeLookupNotFoundError as exc:
        raise api_error(404, "theme_not_found", str(exc)) from exc
    except ThemeVersionConflictError as exc:
        raise api_error(409, "theme_version_conflict", str(exc)) from exc
    except ThemeMutationValidationError as exc:
        raise api_error(400, "theme_merge_invalid", str(exc)) from exc
    return MergeThemeResponse(
        source_theme_id=summary["source_theme_id"],
        target_theme=ThemeListItemResponse(**summary["target_theme"]),
        affected_story_count=summary["affected_story_count"],
        dataset_version=dataset.version,
    )


@router.delete("/{theme_id}", response_model=DeleteThemeResponse)
def remove_canonical_theme(
    theme_id: str,
    expected_theme_version: int = Query(ge=1),
    remove_from_all_stories: bool = Query(default=False),
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> DeleteThemeResponse:
    try:
        dataset, summary = delete_theme(
            session,
            theme_id,
            expected_version=expected_theme_version,
            remove_from_all_stories=remove_from_all_stories,
            actor_user_id=auth_context.user.id,
        )
    except ThemeLookupNotFoundError as exc:
        raise api_error(404, "theme_not_found", str(exc)) from exc
    except ThemeVersionConflictError as exc:
        raise api_error(409, "theme_version_conflict", str(exc)) from exc
    except ThemeDeletionConflictError as exc:
        raise api_error(409, "theme_delete_conflict", str(exc)) from exc
    except ThemeMutationValidationError as exc:
        raise api_error(400, "theme_mutation_invalid", str(exc)) from exc
    return DeleteThemeResponse(
        deleted_theme_id=summary["deleted_theme_id"],
        affected_story_count=summary["affected_story_count"],
        dataset_version=dataset.version,
    )
