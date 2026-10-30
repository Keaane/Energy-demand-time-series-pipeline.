"""Fetch hourly API observations and run the existing next-hour demand model.

Historical example::

    python -m src.forecast --backend sql --when 2018-12-31T22:00:00Z

Forecast after the latest observation::

    python -m src.forecast --backend mongo --temp-mean 6.5
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta

import httpx
import pandas as pd
from pydantic import ValidationError

from src.features import HISTORY_HOURS
from src.predict import MODEL_PATH, load_model, predict_next_hour
from src.schemas import HourlyRecord, timestamp_key, utc_hour


def _get_json(client, path, *, params=None, allow_missing=False):
    try:
        response = client.get(path, params=params)
    except httpx.RequestError:
        raise ValueError(
            "Could not reach the API; check its address and that it is running"
        ) from None
    if response.status_code == 404 and allow_missing:
        return None
    if not response.is_success:
        raise ValueError(f"API request failed with HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise ValueError("API returned invalid JSON") from None


def _record(payload):
    try:
        return HourlyRecord.model_validate(payload)
    except (ValidationError, TypeError):
        raise ValueError("API returned an invalid hourly record") from None


def _forecast_hour(value):
    try:
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
        return utc_hour(value)
    except (ValueError, TypeError, AttributeError):
        raise ValueError(
            "Forecast time must be a whole hour with a timezone, for example 2018-12-31T22:00:00Z"
        ) from None


def _temperature(value):
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ValueError(
            "Mean temperature must be a finite number between -100 and 70 degrees Celsius"
        ) from None
    if not math.isfinite(value) or not -100 <= value <= 70:
        raise ValueError(
            "Mean temperature must be a finite number between -100 and 70 degrees Celsius"
        )
    return value


def forecast_from_api(
    client, backend, *, when=None, temp_mean=None, model_path=MODEL_PATH
):
    """Predict one hour using 168 preceding records fetched from an API client.

    A historical target contributes its temperature and optional evaluation
    value. Its demand is never included in the model's history. A future hour
    needs an explicitly supplied expected mean temperature across five cities.
    """
    if backend not in {"sql", "mongo"}:
        raise ValueError("Backend must be sql or mongo")
    if temp_mean is not None:
        temp_mean = _temperature(temp_mean)

    prefix = f"/{backend}/records"
    target = None
    if when is None:
        if temp_mean is None:
            raise ValueError("A future forecast requires --temp-mean for the next hour")
        latest = _record(_get_json(client, f"{prefix}/latest"))
        when = latest.timestamp + timedelta(hours=1)
    else:
        when = _forecast_hour(when)
        payload = _get_json(
            client, f"{prefix}/{timestamp_key(when)}", allow_missing=True
        )
        if payload is not None:
            target = _record(payload)
            if target.timestamp != when:
                raise ValueError(
                    "API returned a different hour than the requested forecast time"
                )
        elif temp_mean is None:
            raise ValueError(
                "No record exists for the forecast hour; supply --temp-mean for a future forecast"
            )

    if temp_mean is None:
        if target is None:
            raise ValueError("A forecast without a stored target hour requires --temp-mean")
        temp_mean = target.temp_mean_c

    start = when - timedelta(hours=HISTORY_HOURS)
    payload = _get_json(
        client,
        prefix,
        params={
            "start": timestamp_key(start),
            "end": timestamp_key(when),
            "limit": HISTORY_HOURS,
        },
    )
    if not isinstance(payload, list):
        raise ValueError("API history must be a list of hourly records")  # noqa: TRY004
    history = [_record(item) for item in payload]
    timestamps = [record.timestamp for record in history]
    if len(set(timestamps)) != len(timestamps):
        raise ValueError("API history contains duplicate hours")
    if len(history) != HISTORY_HOURS:
        raise ValueError(
            f"Need exactly {HISTORY_HOURS} preceding hourly records; received {len(history)}"
        )
    expected = {start + timedelta(hours=offset) for offset in range(HISTORY_HOURS)}
    if set(timestamps) != expected:
        raise ValueError(
            f"API history must contain every hour from {timestamp_key(start)} up to {timestamp_key(when)} (exclusive)"
        )
    loads = pd.Series(
        [record.load_mw for record in history],
        index=pd.DatetimeIndex(timestamps),
        dtype=float,
    ).sort_index()
    model = load_model(model_path)
    prediction = predict_next_hour(model, loads, temp_mean, when)
    if not math.isfinite(prediction):
        raise ValueError("Model returned a non-finite prediction")

    result = {
        "forecast_timestamp": timestamp_key(when),
        "predicted_load_mw": prediction,
        "backend": backend,
        "history_hours": len(history),
        "temp_mean_c": temp_mean,
        "mode": "historical" if target is not None else "future",
    }
    if target is not None and not target.target_was_missing:
        result["actual_load_mw"] = target.load_mw
        result["absolute_error_mw"] = abs(prediction - target.load_mw)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["sql", "mongo"], required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--when", help="Historical or custom forecast hour, including timezone"
    )
    parser.add_argument(
        "--temp-mean", type=float, help="Expected five-city mean temperature in Celsius"
    )
    parser.add_argument(
        "--model", default=MODEL_PATH, help="Path to the existing trained model bundle"
    )
    args = parser.parse_args(argv)
    try:
        with httpx.Client(base_url=args.base_url, timeout=30) as client:
            result = forecast_from_api(
                client,
                args.backend,
                when=args.when,
                temp_mean=args.temp_mean,
                model_path=args.model,
            )
    except (ValueError, OSError) as error:
        print(f"Forecast failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
