from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_minimum_role, require_minimum_role_with_csrf
from app.api.errors import api_error
from app.db.models import KeywordConfirmationStatus, TropeConfirmationStatus, UserRole
from app.services.auth import AuthSessionContext
from app.services.curation import (
    CurationConflictError,
    CurationNotFoundError,
    CurationValidationError,
    delete_unused_tropes,
    delete_unused_keywords,
    list_near_duplicate_tropes,
    list_near_duplicate_keywords,
    list_similar_unconfirmed_tropes,
    list_similar_unconfirmed_keywords,
    merge_tropes,
    merge_keywords,
    validate_trope_merges,
    validate_keyword_merges,
)
from app.services.tropes import (
    TropeLookupNotFoundError,
    TropeMutationValidationError,
    TropeVersionConflictError,
    set_trope_confirmation_statuses,
)
from app.services.keywords import (
    KeywordLookupNotFoundError,
    KeywordMutationValidationError,
    KeywordVersionConflictError,
    set_keyword_confirmation_statuses,
)


class JobSummaryResponse(BaseModel):
    id: str
    status: str
    job_type: str


class TropeSummaryResponse(BaseModel):
    id: str
    version: int | None = None
    text: str
    confirmation_status: TropeConfirmationStatus | None = None
    story_count: int


class NearDuplicateTropePairResponse(BaseModel):
    source_trope: TropeSummaryResponse
    target_trope: TropeSummaryResponse
    similarity_score: float
    metadata: dict


class NearDuplicateTropeListResponse(BaseModel):
    items: list[NearDuplicateTropePairResponse]
    artifact_version: int | None
    model_name: str
    total: int


class SimilarUnconfirmedTropeResponse(TropeSummaryResponse):
    similarity_score: float


class SimilarUnconfirmedTropeListResponse(BaseModel):
    source_trope_id: str
    items: list[SimilarUnconfirmedTropeResponse]
    artifact_version: int | None
    model_name: str
    minimum_similarity: float
    total: int


class KeywordSummaryResponse(BaseModel):
    id: str
    version: int | None = None
    text: str
    confirmation_status: KeywordConfirmationStatus | None = None
    story_count: int


class NearDuplicateKeywordPairResponse(BaseModel):
    source_keyword: KeywordSummaryResponse
    target_keyword: KeywordSummaryResponse
    similarity_score: float
    metadata: dict


class NearDuplicateKeywordListResponse(BaseModel):
    items: list[NearDuplicateKeywordPairResponse]
    artifact_version: int | None
    model_name: str
    total: int


class SimilarUnconfirmedKeywordResponse(KeywordSummaryResponse):
    similarity_score: float


class SimilarUnconfirmedKeywordListResponse(BaseModel):
    source_keyword_id: str
    items: list[SimilarUnconfirmedKeywordResponse]
    artifact_version: int | None
    model_name: str
    minimum_similarity: float
    total: int


class MergeTropesRequest(BaseModel):
    source_trope_id: str
    target_trope_id: str


class MergeTropesResponse(BaseModel):
    source_trope_id: str
    target_trope_id: str
    affected_story_count: int
    dataset_version: int
    queued_job: JobSummaryResponse | None


class ValidateTropesRequest(BaseModel):
    merges: list[MergeTropesRequest]


class AppliedMergeSummaryResponse(BaseModel):
    source_trope_id: str
    target_trope_id: str
    affected_story_count: int


class ValidateTropesResponse(BaseModel):
    applied_merges: list[AppliedMergeSummaryResponse]
    merge_count: int
    affected_story_count: int
    dataset_version: int
    queued_job: JobSummaryResponse | None


class CanonicalizeTropeRequest(BaseModel):
    trope_id: str
    expected_trope_version: int


class CanonicalizeTropesRequest(BaseModel):
    tropes: list[CanonicalizeTropeRequest]


class CanonicalizeTropesResponse(BaseModel):
    tropes: list[TropeSummaryResponse]


