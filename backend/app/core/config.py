from functools import lru_cache
import os
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.engine import make_url

from app.core.parsing import clean_text

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_FRONTEND_DIST_DIR = REPO_ROOT / "frontend" / "dist"
DEFAULT_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"


def _resolve_runtime_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def _sqlite_url_from_path(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return f"postgresql+psycopg://{database_url[len('postgres://'):]}"
    if database_url.startswith("postgresql://"):
        return f"postgresql+psycopg://{database_url[len('postgresql://'):]}"
    return database_url


def _database_backend_name(database_url: str) -> str:
    return make_url(database_url).get_backend_name()


def _database_path_from_url(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    return _resolve_runtime_path(database)


class Settings(BaseModel):
    app_name: str = "Marawa Backend"
    api_prefix: str = "/api"
    data_dir: Path
    database_backend: str
    database_path: Path | None
    database_url: str
    model_name: str = DEFAULT_MODEL_NAME
    model_cache_dir: Path
    frontend_dist_dir: Path
    port: int = 8000
    cors_allowed_origins: list[str]
    max_upload_bytes: int = 10 * 1024 * 1024
    session_secret: str
    session_cookie_name: str = "marawa_session"
    csrf_cookie_name: str = "marawa_csrf"
    session_ttl_hours: int = 24 * 7
    invite_reset_ttl_hours: int = 72
    session_cookie_secure: bool = False
    session_cookie_same_site: str = "lax"
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_display_name: str = "Bootstrap Admin"

    @classmethod
    def from_environment(cls) -> "Settings":
        data_dir = _resolve_runtime_path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR)))
        database_path_value = os.getenv("DATABASE_PATH")
        database_url_value = os.getenv("MARAWA_DATABASE_URL")
        cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "")

        if database_url_value:
            database_url = _normalize_database_url(database_url_value)
            database_backend = _database_backend_name(database_url)
            database_path = _database_path_from_url(database_url)
        elif database_path_value:
            if "://" in database_path_value:
                database_url = _normalize_database_url(database_path_value)
                database_backend = _database_backend_name(database_url)
                database_path = _database_path_from_url(database_url)
                if database_path is not None:
                    data_dir = database_path.parent
            else:
                database_path = _resolve_runtime_path(database_path_value)
                data_dir = database_path.parent
                database_url = _sqlite_url_from_path(database_path)
                database_backend = "sqlite"
        else:
            database_path = data_dir / "app.db"
            database_url = _sqlite_url_from_path(database_path)
            database_backend = "sqlite"

        return cls(
            data_dir=data_dir,
            database_backend=database_backend,
            database_path=database_path,
            database_url=database_url,
            model_name=os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME),
            model_cache_dir=data_dir / "model-cache",
            frontend_dist_dir=_resolve_runtime_path(
                os.getenv("FRONTEND_DIST_DIR", str(DEFAULT_FRONTEND_DIST_DIR))
            ),
            port=int(os.getenv("PORT", "8000")),
            cors_allowed_origins=[
                origin
                for origin in (
                    [item.strip() for item in cors_env.split(",") if item.strip()]
                    or [
                        "http://127.0.0.1:5173",
                        "http://localhost:5173",
                        "http://127.0.0.1:4173",
                        "http://localhost:4173",
                    ]
                )
            ],
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
            session_secret=os.getenv("SESSION_SECRET", "dev-session-secret"),
            session_cookie_name=os.getenv("SESSION_COOKIE_NAME", "marawa_session"),
            csrf_cookie_name=os.getenv("CSRF_COOKIE_NAME", "marawa_csrf"),
            session_ttl_hours=int(os.getenv("SESSION_TTL_HOURS", str(24 * 7))),
            invite_reset_ttl_hours=int(os.getenv("INVITE_RESET_TTL_HOURS", "72")),
            session_cookie_secure=os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"},
            session_cookie_same_site=os.getenv("SESSION_COOKIE_SAME_SITE", "lax"),
            bootstrap_admin_email=clean_text(os.getenv("BOOTSTRAP_ADMIN_EMAIL", "")) or None,
            bootstrap_admin_password=os.getenv("BOOTSTRAP_ADMIN_PASSWORD"),
            bootstrap_admin_display_name=clean_text(os.getenv("BOOTSTRAP_ADMIN_DISPLAY_NAME", "Bootstrap Admin")),
        )

    def ensure_runtime_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.database_path is not None:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_cache_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings.from_environment()
    settings.ensure_runtime_directories()
    return settings
