"""Exercise SQL HTTP behavior against a real temporary SQLite database."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings


@pytest.fixture
def payload():
    document = json.loads(
        (
            Path(__file__).resolve().parents[1] / "mongodb/sample_documents.json"
        ).read_text()
    )[0]
    document["timestamp"] = document["timestamp"]["$date"]
    document.pop("_id")
    return document


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None, sqlite_path=tmp_path / "test.db", mongodb_uri=None
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_sql_crud_and_http_errors(client, payload):
    path = "/sql/records/" + payload["timestamp"]
    assert client.get("/sql/records/latest").status_code == 404
    assert client.post("/sql/records", json=payload).status_code == 201
    assert client.post("/sql/records", json=payload).status_code == 409
    assert client.get(path).json() == payload
    assert client.get("/sql/records/latest").json() == payload
    updated = {**payload, "load_mw": 27000}
    assert client.put(path, json=updated).json() == updated
    assert client.get(path).json()["load_mw"] == 27000
    changed_time = {**updated, "timestamp": "2019-01-01T00:00:00Z"}
    assert client.put(path, json=changed_time).status_code == 422
    assert client.delete(path).status_code == 204
    assert client.get(path).status_code == 404
    assert client.delete(path).status_code == 404
    assert client.put(path, json=payload).status_code == 404


def test_range_is_sorted_and_end_exclusive(client, payload):
    for timestamp in [
        "2018-01-01T02:00:00Z",
        "2018-01-01T00:00:00Z",
        "2018-01-01T01:00:00Z",
    ]:
        assert (
            client.post(
                "/sql/records", json={**payload, "timestamp": timestamp}
            ).status_code
            == 201
        )
    bounds = {"start": "2018-01-01T00:00:00Z", "end": "2018-01-01T02:00:00Z"}
    response = client.get("/sql/records", params=bounds)
    assert [row["timestamp"] for row in response.json()] == [
        "2018-01-01T00:00:00Z",
        "2018-01-01T01:00:00Z",
    ]
    page = client.get("/sql/records", params={**bounds, "limit": 1, "offset": 1}).json()
    assert page[0]["timestamp"] == "2018-01-01T01:00:00Z"
    summary = client.get("/sql/summary/daily", params=bounds).json()
    assert summary[0]["count"] == 2
    assert summary[0]["avg_load_mw"] == payload["load_mw"]


@pytest.mark.parametrize(
    "parameters",
    [
        {"start": "2018-01-01T00:00:00"},
        {"start": "2018-01-01T00:30:00Z"},
        {"start": "9999-12-31T23:00:00-01:00"},
        {"start": "0001-01-01T00:00:00+01:00"},
        {"start": "2018-01-02T00:00:00Z", "end": "2018-01-01T00:00:00Z"},
        {"limit": 0},
        {"limit": 1001},
        {"offset": -1},
    ],
)
def test_bad_range_parameters(client, parameters):
    assert client.get("/sql/records", params=parameters).status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        "missing_city",
        "duplicate_city",
        "nan_load",
        "negative_load",
        "naive_time",
        "extra",
    ],
)
def test_invalid_records_rejected(client, payload, change):
    bad = deepcopy(payload)
    if change == "missing_city":
        bad["weather"].pop()
    elif change == "duplicate_city":
        bad["weather"][1]["city"] = bad["weather"][0]["city"]
    elif change == "nan_load":
        bad["load_mw"] = "NaN"
    elif change == "negative_load":
        bad["load_mw"] = -1
    elif change == "naive_time":
        bad["timestamp"] = "2018-01-01T00:00:00"
    else:
        bad["unsupported"] = 123
    assert client.post("/sql/records", json=bad).status_code == 422


def test_missing_atlas_settings_and_bad_backend(client):
    assert client.get("/mongo/records/latest").status_code == 503
    assert client.get("/unknown/records/latest").status_code == 422
    assert client.get("/openapi.json").status_code == 200


def test_nonfinite_json_value_returns_validation_error(client, payload):
    payload["load_mw"] = float("nan")
    response = client.post(
        "/sql/records",
        content=json.dumps(payload),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "finite_number"
