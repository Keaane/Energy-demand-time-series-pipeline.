"""Construct either storage implementation from the same settings."""

from typing import Literal

from src.config import Settings
from src.storage import StorageUnavailableError
from src.storage.mongo import MongoRepository
from src.storage.sqlite import SQLiteRepository

Backend = Literal["sql", "mongo"]


def open_repository(backend: Backend, settings: Settings):
    if backend == "sql":
        return SQLiteRepository(settings.sqlite_path)
    if backend == "mongo":
        if not settings.mongodb_uri or not settings.mongodb_uri.get_secret_value():
            raise StorageUnavailableError(
                "Set MONGODB_URI in .env to connect to Atlas."
            )
        return MongoRepository(
            settings.mongodb_uri.get_secret_value(),
            settings.mongodb_database,
            settings.mongodb_collection,
        )
    raise ValueError("backend must be sql or mongo")
