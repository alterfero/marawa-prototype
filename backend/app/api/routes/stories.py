from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.db.models import StoryTropeOrigin, UserRole
from app.services.auth import AuthSessionContext
from app.services.stories import (
    ActiveDatasetNotFoundError,
    DatasetVersionConflictError,
    KeywordNotFoundError,
    StoryMutationValidationError,
    StoryKeywordNotFoundError,
    StoryNotFoundError,
    StoryTropeNotFoundError,
    StoryVersionConflictError,
    TropeNotFoundError,
    add_story_keyword,
    add_story_trope,
    create_story,
    delete_story_keyword,
    delete_story_trope,
    get_story_detail,
    get_story_keywords,
    get_story_tropes,
    list_active_stories,
    replace_story_keyword,
    replace_story_trope,
    update_story,
    validate_story_trope,
)


class JobSummaryResponse(BaseModel):
    id: str
    status: str
    job_type: str


class StorySummaryResponse(BaseModel):
    id: str
    dataset_id: str
    source_row_number: int | None
    version: int
    title: str
    territory: str
    summary: str
    has_location: bool
    trope_count: int
    keyword_count: int


class StoryListResponse(BaseModel):
    items: list[StorySummaryResponse]
    total: int


class StoryTropeResponse(BaseModel):
    id: str
    text: str
    story_count: int
    origin: str
    status: str
    position: int | None


class StoryKeywordResponse(BaseModel):
    id: str
    text: str
    position: int | None


class StoryDetailResponse(BaseModel):
    id: str
    dataset_id: str
    source_row_number: int | None
    version: int
    created_at: str
    updated_at: str
    fields: dict[str, str]
    tropes: list[StoryTropeResponse]
    keywords: list[StoryKeywordResponse]


class StoryTropesResponse(BaseModel):
    story_id: str
    story_version: int
    items: list[StoryTropeResponse]


class StoryKeywordsResponse(BaseModel):
    story_id: str
    story_version: int
    items: list[StoryKeywordResponse]


class CreateStoryRequest(BaseModel):
    expected_dataset_version: int
    fields: dict[str, str] = Field(default_factory=dict)
    tropes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class UpdateStoryRequest(BaseModel):
    expected_story_version: int
    fields: dict[str, str] = Field(default_factory=dict)


class AddStoryTropeRequest(BaseModel):
    expected_story_version: int
    trope_id: str | None = None
    text: str | None = None
    origin: StoryTropeOrigin = StoryTropeOrigin.HUMAN_ENTERED


class AddStoryKeywordRequest(BaseModel):
    expected_story_version: int
    keyword_id: str | None = None
    text: str | None = None


class ReplaceStoryTropeRequest(BaseModel):
    expected_story_version: int
    trope_id: str | None = None
    text: str | None = None


class ReplaceStoryKeywordRequest(BaseModel):
    expected_story_version: int
    keyword_id: str | None = None
    text: str | None = None


class StoryVersionRequest(BaseModel):
    expected_story_version: int


class StoryTropeMutationResponse(BaseModel):
    story_id: str
    story_version: int
    dataset_version: int
    trope: StoryTropeResponse
    queued_job: JobSummaryResponse | None


class StoryKeywordMutationResponse(BaseModel):
    story_id: str
    story_version: int
    dataset_version: int
    keyword: StoryKeywordResponse
    queued_job: JobSummaryResponse | None


class DeleteStoryTropeResponse(BaseModel):
    story_id: str
    story_version: int
    dataset_version: int
    deleted_trope_id: str
    queued_job: JobSummaryResponse | None


class DeleteStoryKeywordResponse(BaseModel):
    story_id: str
    story_version: int
    dataset_version: int
    deleted_keyword_id: str
    queued_job: JobSummaryResponse | None


class CreateStoryResponse(BaseModel):
    story: StoryDetailResponse
    dataset_version: int
    queued_job: JobSummaryResponse | None


router = APIRouter(prefix="/stories", tags=["stories"])


def _queued_job_summary(job) -> JobSummaryResponse | None:
    if job is None:
        return None
    return JobSummaryResponse(
        id=job.id,
        status=job.status.value,
        job_type=job.job_type,
    )


def _raise_story_service_error(exc: Exception) -> None:
    if isinstance(exc, StoryVersionConflictError):
        raise api_error(
            409,
            "story_version_conflict",
            str(exc),
            {"current_story_version": exc.current_story_version},
        ) from exc
    if isinstance(exc, DatasetVersionConflictError):
        raise api_error(
            409,
            "dataset_version_conflict",
            str(exc),
            {"current_dataset_version": exc.current_dataset_version},
        ) from exc
    if isinstance(exc, StoryNotFoundError):
        raise api_error(404, "story_not_found", str(exc)) from exc
    if isinstance(exc, ActiveDatasetNotFoundError):
        raise api_error(404, "active_dataset_not_found", str(exc)) from exc
    if isinstance(exc, StoryTropeNotFoundError):
        raise api_error(404, "story_trope_not_found", str(exc)) from exc
    if isinstance(exc, StoryKeywordNotFoundError):
        raise api_error(404, "story_keyword_not_found", str(exc)) from exc
    if isinstance(exc, TropeNotFoundError):
        raise api_error(404, "trope_not_found", str(exc)) from exc
    if isinstance(exc, KeywordNotFoundError):
        raise api_error(404, "keyword_not_found", str(exc)) from exc
    if isinstance(exc, StoryMutationValidationError):
        raise api_error(400, "story_mutation_invalid", str(exc)) from exc
    raise exc


