"""Load the saved model and forecast the next hour of electricity demand.

Run from the project folder with:  python -m src.predict
"""
from pathlib import Path

import joblib
import pandas as pd

from src.features import FEATURES, build_features

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "models" / "best_rf_model.joblib"


def load_model(path=MODEL_PATH):
    """The saved file is a dictionary with the fitted model and its feature list."""
    bundle = joblib.load(path)
    if list(bundle["features"]) != FEATURES:
        raise ValueError("The saved model uses different features than src/features.py.")
    return bundle["model"]


def predict_next_hour(model, loads, temp_mean, when):
    """Forecast the load (MW) for the hour `when` from the previous 168 hourly loads."""
    return float(model.predict(build_features(loads, temp_mean, when))[0])


if __name__ == "__main__":
    # forecast the last hour of the sample file from the hours before it
    sample = pd.read_csv(ROOT / "data" / "sample_recent_hours.csv", index_col="time", parse_dates=True)
    when = sample.index[-1]
    history = sample["total load actual"].iloc[:-1]
    forecast = predict_next_hour(load_model(), history, sample["temp_mean"].iloc[-1], when)
    print(f"Forecast for {when}: {forecast:,.0f} MW | actual: {sample['total load actual'].iloc[-1]:,.0f} MW")
