"""Service layer for backend business operations."""

from app.services.curation import (
    CurationConflictError,
    CurationNotFoundError,
    CurationValidationError,
    delete_trope,
    list_canonical_tropes,
    list_near_duplicate_tropes,
    merge_tropes,
)
from app.services.csv_io import CSVImportValidationError, export_active_dataset_to_csv_bytes, import_csv_bytes
from app.services.dataset import get_dataset_status, upload_dataset_csv
from app.services.exploration import build_exploration_response, english_story_title, primary_abstract, similarity_to_color
from app.services.health import get_health_status
from app.services.jobs import get_job, list_jobs, queue_job, requeue_stale_running_jobs
from app.services.search_service import SearchService
from app.services.stories import (
    StoryMutationValidationError,
    StoryNotFoundError,
    StoryTropeNotFoundError,
    StoryVersionConflictError,
    TropeNotFoundError,
    add_story_trope,
    delete_story_trope,
    get_story_detail,
    get_story_tropes,
    list_active_stories,
    validate_story_trope,
)

__all__ = [
    "CSVImportValidationError",
    "CurationConflictError",
    "CurationNotFoundError",
    "CurationValidationError",
    "export_active_dataset_to_csv_bytes",
    "build_exploration_response",
    "english_story_title",
    "get_job",
    "get_dataset_status",
    "get_health_status",
    "import_csv_bytes",
    "list_canonical_tropes",
    "list_near_duplicate_tropes",
    "list_jobs",
    "merge_tropes",
    "primary_abstract",
    "queue_job",
    "requeue_stale_running_jobs",
    "SearchService",
    "StoryMutationValidationError",
    "StoryNotFoundError",
    "StoryTropeNotFoundError",
    "StoryVersionConflictError",
    "TropeNotFoundError",
    "add_story_trope",
    "delete_story_trope",
    "upload_dataset_csv",
    "get_story_detail",
    "get_story_tropes",
    "list_active_stories",
    "similarity_to_color",
    "validate_story_trope",
    "delete_trope",
]
