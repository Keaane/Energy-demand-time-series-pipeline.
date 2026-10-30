"""SQLite persistence for hourly energy and five-city weather observations."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from itertools import islice
from pathlib import Path
from threading import RLock

from src.schemas import HourlyRecord
from src.schemas import timestamp_key as _timestamp
from src.storage import DuplicateRecordError, StorageUnavailableError

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"
ENERGY_COLUMNS = (
    "timestamp",
    "load_mw",
    "load_forecast_mw",
    "price_actual_eur_mwh",
    "price_day_ahead_eur_mwh",
    "target_was_missing",
)
WEATHER_COLUMNS = (
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "wind_speed_ms",
    "cloud_cover_pct",
    "rain_1h_mm",
)
INSERT_ENERGY = """
    INSERT INTO energy_readings
        (timestamp, load_mw, load_forecast_mw, price_actual_eur_mwh,
         price_day_ahead_eur_mwh, target_was_missing)
    VALUES (?, ?, ?, ?, ?, ?)
"""
UPSERT_ENERGY = (
    INSERT_ENERGY
    + """
    ON CONFLICT(timestamp) DO UPDATE SET
        load_mw = excluded.load_mw,
        load_forecast_mw = excluded.load_forecast_mw,
        price_actual_eur_mwh = excluded.price_actual_eur_mwh,
        price_day_ahead_eur_mwh = excluded.price_day_ahead_eur_mwh,
        target_was_missing = excluded.target_was_missing
"""
)
INSERT_WEATHER = """
    INSERT INTO weather_readings
        (timestamp, city_id, temperature_c, humidity_pct, pressure_hpa,
         wind_speed_ms, cloud_cover_pct, rain_1h_mm)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def _energy_values(record: HourlyRecord) -> tuple:
    return (
        _timestamp(record.timestamp),
        record.load_mw,
        record.load_forecast_mw,
        record.price_actual_eur_mwh,
        record.price_day_ahead_eur_mwh,
        int(record.target_was_missing),
    )


