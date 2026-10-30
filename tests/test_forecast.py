"""Exercise API history validation and forecasting with the saved model."""

from copy import deepcopy
from pathlib import Path

import httpx
import pandas as pd
import pytest

from src.forecast import forecast_from_api, main
from src.predict import load_model, predict_next_hour
from src.schemas import CITIES, timestamp_key

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def observations():
    sample = pd.read_csv(ROOT / "data" / "sample_recent_hours.csv").tail(169)
    records = []
    for row in sample.to_dict("records"):
        records.append(
            {
                "timestamp": timestamp_key(pd.Timestamp(row["time"]).to_pydatetime()),
                "load_mw": row["total load actual"],
                "target_was_missing": False,
                "weather": [
                    {
                        "city": city,
                        "temperature_c": row["temp_mean"],
                        "humidity_pct": 50.0,
                        "pressure_hpa": 1000.0,
                        "wind_speed_ms": 2.0,
                        "cloud_cover_pct": 20.0,
                        "rain_1h_mm": 0.0,
                    }
                    for city in sorted(CITIES)
                ],
            }
        )
    return records[:-1], records[-1]


def api_client(history, target, backend="sql", *, target_status=200):
    def handle(request):
        prefix = f"/{backend}/records"
        if request.url.path == f"{prefix}/latest":
            return httpx.Response(200, json=history[-1])
        if request.url.path == prefix:
            when = pd.Timestamp(target["timestamp"])
            assert request.url.params["start"] == timestamp_key(
                (when - pd.Timedelta(hours=168)).to_pydatetime()
            )
            assert request.url.params["end"] == target["timestamp"]
            assert request.url.params["limit"] == "168"
            return httpx.Response(200, json=history)
        assert request.url.path == f"{prefix}/{target['timestamp']}"
        return httpx.Response(
            target_status,
            json=target if target_status == 200 else {"detail": "not found"},
        )

    return httpx.Client(
        base_url="http://testserver", transport=httpx.MockTransport(handle)
    )


@pytest.mark.parametrize("backend", ["sql", "mongo"])
def test_historical_forecast_matches_direct_saved_model_and_sorts_history(
    observations, backend
):
    history, target = observations
    loads = pd.Series(
        [record["load_mw"] for record in history],
        index=pd.to_datetime([record["timestamp"] for record in history], utc=True),
    )
    temperature = target["weather"][0]["temperature_c"]
    expected = predict_next_hour(load_model(), loads, temperature, target["timestamp"])
    with api_client(list(reversed(history)), target, backend) as client:
        result = forecast_from_api(client, backend, when=target["timestamp"])
    assert result["forecast_timestamp"] == target["timestamp"]
    assert result["predicted_load_mw"] == pytest.approx(expected)
    assert result["actual_load_mw"] == target["load_mw"]
    assert result["absolute_error_mw"] == pytest.approx(
        abs(expected - target["load_mw"])
    )
    assert result["history_hours"] == 168
    assert result["temp_mean_c"] == pytest.approx(temperature)
    assert result["mode"] == "historical"
    assert result["backend"] == backend


def test_target_demand_never_enters_model_features(observations):
    history, target = observations
    with api_client(history, target) as client:
        original = forecast_from_api(client, "sql", when=target["timestamp"])
    changed = deepcopy(target)
    changed["load_mw"] *= 5
    with api_client(history, changed) as client:
        perturbed = forecast_from_api(client, "sql", when=changed["timestamp"])
    assert perturbed["predicted_load_mw"] == pytest.approx(
        original["predicted_load_mw"], rel=0, abs=1e-8
    )
    assert perturbed["actual_load_mw"] != original["actual_load_mw"]


@pytest.mark.parametrize(
    "problem", ["missing", "duplicate", "out_of_window", "nonfinite"]
)
def test_forecast_rejects_invalid_history(observations, problem):
    history, target = observations
    history = deepcopy(history)
    if problem == "missing":
        history.pop(40)
    elif problem == "duplicate":
        history[40] = deepcopy(history[39])
    elif problem == "out_of_window":
        history[0] = deepcopy(target)
    else:
        history[40]["load_mw"] = "Infinity"
    with api_client(history, target) as client, pytest.raises(ValueError):
        forecast_from_api(client, "sql", when=target["timestamp"])


def test_latest_forecast_requires_next_hour_temperature(observations):
    history, target = observations
    with (
        api_client(history, target) as client,
        pytest.raises(ValueError, match="temperature|temp"),
    ):
        forecast_from_api(client, "sql")


def test_latest_forecast_uses_explicit_expected_temperature(observations):
    history, target = observations
    with api_client(history, target) as client:
        result = forecast_from_api(client, "sql", temp_mean=18.5)
    assert result["forecast_timestamp"] == target["timestamp"]
    assert result["temp_mean_c"] == 18.5
    assert result["mode"] == "future"
    assert "actual_load_mw" not in result


def test_explicit_hour_and_temperature_can_forecast_without_target_record(observations):
    history, target = observations
    with api_client(history, target, target_status=404) as client:
        result = forecast_from_api(
            client, "sql", when=target["timestamp"], temp_mean=18.5
        )
    assert result["mode"] == "future"
    assert result["temp_mean_c"] == 18.5
    assert "actual_load_mw" not in result


def test_missing_target_without_temperature_is_an_error(observations):
    history, target = observations
    with (
        api_client(history, target, target_status=404) as client,
        pytest.raises(ValueError),
    ):
        forecast_from_api(client, "sql", when=target["timestamp"])


def test_imputed_target_is_not_reported_as_measured_actual(observations):
    history, target = observations
    target["target_was_missing"] = True
    with api_client(history, target) as client:
        result = forecast_from_api(client, "sql", when=target["timestamp"])
    assert result["mode"] == "historical"
    assert "actual_load_mw" not in result
    assert "absolute_error_mw" not in result


@pytest.mark.parametrize("temperature", [float("nan"), float("inf"), -101.0, 71.0])
def test_explicit_temperature_must_be_finite_and_plausible(observations, temperature):
    history, target = observations
    with api_client(history, target) as client, pytest.raises(ValueError):
        forecast_from_api(
            client, "sql", when=target["timestamp"], temp_mean=temperature
        )


@pytest.mark.parametrize("failure", ["status", "network", "invalid_json"])
def test_api_failures_are_sanitized(observations, failure):
    _, target = observations
    secret = "private-database-password"

    def handle(request):
        if failure == "network":
            raise httpx.ConnectError(secret, request=request)
        if failure == "invalid_json":
            return httpx.Response(200, text=secret)
        return httpx.Response(503, text=secret)

    with (
        httpx.Client(
            base_url="http://testserver", transport=httpx.MockTransport(handle)
        ) as client,
        pytest.raises(ValueError) as error,
    ):
        forecast_from_api(client, "sql", when=target["timestamp"])
    assert secret not in str(error.value)


def test_cli_invalid_temperature_exits_without_network_or_model_loading(
    monkeypatch, capsys
):
    def forbid_io(*args, **kwargs):
        pytest.fail("Invalid temperature should fail before network or model loading")

    monkeypatch.setattr(httpx.Client, "get", forbid_io)
    monkeypatch.setattr("src.forecast.load_model", forbid_io)
    assert main(["--backend", "sql", "--temp-mean", "nan"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "finite" in output.err
