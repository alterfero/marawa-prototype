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


class Dataset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = (
        Index(
            "uq_datasets_single_active",
            "status",
            unique=True,
            sqlite_where=text("status = 'active'"),
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


class Story(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stories"
    __table_args__ = (
        Index(
            "uq_stories_dataset_source_row_number",
            "dataset_id",
            "source_row_number",
            unique=True,
            sqlite_where=text("source_row_number IS NOT NULL"),
        ),
    )

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_row_number: Mapped[int | None] = mapped_column(Integer)
    fields_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    dataset: Mapped["Dataset"] = relationship(back_populates="stories")
    trope_links: Mapped[list["StoryTrope"]] = relationship(back_populates="story", cascade="all, delete-orphan")
    keyword_links: Mapped[list["StoryKeyword"]] = relationship(back_populates="story", cascade="all, delete-orphan")


class Trope(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tropes"

    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
    cached_story_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    story_links: Mapped[list["StoryTrope"]] = relationship(back_populates="trope")
    embeddings: Mapped[list["TermEmbedding"]] = relationship(back_populates="trope")

    @validates("text")
    def _sync_text_fields(self, _: str, value: str) -> str:
        cleaned = clean_text(value)
        self.normalized_text = normalize_text(cleaned)
        return cleaned


class Keyword(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "keywords"

    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
    cached_story_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

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


class TermEmbedding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "term_embeddings"
    __table_args__ = (
        CheckConstraint(
            "(trope_id IS NOT NULL AND keyword_id IS NULL) OR (trope_id IS NULL AND keyword_id IS NOT NULL)",
            name="ck_term_embeddings_exactly_one_term",
        ),
        Index(
            "uq_term_embeddings_trope_model",
            "trope_id",
            "model_name",
            unique=True,
            sqlite_where=text("trope_id IS NOT NULL"),
        ),
        Index(
            "uq_term_embeddings_keyword_model",
            "keyword_id",
            "model_name",
            unique=True,
            sqlite_where=text("keyword_id IS NOT NULL"),
        ),
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
