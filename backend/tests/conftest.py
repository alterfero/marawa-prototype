import pytest

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def app():
    get_settings.cache_clear()
    app_instance = create_app()
    yield app_instance
    get_settings.cache_clear()
