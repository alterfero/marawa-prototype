from fastapi.testclient import TestClient

from app.db import build_engine, build_session_factory
from app.main import create_app


def test_app_serves_built_frontend_assets(tmp_path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<!doctype html><html><body>Marawa Frontend</body></html>")

    db_path = tmp_path / "frontend-static.db"
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(
        db_engine=engine,
        session_factory=session_factory,
        job_runner_enabled=False,
        frontend_dist_dir=dist_dir,
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Marawa Frontend" in response.text