class DeleteUnusedTropesResponse(BaseModel):
    deleted_trope_count: int
    dataset_version: int
    queued_job: JobSummaryResponse | None


class MergeKeywordsRequest(BaseModel):
    source_keyword_id: str
    target_keyword_id: str


class MergeKeywordsResponse(BaseModel):
    source_keyword_id: str
    target_keyword_id: str
    affected_story_count: int
    dataset_version: int
    queued_job: JobSummaryResponse | None


class ValidateKeywordsRequest(BaseModel):
    merges: list[MergeKeywordsRequest]


class AppliedKeywordMergeSummaryResponse(BaseModel):
    source_keyword_id: str
    target_keyword_id: str
    affected_story_count: int


class ValidateKeywordsResponse(BaseModel):
    applied_merges: list[AppliedKeywordMergeSummaryResponse]
    merge_count: int
    affected_story_count: int
    dataset_version: int
    queued_job: JobSummaryResponse | None


class CanonicalizeKeywordRequest(BaseModel):
    keyword_id: str
    expected_keyword_version: int


class CanonicalizeKeywordsRequest(BaseModel):
    keywords: list[CanonicalizeKeywordRequest]


class CanonicalizeKeywordsResponse(BaseModel):
    keywords: list[KeywordSummaryResponse]


class DeleteUnusedKeywordsResponse(BaseModel):
    deleted_keyword_count: int
    dataset_version: int
    queued_job: JobSummaryResponse | None


router = APIRouter(prefix="/curation", tags=["curation"])


def get_search_service(request: Request):
    return request.app.state.search_service


def _queued_job_summary(job) -> JobSummaryResponse | None:
    if job is None:
        return None
    return JobSummaryResponse(
        id=job.id,
        status=job.status.value,
        job_type=job.job_type,
    )


def _raise_curation_error(exc: Exception) -> None:
    if isinstance(exc, CurationNotFoundError):
        raise api_error(404, "trope_not_found", str(exc)) from exc
    if isinstance(exc, CurationConflictError):
        raise api_error(409, "trope_merge_conflict", str(exc)) from exc
    if isinstance(exc, CurationValidationError):
        raise api_error(400, "trope_merge_invalid", str(exc)) from exc
    if isinstance(exc, TropeLookupNotFoundError):
        raise api_error(404, "trope_not_found", str(exc)) from exc
    if isinstance(exc, TropeVersionConflictError):
        raise api_error(409, "trope_version_conflict", str(exc)) from exc
    if isinstance(exc, TropeMutationValidationError):
        raise api_error(400, "trope_mutation_invalid", str(exc)) from exc
    raise exc


def _raise_keyword_curation_error(exc: Exception) -> None:
    if isinstance(exc, CurationNotFoundError):
        raise api_error(404, "keyword_not_found", str(exc)) from exc
    if isinstance(exc, CurationConflictError):
        raise api_error(409, "keyword_merge_conflict", str(exc)) from exc
    if isinstance(exc, CurationValidationError):
        raise api_error(400, "keyword_merge_invalid", str(exc)) from exc
    if isinstance(exc, KeywordLookupNotFoundError):
        raise api_error(404, "keyword_not_found", str(exc)) from exc
    if isinstance(exc, KeywordVersionConflictError):
        raise api_error(409, "keyword_version_conflict", str(exc)) from exc
    if isinstance(exc, KeywordMutationValidationError):
        raise api_error(400, "keyword_mutation_invalid", str(exc)) from exc
    raise exc


@router.get("/near-duplicate-tropes", response_model=NearDuplicateTropeListResponse)
def read_near_duplicate_tropes(
    _: object = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
) -> NearDuplicateTropeListResponse:
    return NearDuplicateTropeListResponse(
        **list_near_duplicate_tropes(session, model_name=search_service.model_name)
    )