@router.get("", response_model=StoryListResponse)
def read_stories(
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
) -> StoryListResponse:
    return StoryListResponse(**list_active_stories(session))


@router.post("", response_model=CreateStoryResponse, status_code=201)
def create_story_record(
    payload: CreateStoryRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.CONTRIBUTOR)),
    session: Session = Depends(get_db_session),
) -> CreateStoryResponse:
    try:
        story, dataset, job = create_story(
            session,
            expected_dataset_version=payload.expected_dataset_version,
            fields=payload.fields,
            tropes=payload.tropes,
            keywords=payload.keywords,
            actor_user_id=auth_context.user.id,
            actor_role=auth_context.user.role,
        )
    except Exception as exc:
        _raise_story_service_error(exc)

    return CreateStoryResponse(
        story=StoryDetailResponse(**get_story_detail(session, story.id)),
        dataset_version=dataset.version,
        queued_job=_queued_job_summary(job),
    )


@router.patch("/{story_id}", response_model=CreateStoryResponse)
def update_story_record(
    story_id: str,
    payload: UpdateStoryRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.CONTRIBUTOR)),
    session: Session = Depends(get_db_session),
) -> CreateStoryResponse:
    try:
        story, dataset, job = update_story(
            session,
            story_id,
            expected_story_version=payload.expected_story_version,
            fields=payload.fields,
            actor_user_id=auth_context.user.id,
            actor_role=auth_context.user.role,
        )
    except Exception as exc:
        _raise_story_service_error(exc)

    return CreateStoryResponse(
        story=StoryDetailResponse(**get_story_detail(session, story.id)),
        dataset_version=dataset.version,
        queued_job=_queued_job_summary(job),
    )


@router.get("/{story_id}", response_model=StoryDetailResponse)
def read_story(
    story_id: str,
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
) -> StoryDetailResponse:
    try:
        return StoryDetailResponse(**get_story_detail(session, story_id))
    except Exception as exc:
        _raise_story_service_error(exc)


@router.get("/{story_id}/tropes", response_model=StoryTropesResponse)
def read_story_tropes(
    story_id: str,
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
) -> StoryTropesResponse:
    try:
        return StoryTropesResponse(**get_story_tropes(session, story_id))
    except Exception as exc:
        _raise_story_service_error(exc)


@router.get("/{story_id}/keywords", response_model=StoryKeywordsResponse)
def read_story_keywords(
    story_id: str,
    _: object = Depends(require_minimum_role(UserRole.GUEST)),
    session: Session = Depends(get_db_session),
) -> StoryKeywordsResponse:
    try:
        return StoryKeywordsResponse(**get_story_keywords(session, story_id))
    except Exception as exc:
        _raise_story_service_error(exc)


@router.post("/{story_id}/tropes", response_model=StoryTropeMutationResponse, status_code=201)
def create_story_trope(
    story_id: str,
    payload: AddStoryTropeRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.CONTRIBUTOR)),
    session: Session = Depends(get_db_session),
) -> StoryTropeMutationResponse:
    try:
        story, dataset, link, job = add_story_trope(
            session,
            story_id,
            expected_story_version=payload.expected_story_version,
            trope_id=payload.trope_id,
            text=payload.text,
            origin=payload.origin,
            actor_user_id=auth_context.user.id,
            actor_role=auth_context.user.role,
        )
    except Exception as exc:
        _raise_story_service_error(exc)

    return StoryTropeMutationResponse(
        story_id=story.id,
        story_version=story.version,
        dataset_version=dataset.version,
        trope=StoryTropeResponse(
            id=link.trope.id,
            text=link.trope.text,
            story_count=int(link.trope.cached_story_count or 0),
            origin=link.origin.value,
            status=link.status.value,
            position=link.position,
        ),
        queued_job=_queued_job_summary(job),
    )


@router.post("/{story_id}/keywords", response_model=StoryKeywordMutationResponse, status_code=201)
def create_story_keyword(
    story_id: str,
    payload: AddStoryKeywordRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.CONTRIBUTOR)),
    session: Session = Depends(get_db_session),
) -> StoryKeywordMutationResponse:
    try:
        story, dataset, link, job = add_story_keyword(
            session,
            story_id,
            expected_story_version=payload.expected_story_version,
            keyword_id=payload.keyword_id,
            text=payload.text,
            actor_user_id=auth_context.user.id,
            actor_role=auth_context.user.role,
        )
    except Exception as exc:
        _raise_story_service_error(exc)

    return StoryKeywordMutationResponse(
        story_id=story.id,
        story_version=story.version,
        dataset_version=dataset.version,
        keyword=StoryKeywordResponse(
            id=link.keyword.id,
            text=link.keyword.text,
            position=link.position,
        ),
        queued_job=_queued_job_summary(job),
    )


