"""Execute three comparable queries and optionally save their actual results."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from src.config import Settings
from src.database import Backend, open_repository
from src.schemas import timestamp_key
from src.storage import StorageUnavailableError

RANGE_START = datetime(2018, 12, 31, 18, tzinfo=UTC)
RANGE_END = datetime(2018, 12, 31, 22, tzinfo=UTC)
SUMMARY_START = datetime(2018, 12, 29, tzinfo=UTC)
SUMMARY_END = datetime(2019, 1, 1, tzinfo=UTC)


def _display(record):
    if record is None:
        return None
    return {
        "timestamp": timestamp_key(record.timestamp),
        "load_mw": record.load_mw,
        "load_forecast_mw": record.load_forecast_mw,
        "price_actual_eur_mwh": record.price_actual_eur_mwh,
        "price_day_ahead_eur_mwh": record.price_day_ahead_eur_mwh,
        "target_was_missing": record.target_was_missing,
        "temp_mean_c": record.temp_mean_c,
    }


def run_queries(repository):
    return {
        "record_count": repository.count(),
        "latest": _display(repository.latest()),
        "range": {
            "start": timestamp_key(RANGE_START),
            "end_exclusive": timestamp_key(RANGE_END),
            "records": [
                _display(row) for row in repository.list_records(RANGE_START, RANGE_END)
            ],
        },
        "daily_summary": {
            "start": timestamp_key(SUMMARY_START),
            "end_exclusive": timestamp_key(SUMMARY_END),
            "records": repository.daily_summary(SUMMARY_START, SUMMARY_END),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["sql", "mongo", "both"], required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings()
    results: dict = {"executed_at_utc": datetime.now(UTC).isoformat(), "databases": {}}
    backends: list[Backend] = (
        ["sql", "mongo"] if args.backend == "both" else [args.backend]
    )
    try:
        for backend in backends:
            repository = open_repository(backend, settings)
            try:
                results["databases"][backend] = run_queries(repository)
            finally:
                repository.close()
        output = json.dumps(results, indent=2, allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n")
        print(output)
    except (StorageUnavailableError, OSError, ValueError):
        parser.exit(1, "Queries failed: check database settings and connectivity.\n")


if __name__ == "__main__":
    main()
