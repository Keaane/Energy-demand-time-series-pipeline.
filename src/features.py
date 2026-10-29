"""Build the model's input features for one hour.

The training notebooks create these columns for the whole series. This file builds
the same columns for a single hour, so a forecast script can use the saved model
without copying the logic.
"""
import numpy as np
import pandas as pd

LOCAL_TZ = "Europe/Madrid"
COMFORT_TEMP = 17          # demand is lowest near 17 degrees C (see the EDA, Question 5)
HISTORY_HOURS = 168        # one week of hourly loads

FEATURES = [
    "hour_sin", "hour_cos", "dayofweek", "month", "is_weekend",
    "load_lag_1", "load_lag_24", "load_lag_168", "load_ma_24", "load_ma_168",
    "temp_mean", "heating_deg", "cooling_deg",
]


def build_features(loads, temp_mean, when):
    """Return a one-row DataFrame with the model features for the hour `when`.

    loads      hourly load in MW, a pandas Series indexed by UTC timestamps. It must
               hold every hour from `when` minus 168 hours up to `when` minus 1 hour.
    temp_mean  mean temperature of the five cities in degrees C, for the hour `when`.
    when       the hour to forecast, a timestamp (naive values are read as UTC).
    """
    when = pd.Timestamp(when)
    when = when.tz_localize("UTC") if when.tzinfo is None else when.tz_convert("UTC")

    needed = pd.date_range(when - pd.Timedelta(hours=HISTORY_HOURS), when - pd.Timedelta(hours=1), freq="h")
    loads = loads.copy()
    loads.index = pd.to_datetime(loads.index, utc=True)
    window = loads.reindex(needed)
    if window.isna().any():
        raise ValueError(
            f"Need the load for every hour from {needed[0]} to {needed[-1]} (UTC), "
            f"but {int(window.isna().sum())} of {len(needed)} hours are missing."
        )

    local = when.tz_convert(LOCAL_TZ)
    row = {
        "hour_sin": np.sin(2 * np.pi * local.hour / 24),
        "hour_cos": np.cos(2 * np.pi * local.hour / 24),
        "dayofweek": local.dayofweek,                 # 0 = Monday
        "month": local.month,
        "is_weekend": int(local.dayofweek >= 5),
        "load_lag_1": window.iloc[-1],
        "load_lag_24": window.iloc[-24],
        "load_lag_168": window.iloc[0],
        "load_ma_24": window.iloc[-24:].mean(),       # the previous 24 hours, not including `when`
        "load_ma_168": window.mean(),
        "temp_mean": temp_mean,
        "heating_deg": max(COMFORT_TEMP - temp_mean, 0),
        "cooling_deg": max(temp_mean - COMFORT_TEMP, 0),
    }
    return pd.DataFrame([row], index=[when])[FEATURES]
