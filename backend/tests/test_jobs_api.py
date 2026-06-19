from fastapi.testclient import TestClient

from app.db import build_engine, build_session_factory, initialize_database
from app.main import create_app
from app.services.jobs import queue_job
from tests.auth_helpers import authenticate_admin, configure_auth_env


def test_jobs_api_lists_jobs_and_returns_job_detail(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    db_path = tmp_path / "jobs-api.db"
    engine = build_engine(f"sqlite:///{db_path}")
    initialize_database(engine)
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        job = queue_job(session, job_type="test_success", payload={"message": "hello jobs"})
        session.commit()
        job_id = job.id

    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)
    with TestClient(app) as client:
        authenticate_admin(client)
        list_response = client.get("/api/jobs")
        detail_response = client.get(f"/api/jobs/{job_id}")

    assert list_response.status_code == 200
    listed_jobs = list_response.json()
    assert len(listed_jobs) == 1
    assert listed_jobs[0]["id"] == job_id
    assert listed_jobs[0]["status"] == "queued"
    assert listed_jobs[0]["job_type"] == "test_success"

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["id"] == job_id
    assert detail["payload"]["message"] == "hello jobs"
    assert detail["status"] == "queued"


def test_jobs_api_returns_404_for_unknown_job(monkeypatch, tmp_path) -> None:
    configure_auth_env(monkeypatch)
    db_path = tmp_path / "jobs-api-missing.db"
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as client:
        authenticate_admin(client)
        response = client.get("/api/jobs/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "code": "job_not_found",
        "message": "Job not found.",
    }
