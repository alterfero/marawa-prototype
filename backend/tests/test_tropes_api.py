import csv
import io

from fastapi.testclient import TestClient

from app.core.csv_schema import CSV_COLUMNS, KEYWORD_FIELD, TROPE_FIELD
from app.db import build_engine, build_session_factory
from app.main import create_app


def make_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def make_row(*, title: str, tropes: str = "", keywords: str = "") -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["Story title (Eng)"] = title
    row[TROPE_FIELD] = tropes
    row[KEYWORD_FIELD] = keywords
    return row


def upload_dataset(client: TestClient, rows: list[dict[str, str]]) -> None:
    response = client.post(
        "/api/dataset/upload",
        files={"file": ("stories.csv", make_csv_bytes(rows), "text/csv")},
    )
    assert response.status_code == 201


def test_trope_detail_lists_story_titles_for_active_dataset(tmp_path) -> None:
    db_path = tmp_path / "tropes-api.db"
    engine = build_engine(f"sqlite:///{db_path}")
    session_factory = build_session_factory(engine)
    app = create_app(db_engine=engine, session_factory=session_factory, job_runner_enabled=False)

    with TestClient(app) as client:
        upload_dataset(
            client,
            [
                make_row(title="Story One", tropes="§§ first trope"),
                make_row(title="Story Two", tropes="§§ second trope\n§§ first trope"),
                make_row(title="Story Three", tropes="§§ second trope"),
            ],
        )

        trope_list = client.get("/api/tropes").json()
        first_trope = next(item for item in trope_list if item["text"] == "first trope")

        response = client.get(f"/api/tropes/{first_trope['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == first_trope["id"]
    assert body["text"] == "first trope"
    assert body["story_count"] == 2
    assert [item["title"] for item in body["stories"]] == ["Story One", "Story Two"]
