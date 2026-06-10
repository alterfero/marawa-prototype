from functools import lru_cache
import os
from pathlib import Path

from pydantic import BaseModel

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


class Settings(BaseModel):
    app_name: str = "Marawa Backend"
    api_prefix: str = "/api"
    data_dir: Path
    database_path: Path
    database_url: str
    model_name: str = DEFAULT_MODEL_NAME
    model_cache_dir: Path
    frontend_dist_dir: Path
    port: int = 8000
    cors_allowed_origins: list[str]
    max_upload_bytes: int = 10 * 1024 * 1024

    @classmethod
    def from_environment(cls) -> "Settings":
        data_dir = _resolve_runtime_path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR)))
        database_path_value = os.getenv("DATABASE_PATH")
        database_url_value = os.getenv("MARAWA_DATABASE_URL")
        cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "")

        if database_url_value:
            database_url = database_url_value
            database_path = data_dir / "app.db"
        elif database_path_value:
            if "://" in database_path_value:
                database_url = database_path_value
                database_path = data_dir / "app.db"
            else:
                database_path = _resolve_runtime_path(database_path_value)
                data_dir = database_path.parent
                database_url = _sqlite_url_from_path(database_path)
        else:
            database_path = data_dir / "app.db"
            database_url = _sqlite_url_from_path(database_path)

        return cls(
            data_dir=data_dir,
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
        )

    def ensure_runtime_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_cache_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings.from_environment()
    settings.ensure_runtime_directories()
    return settings
