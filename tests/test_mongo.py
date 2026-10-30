"""Integration tests use a disposable collection on a real MongoDB server."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pymongo.errors import OperationFailure

from src.schemas import HourlyRecord
from src.storage import DuplicateRecordError
from src.storage.mongo import MongoRepository


def make_record(timestamp=None, load=25_000):
    return HourlyRecord.model_validate(
        {
            "timestamp": timestamp or datetime(2018, 12, 30, tzinfo=UTC),
            "load_mw": load,
            "load_forecast_mw": 24_900,
            "price_actual_eur_mwh": 65,
            "price_day_ahead_eur_mwh": 60,
            "target_was_missing": False,
            "weather": [
                {
                    "city": city,
                    "temperature_c": 12,
                    "humidity_pct": 70,
                    "pressure_hpa": 1015,
                    "wind_speed_ms": 3,
                    "cloud_cover_pct": 10,
                    "rain_1h_mm": 0,
                }
                for city in ["Barcelona", "Bilbao", "Madrid", "Seville", "Valencia"]
            ],
        }
    )


@pytest.fixture
def repository():
    uri = os.environ.get("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("Set TEST_MONGODB_URI to run integration tests on real MongoDB.")
    collection = "test_hourly_" + uuid4().hex
    repository = MongoRepository(
        uri,
        database=os.environ.get("TEST_MONGODB_DATABASE", "energy_pipeline_tests"),
        collection=collection,
    )
    try:
        yield repository
    finally:
        try:
            repository.database.drop_collection(collection)
        finally:
            repository.close()


def test_crud_and_duplicate_timestamp(repository):
    record = make_record()
    assert repository.latest() is None
    assert repository.create(record) == record
    assert repository.get(record.timestamp) == record
    with pytest.raises(DuplicateRecordError):
        repository.create(record)

    updated = record.model_copy(update={"load_mw": 26_000})
    assert repository.update(record.timestamp, updated) == updated
    assert repository.get(record.timestamp).load_mw == 26_000
    assert repository.count() == 1
    assert repository.delete(record.timestamp)
    assert repository.get(record.timestamp) is None
    assert repository.update(record.timestamp, updated) is None
    assert not repository.delete(record.timestamp)


def test_date_range_order_pagination_and_daily_summary(repository):
    first = make_record()
    second = make_record(first.timestamp + timedelta(hours=1), load=27_000)
    third = make_record(first.timestamp + timedelta(days=1), load=29_000)
    repository.import_records([third, first, second])

    assert repository.latest() == third
    assert repository.list_records(first.timestamp, second.timestamp) == [first]
    assert repository.list_records(limit=1, offset=1) == [second]
    assert repository.list_records(third.timestamp + timedelta(hours=1)) == []
    assert repository.daily_summary(first.timestamp, third.timestamp) == [
        {
            "date": "2018-12-30",
            "count": 2,
            "avg_load_mw": 26_000,
            "min_load_mw": 25_000,
            "max_load_mw": 27_000,
        }
    ]


def test_import_is_repeatable_and_refreshes_embedded_weather(repository):
    record = make_record()
    assert repository.import_records([record]) == 1
    weather = record.weather[0].model_copy(update={"temperature_c": 15})
    changed = record.model_copy(update={"weather": [weather, *record.weather[1:]]})
    assert repository.import_records([changed]) == 1
    assert repository.count() == 1
    assert repository.get(record.timestamp) == changed


def test_timestamp_is_immutable(repository):
    record = make_record()
    repository.create(record)
    changed = record.model_copy(
        update={"timestamp": record.timestamp + timedelta(hours=1)}
    )
    with pytest.raises(ValueError, match="timestamp cannot be changed"):
        repository.update(record.timestamp, changed)
    assert repository.get(record.timestamp) == record


def test_server_rejects_invalid_documents(repository):
    record = make_record()
    document = record.model_dump(mode="python")
    document["_id"] = record.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    document["weather"][1]["city"] = document["weather"][0]["city"]
    with pytest.raises(OperationFailure) as error:
        repository.collection.insert_one(document)
    assert error.value.code == 121
    assert repository.count() == 0
