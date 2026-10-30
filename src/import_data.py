"""Load the cleaned source dataset into SQLite, Atlas, or both."""

import argparse
import json

from src.config import ROOT, Settings
from src.database import Backend, open_repository
from src.preprocessing import iter_records
from src.schemas import HourlyRecord
from src.storage import StorageUnavailableError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["sql", "mongo", "both"], required=True)
    parser.add_argument("--data-dir", type=str, default=str(ROOT / "data"))
    args = parser.parse_args()
    try:
        # Validate the entire source before any MongoDB batch is written.
        records = [
            HourlyRecord.model_validate(row) for row in iter_records(args.data_dir)
        ]
        settings = Settings()
        results = {}
        backends: list[Backend] = (
            ["sql", "mongo"] if args.backend == "both" else [args.backend]
        )
        for backend in backends:
            repository = open_repository(backend, settings)
            try:
                results[backend] = {
                    "processed": repository.import_records(records),
                    "stored": repository.count(),
                }
            finally:
                repository.close()
        print(json.dumps(results, indent=2))
    except StorageUnavailableError as error:
        parser.exit(1, f"Import failed: {error}\n")
    except (ValueError, OSError):
        parser.exit(
            1, "Import failed: check source files, record values, and local settings.\n"
        )


if __name__ == "__main__":
    main()
