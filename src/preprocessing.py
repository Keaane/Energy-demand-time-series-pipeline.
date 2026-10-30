"""Prepare the notebook's modeling observations for either database.

The notebooks remain standalone. This module uses their past-value filling
policy: forward fill, then fill leading gaps from the first available value.
Calendar, lag and rolling features are built only when forecasting.
"""

from pathlib import Path

import numpy as np
import pandas as pd

TARGET = "total load actual"
CITIES = ["Barcelona", "Bilbao", "Madrid", "Seville", "Valencia"]
WEATHER_VARS = ["temp", "humidity", "pressure", "wind_speed", "clouds_all", "rain_1h"]
EMPTY_OR_ZERO_COLS = [
    "generation fossil coal-derived gas", "generation fossil oil shale",
    "generation fossil peat", "generation geothermal",
    "generation hydro pumped storage aggregated", "generation marine",
    "generation wind offshore", "forecast wind offshore eday ahead",
]
ZERO_IS_DROPOUT_COLS = [
    "generation biomass", "generation fossil gas", "generation fossil hard coal",
    "generation fossil oil", "generation hydro run-of-river and poundage",
    "generation hydro water reservoir", "generation nuclear", "generation other",
    "generation other renewable", "generation solar", "generation waste",
    "generation wind onshore",
]
ENERGY_FIELDS = {
    "load_mw": TARGET,
    "load_forecast_mw": "total load forecast",
    "price_actual_eur_mwh": "price actual",
    "price_day_ahead_eur_mwh": "price day ahead",
}
WEATHER_FIELDS = {
    "temperature_c": "temp",
    "humidity_pct": "humidity",
    "pressure_hpa": "pressure",
    "wind_speed_ms": "wind_speed",
    "cloud_cover_pct": "clouds_all",
    "rain_1h_mm": "rain_1h",
}


def _require_columns(frame, columns):
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing source columns: {', '.join(missing)}")


def _indexed_energy(raw):
    _require_columns(raw, ["time", TARGET])
    frame = raw.copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="raise")
    if frame["time"].isna().any():
        raise ValueError("Energy timestamps cannot be missing")
    return (
        frame.sort_values("time", kind="stable")
        .drop_duplicates("time", keep="first")
        .set_index("time")
    )


def clean_energy(raw):
    """Clean energy with the same past-value policy used for model training."""
    frame = _indexed_energy(raw).drop(columns=EMPTY_OR_ZERO_COLS, errors="ignore")
    dropouts = frame.columns.intersection(ZERO_IS_DROPOUT_COLS)
    frame[dropouts] = frame[dropouts].mask(frame[dropouts] == 0)
    return frame.ffill().bfill()


def clean_weather(raw):
    """Return cleaned per-city weather and five-city means, indexed by UTC hour."""
    _require_columns(raw, ["city_name", "dt_iso", *WEATHER_VARS])
    weather = raw.copy()
    weather["city_name"] = weather["city_name"].str.strip()
    if set(weather["city_name"]) != set(CITIES):
        raise ValueError(f"Weather must contain exactly these cities: {', '.join(CITIES)}")
    weather["dt_iso"] = pd.to_datetime(weather["dt_iso"], utc=True, errors="raise")
    if weather["dt_iso"].isna().any():
        raise ValueError("Weather timestamps cannot be missing")
    weather = weather.sort_values(["city_name", "dt_iso"], kind="stable")
    weather = weather.drop_duplicates(["city_name", "dt_iso"], keep="first")
    weather.loc[~weather["pressure"].between(900, 1100), "pressure"] = np.nan
    weather.loc[weather["wind_speed"] > 50, "wind_speed"] = np.nan
    weather["temp"] = weather["temp"] - 273.15

    cities = []
    for city, group in weather.groupby("city_name"):
        group = group.set_index("dt_iso")[WEATHER_VARS].ffill().bfill()
        group.columns = [f"{column}_{city}" for column in WEATHER_VARS]
        cities.append(group)
    wide = pd.concat(cities, axis=1, sort=True)
    for column in WEATHER_VARS:
        wide[f"{column}_mean"] = wide[[f"{column}_{city}" for city in CITIES]].mean(axis=1)
    wide.index.name = "time"
    return wide


def iter_records(data_dir: Path):
    """Yield identical cleaned hourly payloads for SQLite and MongoDB imports.

    Every record has five city observations. Missing source demand is filled
    for history, while ``target_was_missing`` preserves its original status.
    An incomplete hourly grid or non-finite required value is an import error.
    """
    data_dir = Path(data_dir)
    raw_energy = pd.read_csv(data_dir / "energy_dataset.csv")
    _require_columns(raw_energy, ENERGY_FIELDS.values())
    missing_target = _indexed_energy(raw_energy)[TARGET].isna()
    energy = clean_energy(raw_energy)
    if energy.empty:
        raise ValueError("Energy data cannot be empty")
    if not energy.index.equals(pd.date_range(energy.index[0], energy.index[-1], freq="h")):
        raise ValueError("Energy timestamps must form a complete hourly sequence")
    if any(energy.index.minute) or any(energy.index.second) or any(energy.index.microsecond) or any(energy.index.nanosecond):
        raise ValueError("Energy timestamps must be aligned to whole hours")

    weather = clean_weather(pd.read_csv(data_dir / "weather_features.csv"))
    merged = energy.join(weather, how="left")
    required = list(ENERGY_FIELDS.values()) + [
        f"{column}_{city}" for city in CITIES for column in WEATHER_VARS
    ]
    if not np.isfinite(merged[required].to_numpy(dtype=float)).all():
        raise ValueError("Every energy hour needs finite energy values and weather for all five cities")

    for timestamp, row in merged.iterrows():
        yield {
            "timestamp": timestamp.to_pydatetime(),
            **{field: float(row[column]) for field, column in ENERGY_FIELDS.items()},
            "target_was_missing": bool(missing_target.loc[timestamp]),
            "weather": [
                {
                    "city": city,
                    **{
                        field: float(row[f"{column}_{city}"])
                        for field, column in WEATHER_FIELDS.items()
                    },
                }
                for city in CITIES
            ],
        }