class SQLiteRepository:
    """Serialize connection access so the same repository is safe across threads."""

    def __init__(self, path: str | Path):
        self._lock = RLock()
        self._closed = False
        try:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(str(path), check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._connection.executescript(SCHEMA_PATH.read_text())
            self._cities = {
                row["name"]: row["id"]
                for row in self._connection.execute("SELECT id, name FROM cities")
            }
        except (OSError, sqlite3.Error) as exc:
            raise StorageUnavailableError(
                "SQLite database could not be opened."
            ) from exc

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._closed:
                raise StorageUnavailableError("SQLite database is closed.")
            try:
                with self._connection:
                    yield self._connection
            except sqlite3.IntegrityError as exc:
                if exc.sqlite_errorcode in (
                    sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
                    sqlite3.SQLITE_CONSTRAINT_UNIQUE,
                ):
                    raise DuplicateRecordError(
                        "A record already exists for this hour."
                    ) from exc
                raise StorageUnavailableError(
                    "SQLite rejected an invalid record."
                ) from exc
            except sqlite3.Error as exc:
                raise StorageUnavailableError(
                    "SQLite database operation failed."
                ) from exc

    def _weather_values(self, record: HourlyRecord):
        timestamp = _timestamp(record.timestamp)
        for weather in record.weather:
            yield (
                timestamp,
                self._cities[weather.city],
                *(getattr(weather, name) for name in WEATHER_COLUMNS),
            )

    @staticmethod
    def _records(connection, rows) -> list[HourlyRecord]:
        """Read weather for the complete page in one query instead of one per hour."""
        rows = list(rows)
        if not rows:
            return []
        weather = {row["timestamp"]: [] for row in rows}
        # A page may exceed SQLite's bind-parameter limit; use contiguous bounds.
        weather_rows = connection.execute(
            """SELECT w.*, c.name AS city FROM weather_readings AS w
               JOIN cities AS c ON c.id = w.city_id
               WHERE w.timestamp >= ? AND w.timestamp <= ?
               ORDER BY w.timestamp, c.name""",
            (min(weather), max(weather)),
        )
        for row in weather_rows:
            if row["timestamp"] in weather:
                weather[row["timestamp"]].append(
                    {
                        "city": row["city"],
                        **{name: row[name] for name in WEATHER_COLUMNS},
                    }
                )
        return [
            HourlyRecord.model_validate(
                {
                    **{name: row[name] for name in ENERGY_COLUMNS},
                    "weather": weather[row["timestamp"]],
                }
            )
            for row in rows
        ]

    def create(self, record: HourlyRecord) -> HourlyRecord:
        with self._transaction() as connection:
            connection.execute(INSERT_ENERGY, _energy_values(record))
            connection.executemany(INSERT_WEATHER, self._weather_values(record))
        return record

    def get(self, timestamp: datetime) -> HourlyRecord | None:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM energy_readings WHERE timestamp = ?",
                (_timestamp(timestamp),),
            )
            records = self._records(connection, rows)
        return records[0] if records else None

    def update(self, timestamp: datetime, record: HourlyRecord) -> HourlyRecord | None:
        key = _timestamp(timestamp)
        if key != _timestamp(record.timestamp):
            raise ValueError("An update cannot change the record timestamp.")
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE energy_readings SET
                   load_mw = ?, load_forecast_mw = ?, price_actual_eur_mwh = ?,
                   price_day_ahead_eur_mwh = ?, target_was_missing = ?
                   WHERE timestamp = ?""",
                (*_energy_values(record)[1:], key),
            )
            if not cursor.rowcount:
                return None
            connection.execute(
                "DELETE FROM weather_readings WHERE timestamp = ?", (key,)
            )
            connection.executemany(INSERT_WEATHER, self._weather_values(record))
        return record

    def delete(self, timestamp: datetime) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM energy_readings WHERE timestamp = ?",
                (_timestamp(timestamp),),
            )
        return bool(cursor.rowcount)

    def latest(self) -> HourlyRecord | None:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM energy_readings ORDER BY timestamp DESC LIMIT 1"
            )
            records = self._records(connection, rows)
        return records[0] if records else None

    @staticmethod
    def _range(start, end):
        clauses, parameters = [], []
        if start is not None:
            clauses.append("timestamp >= ?")
            parameters.append(_timestamp(start))
        if end is not None:
            clauses.append("timestamp < ?")
            parameters.append(_timestamp(end))
        if start is not None and end is not None and start >= end:
            raise ValueError("start must be earlier than end.")
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), parameters

    def list_records(
        self, start=None, end=None, limit=100, offset=0
    ) -> list[HourlyRecord]:
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset must be non-negative.")
        where, parameters = self._range(start, end)
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM energy_readings"
                + where
                + " ORDER BY timestamp LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            )
            return self._records(connection, rows)

    def count(self) -> int:
        with self._transaction() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM energy_readings"
            ).fetchone()[0]

    def daily_summary(self, start=None, end=None) -> list[dict]:
        where, parameters = self._range(start, end)
        with self._transaction() as connection:
            rows = connection.execute(
                """SELECT substr(timestamp, 1, 10) AS date, COUNT(*) AS count,
                   AVG(load_mw) AS avg_load_mw, MIN(load_mw) AS min_load_mw,
                   MAX(load_mw) AS max_load_mw FROM energy_readings"""
                + where
                + " GROUP BY substr(timestamp, 1, 10) ORDER BY date",
                parameters,
            )
            return [dict(row) for row in rows]

    def import_records(self, records) -> int:
        """Upsert in batches within one transaction; return the number processed."""
        count = 0
        iterator = iter(records)
        with self._transaction() as connection:
            while batch := list(islice(iterator, 1000)):
                batch = [HourlyRecord.model_validate(record) for record in batch]
                connection.executemany(UPSERT_ENERGY, map(_energy_values, batch))
                connection.executemany(
                    "DELETE FROM weather_readings WHERE timestamp = ?",
                    ((_timestamp(record.timestamp),) for record in batch),
                )
                connection.executemany(
                    INSERT_WEATHER,
                    (row for record in batch for row in self._weather_values(record)),
                )
                count += len(batch)
        return count

    def close(self):
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True
