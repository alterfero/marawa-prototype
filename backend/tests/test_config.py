from app.core.config import REPO_ROOT, get_settings


def test_settings_default_to_local_data_directory(monkeypatch) -> None:
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("MARAWA_DATABASE_URL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.data_dir == REPO_ROOT / "data"
    assert settings.database_path == REPO_ROOT / "data" / "app.db"
    assert settings.database_url == f"sqlite:///{(REPO_ROOT / 'data' / 'app.db').as_posix()}"
    assert settings.port == 8000

    get_settings.cache_clear()


def test_settings_prefer_explicit_database_path(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "railway-data" / "stories.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "ignored-data-dir"))
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("MODEL_NAME", "custom-model")
    monkeypatch.setenv("PORT", "9001")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.data_dir == database_path.parent
    assert settings.database_path == database_path
    assert settings.database_url == f"sqlite:///{database_path.as_posix()}"
    assert settings.model_name == "custom-model"
    assert settings.port == 9001
    assert settings.model_cache_dir == database_path.parent / "model-cache"

    get_settings.cache_clear()
