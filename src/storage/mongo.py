"""MongoDB storage for complete hourly energy and weather observations."""

import json
from collections.abc import Iterable
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import cast

from pymongo import ASCENDING, DESCENDING, MongoClient, ReplaceOne, ReturnDocument
from pymongo.errors import CollectionInvalid, DuplicateKeyError, PyMongoError

from src.schemas import HourlyRecord, timestamp_key, utc_hour
from src.storage import DuplicateRecordError, StorageUnavailableError

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "mongodb" / "schema.json"
IMPORT_BATCH_SIZE = 500


def _database_operation(method):
    """Keep database credentials and server details out of application errors."""

    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except DuplicateKeyError:
            raise DuplicateRecordError(
                "A record already exists for this timestamp."
            ) from None
        except PyMongoError:
            raise StorageUnavailableError(
                "MongoDB is unavailable or rejected the operation."
            ) from None

    return wrapped


def _utc(timestamp: datetime) -> datetime:
    return utc_hour(timestamp)


def _document(record: HourlyRecord) -> dict:
    document = record.model_dump(mode="python")
    document["_id"] = timestamp_key(record.timestamp)
    return document


def _record(document: dict | None) -> HourlyRecord | None:
    if document is None:
        return None
    return HourlyRecord.model_validate(
        {key: value for key, value in document.items() if key != "_id"}
    )


def _date_filter(start: datetime | None, end: datetime | None) -> dict:
    bounds = {}
    if start is not None:
        bounds["$gte"] = _utc(start)
    if end is not None:
        bounds["$lt"] = _utc(end)
    if start is not None and end is not None and _utc(start) >= _utc(end):
        raise ValueError("start must be earlier than end.")
    return {"timestamp": bounds} if bounds else {}


class MongoRepository:
    """Store each hour atomically, with its five city readings embedded."""

    @_database_operation
    def __init__(
        self,
        uri: str,
        database: str = "energy_pipeline",
        collection: str = "hourly_records",
    ):
        self.client: MongoClient[dict] = MongoClient(
            uri,
            tz_aware=True,
            serverSelectionTimeoutMS=10_000,
            connectTimeoutMS=10_000,
            socketTimeoutMS=10_000,
        )
        try:
            self.database = self.client[database]
            validator = json.loads(SCHEMA_PATH.read_text())
            try:
                self.database.create_collection(
                    collection,
                    validator=validator,
                    validationLevel="strict",
                    validationAction="error",
                )
            except CollectionInvalid:
                self.database.command(
                    "collMod",
                    collection,
                    validator=validator,
                    validationLevel="strict",
                    validationAction="error",
                )
            self.collection = self.database[collection]
            self.collection.create_index(
                [("timestamp", ASCENDING)], unique=True, name="timestamp_unique"
            )
        except Exception:
            self.client.close()
            raise

    @_database_operation
    def create(self, record: HourlyRecord) -> HourlyRecord:
        self.collection.insert_one(_document(record))
        return record

    @_database_operation
    def get(self, timestamp: datetime) -> HourlyRecord | None:
        return _record(self.collection.find_one({"timestamp": _utc(timestamp)}))

    @_database_operation
    def update(self, timestamp: datetime, record: HourlyRecord) -> HourlyRecord | None:
        if _utc(timestamp) != _utc(record.timestamp):
            raise ValueError("The record timestamp cannot be changed.")
        document = self.collection.find_one_and_replace(
            {"timestamp": _utc(timestamp)},
            _document(record),
            return_document=ReturnDocument.AFTER,
        )
        return _record(document)

    @_database_operation
    def delete(self, timestamp: datetime) -> bool:
        return (
            self.collection.delete_one({"timestamp": _utc(timestamp)}).deleted_count
            == 1
        )

    @_database_operation
    def latest(self) -> HourlyRecord | None:
        return _record(self.collection.find_one(sort=[("timestamp", DESCENDING)]))

    @_database_operation
    def list_records(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[HourlyRecord]:
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset must be nonnegative.")
        cursor = (
            self.collection.find(_date_filter(start, end))
            .sort("timestamp", ASCENDING)
            .skip(offset)
            .limit(limit)
        )
        return [cast(HourlyRecord, _record(document)) for document in cursor]

    @_database_operation
    def count(self) -> int:
        return self.collection.count_documents({})

    @_database_operation
    def daily_summary(self, start: datetime | None, end: datetime | None) -> list[dict]:
        pipeline = [
            {"$match": _date_filter(start, end)},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$timestamp",
                            "timezone": "UTC",
                        }
                    },
                    "count": {"$sum": 1},
                    "avg_load_mw": {"$avg": "$load_mw"},
                    "min_load_mw": {"$min": "$load_mw"},
                    "max_load_mw": {"$max": "$load_mw"},
                }
            },
            {"$sort": {"_id": 1}},
            {
                "$project": {
                    "_id": 0,
                    "date": "$_id",
                    "count": 1,
                    "avg_load_mw": 1,
                    "min_load_mw": 1,
                    "max_load_mw": 1,
                }
            },
        ]
        return list(self.collection.aggregate(pipeline))

    @_database_operation
    def import_records(self, records: Iterable[HourlyRecord]) -> int:
        """Refresh source records in bounded, repeatable batches of upserts."""
        batch = []
        count = 0
        for record in records:
            document = _document(record)
            batch.append(ReplaceOne({"_id": document["_id"]}, document, upsert=True))
            if len(batch) == IMPORT_BATCH_SIZE:
                self.collection.bulk_write(batch, ordered=True)
                count += len(batch)
                batch.clear()
        if batch:
            self.collection.bulk_write(batch, ordered=True)
            count += len(batch)
        return count

    def close(self) -> None:
        self.client.close()
