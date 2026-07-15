from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import uuid

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.parsing import clean_text, normalize_text
from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_uuid() -> str:
    return str(uuid.uuid4())


def enum_values(enum_class: type[Enum]) -> list[str]:
    return [item.value for item in enum_class]


class DatasetStatus(str, Enum):
    STAGED = "staged"
    ACTIVE = "active"
    ARCHIVED = "archived"
    FAILED = "failed"


class UserRole(str, Enum):
    GUEST = "guest"
    CONTRIBUTOR = "contributor"
    ADMIN = "admin"


class UserStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING_INVITE = "pending_invite"


class InviteResetTokenKind(str, Enum):
    INVITE = "invite"
    ADMIN_RESET = "admin_reset"


class TermReviewStatus(str, Enum):
    APPROVED = "approved"
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"


class StoryCompleteness(str, Enum):
    INCOMPLETE = "incomplete"
    PENDING_REVIEW = "pending review"
    COMPLETE = "complete"


class StoryTropeOrigin(str, Enum):
    CSV_IMPORT = "csv_import"
    SEMANTIC_SUGGESTION = "semantic_suggestion"
    HUMAN_ENTERED = "human_entered"
    HUMAN_APPROVED = "human_approved"
    MERGE = "merge"


class AssignmentStatus(str, Enum):
    PENDING = "pending"
    VALIDATED = "validated"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TermKind(str, Enum):
    TROPE = "trope"
    KEYWORD = "keyword"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewType(str, Enum):
    STORY_CREATED = "story_created"
    STORY_UPDATED = "story_updated"
    TROPE_PENDING = "trope_pending"
    KEYWORD_PENDING = "keyword_pending"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SqlEnum(
            UserRole,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=UserRole.GUEST,
        nullable=False,
        index=True,
    )
    password_hash: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[UserStatus] = mapped_column(
        SqlEnum(
            UserStatus,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=UserStatus.PENDING_INVITE,
        nullable=False,
        index=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="UserSession.user_id",
    )
    created_tokens: Mapped[list["InviteResetToken"]] = relationship(
        back_populates="created_by_user",
        foreign_keys="InviteResetToken.created_by_user_id",
    )
    received_tokens: Mapped[list["InviteResetToken"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="InviteResetToken.user_id",
    )

    @validates("email")
    def _normalize_email(self, _: str, value: str) -> str:
        return clean_text(value).lower()

    @validates("display_name")
    def _normalize_display_name(self, _: str, value: str) -> str:
        return clean_text(value)


class UserSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(255))
    user_agent: Mapped[str | None] = mapped_column(String(1024))

    user: Mapped["User"] = relationship(back_populates="sessions", foreign_keys=[user_id])


class InviteResetToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invite_reset_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    token_kind: Mapped[InviteResetTokenKind] = mapped_column(
        SqlEnum(
            InviteResetTokenKind,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    user: Mapped["User"] = relationship(back_populates="received_tokens", foreign_keys=[user_id])
    created_by_user: Mapped["User | None"] = relationship(back_populates="created_tokens", foreign_keys=[created_by_user_id])


class Dataset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = (
        Index(
            "uq_datasets_single_active",
            "status",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[DatasetStatus] = mapped_column(
        SqlEnum(
            DatasetStatus,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=DatasetStatus.STAGED,
        nullable=False,
    )
    source_filename: Mapped[str | None] = mapped_column(String(512))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    stories: Mapped[list["Story"]] = relationship(back_populates="dataset")
    jobs: Mapped[list["Job"]] = relationship(back_populates="dataset")
    tropes: Mapped[list["Trope"]] = relationship(back_populates="dataset")
    keywords: Mapped[list["Keyword"]] = relationship(back_populates="dataset")
    review_items: Mapped[list["ReviewItem"]] = relationship(back_populates="dataset")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="dataset")


class Story(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stories"
    __table_args__ = (
        Index("uq_stories_dataset_source_row_number", "dataset_id", "source_row_number", unique=True),
    )

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_row_number: Mapped[int | None] = mapped_column(Integer)
    fields_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    completeness: Mapped[StoryCompleteness] = mapped_column(
        SqlEnum(
            StoryCompleteness,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=StoryCompleteness.INCOMPLETE,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    dataset: Mapped["Dataset"] = relationship(back_populates="stories")
    trope_links: Mapped[list["StoryTrope"]] = relationship(back_populates="story", cascade="all, delete-orphan")
    keyword_links: Mapped[list["StoryKeyword"]] = relationship(back_populates="story", cascade="all, delete-orphan")


class Trope(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tropes"
    __table_args__ = (
        Index("uq_tropes_dataset_normalized_text", "dataset_id", "normalized_text", unique=True),
    )

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(String(512), nullable=False)
    review_status: Mapped[TermReviewStatus] = mapped_column(
        SqlEnum(
            TermReviewStatus,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=TermReviewStatus.APPROVED,
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    updated_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    cached_story_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    dataset: Mapped["Dataset"] = relationship(back_populates="tropes")
    story_links: Mapped[list["StoryTrope"]] = relationship(back_populates="trope")
    embeddings: Mapped[list["TermEmbedding"]] = relationship(back_populates="trope")

    @validates("text")
    def _sync_text_fields(self, _: str, value: str) -> str:
        cleaned = clean_text(value)
        self.normalized_text = normalize_text(cleaned)
        return cleaned


class Keyword(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "keywords"
    __table_args__ = (
        Index("uq_keywords_dataset_normalized_text", "dataset_id", "normalized_text", unique=True),
    )

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(String(512), nullable=False)
    review_status: Mapped[TermReviewStatus] = mapped_column(
        SqlEnum(
            TermReviewStatus,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=TermReviewStatus.APPROVED,
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    updated_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    cached_story_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    dataset: Mapped["Dataset"] = relationship(back_populates="keywords")
    story_links: Mapped[list["StoryKeyword"]] = relationship(back_populates="keyword")
    embeddings: Mapped[list["TermEmbedding"]] = relationship(back_populates="keyword")

    @validates("text")
    def _sync_text_fields(self, _: str, value: str) -> str:
        cleaned = clean_text(value)
        self.normalized_text = normalize_text(cleaned)
        return cleaned


class StoryTrope(TimestampMixin, Base):
    __tablename__ = "story_tropes"
    __table_args__ = (
        UniqueConstraint("story_id", "trope_id", name="uq_story_tropes_story_id_trope_id"),
    )

    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True)
    trope_id: Mapped[str] = mapped_column(ForeignKey("tropes.id", ondelete="RESTRICT"), primary_key=True)
    origin: Mapped[StoryTropeOrigin] = mapped_column(
        SqlEnum(
            StoryTropeOrigin,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=StoryTropeOrigin.CSV_IMPORT,
        nullable=False,
    )
    status: Mapped[AssignmentStatus] = mapped_column(
        SqlEnum(
            AssignmentStatus,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=AssignmentStatus.PENDING,
        nullable=False,
    )
    position: Mapped[int | None] = mapped_column(Integer)

    story: Mapped["Story"] = relationship(back_populates="trope_links")
    trope: Mapped["Trope"] = relationship(back_populates="story_links")


class StoryKeyword(TimestampMixin, Base):
    __tablename__ = "story_keywords"
    __table_args__ = (
        UniqueConstraint("story_id", "keyword_id", name="uq_story_keywords_story_id_keyword_id"),
    )

    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True)
    keyword_id: Mapped[str] = mapped_column(ForeignKey("keywords.id", ondelete="RESTRICT"), primary_key=True)
    position: Mapped[int | None] = mapped_column(Integer)

    story: Mapped["Story"] = relationship(back_populates="keyword_links")
    keyword: Mapped["Keyword"] = relationship(back_populates="story_links")


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "jobs"

    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"), index=True)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        SqlEnum(
            JobStatus,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=JobStatus.QUEUED,
        nullable=False,
        index=True,
    )
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    dataset: Mapped["Dataset | None"] = relationship(back_populates="jobs")


class ReviewItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "review_items"

    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"), index=True)
    review_type: Mapped[ReviewType] = mapped_column(
        SqlEnum(
            ReviewType,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        nullable=False,
        index=True,
    )
    subject_table: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[ReviewStatus] = mapped_column(
        SqlEnum(
            ReviewStatus,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=ReviewStatus.PENDING,
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    resolved_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    dataset: Mapped["Dataset | None"] = relationship(back_populates="review_items")


class AuditEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audit_events"

    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"), index=True)
    subject_table: Mapped[str | None] = mapped_column(String(64), index=True)
    subject_id: Mapped[str | None] = mapped_column(String(36), index=True)
    request_id: Mapped[str | None] = mapped_column(String(255), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    dataset: Mapped["Dataset | None"] = relationship(back_populates="audit_events")


class TermEmbedding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "term_embeddings"
    __table_args__ = (
        CheckConstraint(
            "(trope_id IS NOT NULL AND keyword_id IS NULL) OR (trope_id IS NULL AND keyword_id IS NOT NULL)",
            name="ck_term_embeddings_exactly_one_term",
        ),
        Index("uq_term_embeddings_trope_model", "trope_id", "model_name", unique=True),
        Index("uq_term_embeddings_keyword_model", "keyword_id", "model_name", unique=True),
    )

    term_kind: Mapped[TermKind] = mapped_column(
        SqlEnum(
            TermKind,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        nullable=False,
        index=True,
    )
    trope_id: Mapped[str | None] = mapped_column(ForeignKey("tropes.id", ondelete="CASCADE"))
    keyword_id: Mapped[str | None] = mapped_column(ForeignKey("keywords.id", ondelete="CASCADE"))
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)
    vector_dimensions: Mapped[int | None] = mapped_column(Integer)
    vector_blob: Mapped[bytes | None] = mapped_column(LargeBinary)
    content_hash: Mapped[str | None] = mapped_column(String(64))

    trope: Mapped["Trope | None"] = relationship(back_populates="embeddings")
    keyword: Mapped["Keyword | None"] = relationship(back_populates="embeddings")


class TermSimilarityCache(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "term_similarity_cache"
    __table_args__ = (
        UniqueConstraint(
            "term_kind",
            "model_name",
            "source_term_id",
            "target_term_id",
            name="uq_term_similarity_cache_lookup",
        ),
        CheckConstraint("source_term_id <> target_term_id", name="ck_term_similarity_cache_distinct_pair"),
    )

    term_kind: Mapped[TermKind] = mapped_column(
        SqlEnum(
            TermKind,
            native_enum=False,
            validate_strings=True,
            values_callable=enum_values,
        ),
        nullable=False,
        index=True,
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)
    source_term_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_term_id: Mapped[str] = mapped_column(String(36), nullable=False)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
