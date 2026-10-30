"""Storage errors shared by the SQLite and MongoDB implementations."""


class DuplicateRecordError(Exception):
    """The timestamp already exists."""


class StorageUnavailableError(Exception):
    """The database operation could not complete."""
