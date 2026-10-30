"""Environment-based configuration; local secrets stay in .env."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    sqlite_path: Path = ROOT / "data" / "energy.db"
    mongodb_uri: SecretStr | None = None
    mongodb_database: str = "energy_pipeline"
    mongodb_collection: str = "hourly_records"
