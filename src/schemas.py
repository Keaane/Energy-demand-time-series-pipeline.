"""Shared validation for hourly observations in both databases and the API."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

City = Literal["Barcelona", "Bilbao", "Madrid", "Seville", "Valencia"]
CITIES = {"Barcelona", "Bilbao", "Madrid", "Seville", "Valencia"}


def utc_hour(value: datetime) -> datetime:
    """Require a timezone and a whole hour, then normalize to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone, for example 2018-12-31T22:00:00Z")
    value = value.astimezone(UTC)
    if value.minute or value.second or value.microsecond:
        raise ValueError("Timestamp must be aligned to a whole UTC hour")
    return value


def timestamp_key(value: datetime) -> str:
    return utc_hour(value).strftime("%Y-%m-%dT%H:%M:%SZ")


class WeatherRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    city: City
    temperature_c: float = Field(ge=-100, le=70)
    humidity_pct: float = Field(ge=0, le=100)
    pressure_hpa: float = Field(ge=900, le=1100)
    wind_speed_ms: float = Field(ge=0, le=50)
    cloud_cover_pct: float = Field(ge=0, le=100)
    rain_1h_mm: float = Field(ge=0)


class HourlyRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    timestamp: datetime
    load_mw: float = Field(gt=0)
    load_forecast_mw: float | None = Field(default=None, ge=0)
    price_actual_eur_mwh: float | None = None
    price_day_ahead_eur_mwh: float | None = None
    target_was_missing: bool = False
    weather: list[WeatherRecord] = Field(min_length=5, max_length=5)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return utc_hour(value)

    @field_validator("weather")
    @classmethod
    def validate_cities(cls, value: list[WeatherRecord]) -> list[WeatherRecord]:
        if {item.city for item in value} != CITIES:
            raise ValueError("Weather must contain each of the five cities exactly once")
        return sorted(value, key=lambda item: item.city)

    @property
    def temp_mean_c(self) -> float:
        return sum(item.temperature_c for item in self.weather) / len(self.weather)
