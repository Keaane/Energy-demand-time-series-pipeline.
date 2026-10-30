"""Exercise MongoDB API routes against disposable real-server collections."""

import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from bson import json_util
from fastapi.testclient import TestClient
from pymongo import MongoClient

from src.api import create_app
from src.config import Settings
from src.schemas import HourlyRecord
from src.storage.mongo import MongoRepository


@pytest.fixture
def sample_record():
    path = Path(__file__).resolve().parents[1] / "mongodb" / "sample_documents.json"
    document = json_util.loads(
        path.read_text(), json_options=json_util.JSONOptions(tz_aware=True)
    )[0]
    document.pop("_id")
    return HourlyRecord.model_validate(document)


@pytest.fixture
def client(tmp_path):
    uri = os.environ.get("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("Set TEST_MONGODB_URI to test API routes against real MongoDB.")
    database = os.environ.get("TEST_MONGODB_DATABASE", "energy_pipeline")
    collection = "test_api_" + uuid4().hex
    repository = MongoRepository(uri, database=database, collection=collection)
    try:
        app = create_app(Settings(sqlite_path=tmp_path / "test.db", mongodb_uri=None))
        app.state.repositories["mongo"] = repository
        with TestClient(app) as test_client:
            yield test_client
    finally:
        # Lifespan may already have closed the repository's client.
        try:
            with MongoClient(
                uri,
                serverSelectionTimeoutMS=10_000,
                connectTimeoutMS=10_000,
                socketTimeoutMS=10_000,
            ) as cleanup_client:
                cleanup_client[database].drop_collection(collection)
        finally:
            repository.close()


def record_path(record):
    return "/mongo/records/" + record.model_dump(mode="json")["timestamp"]


def test_mongo_crud_routes_and_duplicate_conflict(client, sample_record):
    payload = sample_record.model_dump(mode="json")
    path = record_path(sample_record)
    assert client.get("/mongo/records/latest").status_code == 404

    created = client.post("/mongo/records", json=payload)
    assert created.status_code == 201
    assert created.json() == payload
    assert client.post("/mongo/records", json=payload).status_code == 409
    assert client.get(path).json() == payload
    assert client.get("/mongo/records/latest").json() == payload

    updated_payload = {**payload, "load_mw": payload["load_mw"] + 1000}
    updated_payload["weather"][0]["temperature_c"] += 1
    updated = client.put(path, json=updated_payload)
    assert updated.status_code == 200
    assert updated.json() == updated_payload
    assert client.get(path).json() == updated_payload

    deleted = client.delete(path)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(path).status_code == 404
    assert client.put(path, json=updated_payload).status_code == 404
    assert client.delete(path).status_code == 404
    assert client.get("/mongo/records/latest").status_code == 404


def test_mongo_date_range_pagination_and_summary(client, sample_record):
    records = [
        sample_record.model_copy(
            update={"timestamp": sample_record.timestamp + timedelta(hours=index)}
        )
        for index in range(3)
    ]
    payloads = [record.model_dump(mode="json") for record in records]
    for payload in reversed(payloads):
        assert client.post("/mongo/records", json=payload).status_code == 201

    parameters = {
        "start": payloads[0]["timestamp"],
        "end": payloads[2]["timestamp"],
    }
    ranged = client.get("/mongo/records", params=parameters)
    assert ranged.status_code == 200
    assert ranged.json() == payloads[:2]
    paginated = client.get(
        "/mongo/records", params={**parameters, "limit": 1, "offset": 1}
    )
    assert paginated.status_code == 200
    assert paginated.json() == [payloads[1]]
    assert client.get("/mongo/records/latest").json() == payloads[2]

    daily = client.get("/mongo/summary/daily", params=parameters)
    assert daily.status_code == 200
    assert sum(row["count"] for row in daily.json()) == 2
    assert all(row["avg_load_mw"] == sample_record.load_mw for row in daily.json())


def test_mongo_put_preserves_timestamp_and_rejects_invalid_range(client, sample_record):
    payload = sample_record.model_dump(mode="json")
    assert client.post("/mongo/records", json=payload).status_code == 201
    shifted = sample_record.model_copy(
        update={"timestamp": sample_record.timestamp + timedelta(hours=1)}
    ).model_dump(mode="json")
    assert client.put(record_path(sample_record), json=shifted).status_code == 422
    assert client.get(record_path(sample_record)).json() == payload

    assert (
        client.get(
            "/mongo/records",
            params={"start": shifted["timestamp"], "end": payload["timestamp"]},
        ).status_code
        == 422
    )
    assert client.get("/mongo/records", params={"limit": 0}).status_code == 422
    assert client.get("/mongo/records", params={"offset": -1}).status_code == 422