@router.put("/{story_id}/tropes/{trope_id}", response_model=StoryTropeMutationResponse)
def update_story_trope(
    story_id: str,
    trope_id: str,
    payload: ReplaceStoryTropeRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.CONTRIBUTOR)),
    session: Session = Depends(get_db_session),
) -> StoryTropeMutationResponse:
    try:
        story, dataset, link, job = replace_story_trope(
            session,
            story_id,
            trope_id,
            expected_story_version=payload.expected_story_version,
            trope_id=payload.trope_id,
            text=payload.text,
            actor_user_id=auth_context.user.id,
            actor_role=auth_context.user.role,
        )
    except Exception as exc:
        _raise_story_service_error(exc)

    return StoryTropeMutationResponse(
        story_id=story.id,
        story_version=story.version,
        dataset_version=dataset.version,
        trope=StoryTropeResponse(
            id=link.trope.id,
            text=link.trope.text,
            story_count=int(link.trope.cached_story_count or 0),
            origin=link.origin.value,
            status=link.status.value,
            position=link.position,
        ),
        queued_job=_queued_job_summary(job),
    )


@router.put("/{story_id}/keywords/{keyword_id}", response_model=StoryKeywordMutationResponse)
def update_story_keyword(
    story_id: str,
    keyword_id: str,
    payload: ReplaceStoryKeywordRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.CONTRIBUTOR)),
    session: Session = Depends(get_db_session),
) -> StoryKeywordMutationResponse:
    try:
        story, dataset, link, job = replace_story_keyword(
            session,
            story_id,
            keyword_id,
            expected_story_version=payload.expected_story_version,
            keyword_id=payload.keyword_id,
            text=payload.text,
            actor_user_id=auth_context.user.id,
            actor_role=auth_context.user.role,
        )
    except Exception as exc:
        _raise_story_service_error(exc)

    return StoryKeywordMutationResponse(
        story_id=story.id,
        story_version=story.version,
        dataset_version=dataset.version,
        keyword=StoryKeywordResponse(
            id=link.keyword.id,
            text=link.keyword.text,
            position=link.position,
        ),
        queued_job=_queued_job_summary(job),
    )


@router.delete("/{story_id}/tropes/{trope_id}", response_model=DeleteStoryTropeResponse)
def remove_story_trope(
    story_id: str,
    trope_id: str,
    payload: StoryVersionRequest = Body(...),
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.CONTRIBUTOR)),
    session: Session = Depends(get_db_session),
) -> DeleteStoryTropeResponse:
    try:
        story, dataset, deleted_trope_id, job = delete_story_trope(
            session,
            story_id,
            trope_id,
            expected_story_version=payload.expected_story_version,
            actor_user_id=auth_context.user.id,
            actor_role=auth_context.user.role,
        )
    except Exception as exc:
        _raise_story_service_error(exc)

    return DeleteStoryTropeResponse(
        story_id=story.id,
        story_version=story.version,
        dataset_version=dataset.version,
        deleted_trope_id=deleted_trope_id,
        queued_job=_queued_job_summary(job),
    )


@router.delete("/{story_id}/keywords/{keyword_id}", response_model=DeleteStoryKeywordResponse)
def remove_story_keyword(
    story_id: str,
    keyword_id: str,
    payload: StoryVersionRequest = Body(...),
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.CONTRIBUTOR)),
    session: Session = Depends(get_db_session),
) -> DeleteStoryKeywordResponse:
    try:
        story, dataset, deleted_keyword_id, job = delete_story_keyword(
            session,
            story_id,
            keyword_id,
            expected_story_version=payload.expected_story_version,
            actor_user_id=auth_context.user.id,
            actor_role=auth_context.user.role,
        )
    except Exception as exc:
        _raise_story_service_error(exc)

    return DeleteStoryKeywordResponse(
        story_id=story.id,
        story_version=story.version,
        dataset_version=dataset.version,
        deleted_keyword_id=deleted_keyword_id,
        queued_job=_queued_job_summary(job),
    )


@router.post("/{story_id}/tropes/{trope_id}/validate", response_model=StoryTropeMutationResponse)
def approve_story_trope(
    story_id: str,
    trope_id: str,
    payload: StoryVersionRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.CONTRIBUTOR)),
    session: Session = Depends(get_db_session),
) -> StoryTropeMutationResponse:
    try:
        story, dataset, link, job = validate_story_trope(
            session,
            story_id,
            trope_id,
            expected_story_version=payload.expected_story_version,
            actor_user_id=auth_context.user.id,
            actor_role=auth_context.user.role,
        )
    except Exception as exc:
        _raise_story_service_error(exc)

    return StoryTropeMutationResponse(
        story_id=story.id,
        story_version=story.version,
        dataset_version=dataset.version,
        trope=StoryTropeResponse(
            id=link.trope.id,
            text=link.trope.text,
            story_count=int(link.trope.cached_story_count or 0),
            origin=link.origin.value,
            status=link.status.value,
            position=link.position,
        ),
        queued_job=_queued_job_summary(job),
    )
