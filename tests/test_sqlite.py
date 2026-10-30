"""Exercise SQLite transactions, relationships and time-series query boundaries."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone

import pytest

from src.schemas import HourlyRecord
from src.storage import DuplicateRecordError, StorageUnavailableError
from src.storage.sqlite import SQLiteRepository

START = datetime(2018, 12, 30, 22, tzinfo=UTC)
CITIES = ["Barcelona", "Bilbao", "Madrid", "Seville", "Valencia"]


def record(hour=0, load=25000.0):
    return HourlyRecord.model_validate(
        {
            "timestamp": START + timedelta(hours=hour),
            "load_mw": load,
            "load_forecast_mw": 26000,
            "price_actual_eur_mwh": -2.5,
            "price_day_ahead_eur_mwh": 40,
            "target_was_missing": False,
            "weather": [
                {
                    "city": city,
                    "temperature_c": 10 + index,
                    "humidity_pct": 60,
                    "pressure_hpa": 1010,
                    "wind_speed_ms": 2,
                    "cloud_cover_pct": 20,
                    "rain_1h_mm": 0,
                }
                for index, city in enumerate(CITIES)
            ],
        }
    )


@pytest.fixture
def repository(tmp_path):
    repository = SQLiteRepository(tmp_path / "energy.db")
    yield repository
    repository.close()


def test_crud_replaces_weather_and_cascades_delete(repository, tmp_path):
    original = record()
    assert repository.create(original) == original
    assert repository.get(START) == original
    replacement = original.model_copy(
        update={
            "load_mw": 30000.0,
            "weather": [
                weather.model_copy(update={"temperature_c": 20.0})
                for weather in original.weather
            ],
        }
    )
    assert repository.update(START, replacement) == replacement
    assert repository.get(START) == replacement
    assert repository.delete(START)
    assert repository.get(START) is None
    assert not repository.delete(START)
    assert repository.update(START, replacement) is None
    with sqlite3.connect(tmp_path / "energy.db") as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM weather_readings").fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM cities").fetchone()[0] == 5


def test_duplicate_does_not_overwrite_existing_record(repository):
    original = record()
    repository.create(original)
    with pytest.raises(DuplicateRecordError):
        repository.create(record(load=30000))
    assert repository.count() == 1
    assert repository.get(START) == original


def test_date_range_order_offsets_pagination_and_latest(repository):
    repository.import_records(
        [record(hour, 25000 + hour) for hour in reversed(range(5))]
    )
    assert [
        item.timestamp
        for item in repository.list_records(
            START + timedelta(hours=1), START + timedelta(hours=4), limit=10
        )
    ] == [START + timedelta(hours=hour) for hour in (1, 2, 3)]
    assert [item.timestamp for item in repository.list_records(limit=2, offset=2)] == [
        START + timedelta(hours=hour) for hour in (2, 3)
    ]
    local_start = START.astimezone(timezone(timedelta(hours=2)))
    assert repository.get(local_start) == record()
    assert repository.latest() == record(4, 25004)
    with pytest.raises(ValueError, match="whole UTC hour"):
        repository.list_records(
            START + timedelta(minutes=30), START + timedelta(hours=2)
        )
    with pytest.raises(ValueError, match="earlier"):
        repository.list_records(START, START)


def test_daily_summary_is_utc_and_observation_count_is_explicit(repository):
    repository.import_records([record(hour, 10 + hour) for hour in range(5)])
    assert repository.daily_summary() == [
        {
            "date": "2018-12-30",
            "count": 2,
            "avg_load_mw": 10.5,
            "min_load_mw": 10.0,
            "max_load_mw": 11.0,
        },
        {
            "date": "2018-12-31",
            "count": 3,
            "avg_load_mw": 13.0,
            "min_load_mw": 12.0,
            "max_load_mw": 14.0,
        },
    ]


def test_import_is_repeatable_and_upserts_without_duplicate_weather(
    repository, tmp_path
):
    records = [record(hour) for hour in range(3)]
    assert repository.import_records(iter(records)) == 3
    assert repository.import_records(records) == 3
    assert repository.import_records([record(1, 32000)]) == 1
    assert repository.count() == 3
    assert repository.get(START + timedelta(hours=1)).load_mw == 32000
    with sqlite3.connect(tmp_path / "energy.db") as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM weather_readings").fetchone()[0]
            == 15
        )


def test_failed_update_rolls_back_energy_and_weather(repository):
    original = record()
    repository.create(original)
    invalid = original.model_copy(
        update={
            "load_mw": 30000.0,
            "weather": [
                weather.model_copy(update={"temperature_c": 1000.0})
                for weather in original.weather
            ],
        }
    )
    with pytest.raises(StorageUnavailableError, match="invalid"):
        repository.update(START, invalid)
    assert repository.get(START) == original


def test_failed_import_rolls_back_previous_batches(repository):
    def failing_input():
        yield from (record(hour) for hour in range(1001))
        raise ValueError("Source failed")

    with pytest.raises(ValueError, match="Source failed"):
        repository.import_records(failing_input())
    assert repository.count() == 0


def test_timestamp_is_immutable_and_empty_queries_are_consistent(repository):
    assert repository.latest() is None
    assert repository.list_records() == []
    assert repository.daily_summary() == []
    repository.create(record())
    with pytest.raises(ValueError, match="timestamp"):
        repository.update(START, record(1))
    assert repository.get(START) == record()


def test_in_memory_repository_supports_threaded_requests():
    repository = SQLiteRepository(":memory:")
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(repository.create, [record(hour) for hour in range(20)]))
            assert list(pool.map(repository.get, [START] * 4)) == [record()] * 4
        assert repository.count() == 20
    finally:
        repository.close()
    with pytest.raises(StorageUnavailableError, match="closed"):
        repository.count()
