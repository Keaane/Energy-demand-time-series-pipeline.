"""One CRUD interface backed by either SQLite or MongoDB Atlas."""

from contextlib import asynccontextmanager
from datetime import datetime
from threading import Lock
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AfterValidator

from src.config import Settings
from src.database import Backend, open_repository
from src.schemas import HourlyRecord, utc_hour
from src.storage import DuplicateRecordError, StorageUnavailableError

Hour = Annotated[datetime, AfterValidator(utc_hour)]


def _check_range(start, end):
    if start is not None and end is not None and start >= end:
        raise HTTPException(422, "start must be earlier than end")


def _found(record):
    if record is None:
        raise HTTPException(404, "Record not found")
    return record


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            for repository in application.state.repositories.values():
                repository.close()
            application.state.repositories.clear()

    application = FastAPI(
        title="Energy demand API",
        description="Hourly Spain electricity demand and five-city weather. All times are UTC.",
        lifespan=lifespan,
    )
    application.state.settings = settings if settings is not None else Settings()
    application.state.repositories = {}
    application.state.repository_lock = Lock()

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Invalid inputs can contain NaN or exception objects that cannot be JSON encoded.
        details = [
            {key: item[key] for key in ("type", "loc", "msg")}
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details})

    def repository_for(backend, request):
        with request.app.state.repository_lock:
            repositories = request.app.state.repositories
            if backend not in repositories:
                repositories[backend] = open_repository(
                    backend, request.app.state.settings
                )
            return repositories[backend]

    @application.exception_handler(DuplicateRecordError)
    async def duplicate_error(request: Request, error: DuplicateRecordError):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @application.exception_handler(StorageUnavailableError)
    async def storage_error(request: Request, error: StorageUnavailableError):
        return JSONResponse(status_code=503, content={"detail": str(error)})

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.post(
        "/{backend}/records", response_model=HourlyRecord, status_code=201
    )
    def create_record(backend: Backend, record: HourlyRecord, request: Request):
        return repository_for(backend, request).create(record)

    # Register static paths before the timestamp route.
    @application.get("/{backend}/records/latest", response_model=HourlyRecord)
    def latest_record(backend: Backend, request: Request):
        return _found(repository_for(backend, request).latest())

    @application.get("/{backend}/records", response_model=list[HourlyRecord])
    def list_records(
        backend: Backend,
        request: Request,
        start: Hour | None = None,
        end: Hour | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        _check_range(start, end)
        return repository_for(backend, request).list_records(start, end, limit, offset)

    @application.get("/{backend}/summary/daily")
    def daily_summary(
        backend: Backend,
        request: Request,
        start: Hour | None = None,
        end: Hour | None = None,
    ):
        _check_range(start, end)
        return repository_for(backend, request).daily_summary(start, end)

    @application.get("/{backend}/records/{timestamp}", response_model=HourlyRecord)
    def get_record(backend: Backend, timestamp: Hour, request: Request):
        return _found(repository_for(backend, request).get(timestamp))

    @application.put("/{backend}/records/{timestamp}", response_model=HourlyRecord)
    def update_record(
        backend: Backend, timestamp: Hour, record: HourlyRecord, request: Request
    ):
        if timestamp != record.timestamp:
            raise HTTPException(422, "The record timestamp cannot be changed")
        return _found(repository_for(backend, request).update(timestamp, record))

    @application.delete("/{backend}/records/{timestamp}", status_code=204)
    def delete_record(backend: Backend, timestamp: Hour, request: Request):
        if not repository_for(backend, request).delete(timestamp):
            raise HTTPException(404, "Record not found")
        return Response(status_code=204)

    return application


app = create_app()
