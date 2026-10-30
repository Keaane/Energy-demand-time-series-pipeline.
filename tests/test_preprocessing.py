"""Check cleaning against source observations and the existing modeling notebook."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.preprocessing import CITIES, clean_energy, clean_weather, iter_records

ROOT = Path(__file__).resolve().parents[1]


def energy_frame(times=None):
    return pd.DataFrame({
        "time": times if times is not None else pd.date_range("2018-01-01", periods=3, freq="h", tz="UTC"),
        "total load actual": [100.0, np.nan, 300.0],
        "total load forecast": [110.0, 220.0, 330.0],
        "price actual": [50.0, 51.0, 52.0],
        "price day ahead": [49.0, 50.0, 51.0],
    })


def weather_frame():
    return pd.DataFrame([
        {
            "dt_iso": timestamp,
            "city_name": city,
            "temp": 280.15,
            "humidity": 50.0,
            "pressure": 1000.0,
            "wind_speed": 2.0,
            "clouds_all": 20.0,
            "rain_1h": 0.0,
        }
        for city in CITIES
        for timestamp in pd.date_range("2018-01-01", periods=3, freq="h", tz="UTC")
    ])


def write_sources(directory, energy=None, weather=None):
    (energy if energy is not None else energy_frame()).to_csv(directory / "energy_dataset.csv", index=False)
    (weather if weather is not None else weather_frame()).to_csv(directory / "weather_features.csv", index=False)


def test_energy_uses_past_values_and_only_backfills_leading_gaps():
    raw = pd.DataFrame({
        "time": pd.date_range("2018-01-01", periods=4, freq="h", tz="UTC"),
        "total load actual": [np.nan, 100.0, np.nan, 200.0],
        "generation biomass": [10.0, 0.0, 20.0, 30.0],
        "generation geothermal": [0.0] * 4,
    })
    cleaned = clean_energy(raw)
    assert cleaned["total load actual"].tolist() == [100.0, 100.0, 100.0, 200.0]
    assert cleaned["generation biomass"].tolist() == [10.0, 10.0, 20.0, 30.0]
    assert "generation geothermal" not in cleaned


def test_utc_dedup_keeps_first_observation_and_distinguishes_dst_hours():
    raw = pd.DataFrame({
        "time": ["2018-10-28T02:00:00+01:00", "2018-10-28T02:00:00+02:00", "2018-10-28T00:00:00Z"],
        "total load actual": [200.0, 100.0, 999.0],
    })
    cleaned = clean_energy(raw)
    assert cleaned.index.tolist() == list(pd.date_range("2018-10-28", periods=2, freq="h", tz="UTC"))
    assert cleaned["total load actual"].tolist() == [100.0, 200.0]


def test_weather_cleans_outliers_and_duplicates_before_city_means():
    raw = weather_frame()
    raw.loc[0, "city_name"] = " Barcelona"
    raw.loc[1, ["pressure", "wind_speed", "temp"]] = [999999.0, 100.0, np.nan]
    duplicate = raw.iloc[[0]].assign(temp=350.0)
    cleaned = clean_weather(pd.concat([raw, duplicate], ignore_index=True))
    assert len(cleaned) == 3
    assert cleaned["temp_Barcelona"].tolist() == pytest.approx([7.0, 7.0, 7.0])
    assert cleaned["temp_mean"].tolist() == pytest.approx([7.0, 7.0, 7.0])
    assert cleaned["pressure_Barcelona"].tolist() == [1000.0] * 3
    assert cleaned["wind_speed_Barcelona"].tolist() == [2.0] * 3


def test_import_preserves_imputed_target_flag_and_five_city_weather(tmp_path):
    write_sources(tmp_path)
    records = list(iter_records(tmp_path))
    assert [record["load_mw"] for record in records] == [100.0, 100.0, 300.0]
    assert [record["target_was_missing"] for record in records] == [False, True, False]
    assert records[0]["timestamp"].isoformat() == "2018-01-01T00:00:00+00:00"
    assert [city["city"] for city in records[0]["weather"]] == CITIES
    assert all(city["temperature_c"] == pytest.approx(7.0) for city in records[0]["weather"])


def test_import_rejects_missing_energy_hour(tmp_path):
    energy = energy_frame().drop(index=1)
    write_sources(tmp_path, energy=energy)
    with pytest.raises(ValueError, match="complete hourly sequence"):
        list(iter_records(tmp_path))


def test_import_rejects_missing_city_weather_for_an_hour(tmp_path):
    weather = weather_frame().drop(index=1)
    write_sources(tmp_path, weather=weather)
    with pytest.raises(ValueError, match="weather for all five cities"):
        list(iter_records(tmp_path))


def test_import_rejects_nonfinite_values(tmp_path):
    energy = energy_frame()
    energy.loc[1, "price actual"] = np.inf
    write_sources(tmp_path, energy=energy)
    with pytest.raises(ValueError, match="finite energy values"):
        list(iter_records(tmp_path))


def test_cleaning_matches_modeling_notebook_on_full_source_files():
    notebook = json.loads((ROOT / "notebooks" / "01_preprocessing_eda.ipynb").read_text())
    definitions = next(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "code" and "def clean_energy(" in "".join(cell["source"])
    )
    namespace = {"pd": pd, "np": np}
    # Execute only the repository's cleaning definitions, without notebook I/O.
    exec(compile(definitions, "notebook_cleaning_definitions", "exec"), namespace)  # noqa: S102
    raw_energy = pd.read_csv(ROOT / "data" / "energy_dataset.csv")
    raw_weather = pd.read_csv(ROOT / "data" / "weather_features.csv")
    pd.testing.assert_frame_equal(clean_energy(raw_energy), namespace["clean_energy"](raw_energy, fill="past"))
    pd.testing.assert_frame_equal(clean_weather(raw_weather), namespace["clean_weather"](raw_weather, fill="past"))