@router.get("/tropes/{trope_id}/similar-unconfirmed", response_model=SimilarUnconfirmedTropeListResponse)
def read_similar_unconfirmed_tropes(
    trope_id: str,
    minimum_similarity: float = Query(default=0.6, ge=0.0, le=1.0),
    include_canonical: bool = Query(default=False),
    _: object = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
) -> SimilarUnconfirmedTropeListResponse:
    try:
        return SimilarUnconfirmedTropeListResponse(
            **list_similar_unconfirmed_tropes(
                session,
                source_trope_id=trope_id,
                minimum_similarity=minimum_similarity,
                search_service=search_service,
                include_canonical=include_canonical,
            )
        )
    except Exception as exc:
        _raise_curation_error(exc)


@router.get("/near-duplicate-keywords", response_model=NearDuplicateKeywordListResponse)
def read_near_duplicate_keywords(
    _: object = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
) -> NearDuplicateKeywordListResponse:
    return NearDuplicateKeywordListResponse(
        **list_near_duplicate_keywords(session, model_name=search_service.model_name)
    )


@router.get("/keywords/{keyword_id}/similar-unconfirmed", response_model=SimilarUnconfirmedKeywordListResponse)
def read_similar_unconfirmed_keywords(
    keyword_id: str,
    minimum_similarity: float = Query(default=0.6, ge=0.0, le=1.0),
    include_canonical: bool = Query(default=False),
    _: object = Depends(require_minimum_role(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
) -> SimilarUnconfirmedKeywordListResponse:
    try:
        return SimilarUnconfirmedKeywordListResponse(
            **list_similar_unconfirmed_keywords(
                session,
                source_keyword_id=keyword_id,
                minimum_similarity=minimum_similarity,
                search_service=search_service,
                include_canonical=include_canonical,
            )
        )
    except Exception as exc:
        _raise_keyword_curation_error(exc)


@router.post("/merge-tropes", response_model=MergeTropesResponse)
def merge_canonical_tropes(
    payload: MergeTropesRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> MergeTropesResponse:
    try:
        dataset, summary, job = merge_tropes(
            session,
            source_trope_id=payload.source_trope_id,
            target_trope_id=payload.target_trope_id,
            actor_user_id=auth_context.user.id,
        )
    except Exception as exc:
        _raise_curation_error(exc)

    return MergeTropesResponse(
        source_trope_id=summary["source_trope_id"],
        target_trope_id=summary["target_trope_id"],
        affected_story_count=summary["affected_story_count"],
        dataset_version=dataset.version,
        queued_job=_queued_job_summary(job),
    )


@router.post("/validate-merges", response_model=ValidateTropesResponse)
def validate_canonical_trope_merges(
    payload: ValidateTropesRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> ValidateTropesResponse:
    try:
        dataset, summary, job = validate_trope_merges(
            session,
            merges=[
                {
                    "source_trope_id": merge.source_trope_id,
                    "target_trope_id": merge.target_trope_id,
                }
                for merge in payload.merges
            ],
            actor_user_id=auth_context.user.id,
        )
    except Exception as exc:
        _raise_curation_error(exc)

    return ValidateTropesResponse(
        applied_merges=[
            AppliedMergeSummaryResponse(
                source_trope_id=merge["source_trope_id"],
                target_trope_id=merge["target_trope_id"],
                affected_story_count=merge["affected_story_count"],
            )
            for merge in summary["applied_merges"]
        ],
        merge_count=summary["merge_count"],
        affected_story_count=summary["affected_story_count"],
        dataset_version=dataset.version,
        queued_job=_queued_job_summary(job),
    )


@router.post("/canonicalize-tropes", response_model=CanonicalizeTropesResponse)
def canonicalize_tropes(
    payload: CanonicalizeTropesRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> CanonicalizeTropesResponse:
    try:
        tropes = set_trope_confirmation_statuses(
            session,
            updates=[
                {
                    "trope_id": trope.trope_id,
                    "expected_version": trope.expected_trope_version,
                    "confirmation_status": TropeConfirmationStatus.CANONICAL,
                }
                for trope in payload.tropes
            ],
            actor_user_id=auth_context.user.id,
        )
    except Exception as exc:
        _raise_curation_error(exc)

    return CanonicalizeTropesResponse(
        tropes=[TropeSummaryResponse(**trope) for trope in tropes]
    )


@router.delete("/unused-tropes", response_model=DeleteUnusedTropesResponse)
def remove_unused_tropes(
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> DeleteUnusedTropesResponse:
    try:
        dataset, summary, job = delete_unused_tropes(
            session,
            actor_user_id=auth_context.user.id,
        )
    except Exception as exc:
        _raise_curation_error(exc)

    return DeleteUnusedTropesResponse(
        deleted_trope_count=summary["deleted_trope_count"],
        dataset_version=dataset.version,
        queued_job=_queued_job_summary(job),
    )


@router.post("/merge-keywords", response_model=MergeKeywordsResponse)
def merge_canonical_keywords(
    payload: MergeKeywordsRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> MergeKeywordsResponse:
    try:
        dataset, summary, job = merge_keywords(
            session,
            source_keyword_id=payload.source_keyword_id,
            target_keyword_id=payload.target_keyword_id,
            actor_user_id=auth_context.user.id,
        )
    except Exception as exc:
        _raise_keyword_curation_error(exc)
    return MergeKeywordsResponse(
        source_keyword_id=summary["source_keyword_id"],
        target_keyword_id=summary["target_keyword_id"],
        affected_story_count=summary["affected_story_count"],
        dataset_version=dataset.version,
        queued_job=_queued_job_summary(job),
    )


@router.post("/validate-keyword-merges", response_model=ValidateKeywordsResponse)
def validate_canonical_keyword_merges(
    payload: ValidateKeywordsRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> ValidateKeywordsResponse:
    try:
        dataset, summary, job = validate_keyword_merges(
            session,
            merges=[
                {
                    "source_keyword_id": merge.source_keyword_id,
                    "target_keyword_id": merge.target_keyword_id,
                }
                for merge in payload.merges
            ],
            actor_user_id=auth_context.user.id,
        )
    except Exception as exc:
        _raise_keyword_curation_error(exc)
    return ValidateKeywordsResponse(
        applied_merges=[
            AppliedKeywordMergeSummaryResponse(
                source_keyword_id=merge["source_keyword_id"],
                target_keyword_id=merge["target_keyword_id"],
                affected_story_count=merge["affected_story_count"],
            )
            for merge in summary["applied_merges"]
        ],
        merge_count=summary["merge_count"],
        affected_story_count=summary["affected_story_count"],
        dataset_version=dataset.version,
        queued_job=_queued_job_summary(job),
    )


@router.post("/canonicalize-keywords", response_model=CanonicalizeKeywordsResponse)
def canonicalize_keywords(
    payload: CanonicalizeKeywordsRequest,
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> CanonicalizeKeywordsResponse:
    try:
        keywords = set_keyword_confirmation_statuses(
            session,
            updates=[
                {
                    "keyword_id": keyword.keyword_id,
                    "expected_version": keyword.expected_keyword_version,
                    "confirmation_status": KeywordConfirmationStatus.CANONICAL,
                }
                for keyword in payload.keywords
            ],
            actor_user_id=auth_context.user.id,
        )
    except Exception as exc:
        _raise_keyword_curation_error(exc)
    return CanonicalizeKeywordsResponse(
        keywords=[KeywordSummaryResponse(**keyword) for keyword in keywords]
    )


@router.delete("/unused-keywords", response_model=DeleteUnusedKeywordsResponse)
def remove_unused_keywords(
    auth_context: AuthSessionContext = Depends(require_minimum_role_with_csrf(UserRole.ADMIN)),
    session: Session = Depends(get_db_session),
) -> DeleteUnusedKeywordsResponse:
    try:
        dataset, summary, job = delete_unused_keywords(
            session,
            actor_user_id=auth_context.user.id,
        )
    except Exception as exc:
        _raise_keyword_curation_error(exc)
    return DeleteUnusedKeywordsResponse(
        deleted_keyword_count=summary["deleted_keyword_count"],
        dataset_version=dataset.version,
        queued_job=_queued_job_summary(job),
    )
