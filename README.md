# Energy demand time-series pipeline

Forecasting Spain's hourly electricity demand (`total load actual`, in MW) from its own recent history, the clock and the temperature. The data is the Kaggle dataset [Energy consumption, generation, prices and weather](https://www.kaggle.com/datasets/nicholasjhana/energy-consumption-generation-prices-and-weather) (hourly, 2015 to 2018).

## Project structure

```
data/                 the two Kaggle files and a small sample for testing the forecast code
notebooks/
  01_preprocessing_eda.ipynb   cleaning, features and the seven analytical questions
  02_model_training.ipynb      baselines, linear regression and random forest
src/
  preprocessing.py    reusable notebook-compatible cleaning for database imports
  schemas.py          shared validation, units and UTC timestamp rules
  config.py           settings loaded from environment variables or .env
  storage/            SQLite and MongoDB persistence
  import_data.py      imports the cleaned dataset into either or both databases
  query_databases.py  executes and records three queries for each database
  api.py              FastAPI CRUD and time-series endpoints
  forecast.py         fetches API history and runs the saved model
  features.py         builds the model's 13 input features for one hour
  predict.py          loads the saved model and forecasts the next hour
sql/                  SQLite schema and three SQL query examples
mongodb/              collection validator, setup, queries and sample documents
docs/                 ERD, database design and recorded query results
tests/                focused preprocessing, database, API and forecast checks
models/               final model (best_rf_model.joblib) and feature_list.json
outputs/
  eda/                figures and tables from the EDA notebook
  model/              results, figures and tuning tables from the model notebook
  processed/          cleaned datasets created by the EDA notebook (not committed)
requirements.txt
requirements-dev.txt
.env.example
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The saved model was trained with scikit-learn 1.9.1 on Python 3.13 and has also been loaded successfully with Python 3.14.7. Keep the pinned scikit-learn version when loading it. If the two CSV files are missing from `data/`, the EDA notebook downloads them from Kaggle with `kagglehub` (it may ask for a Kaggle API token).

SQLite runs through Python's standard library. The document database uses MongoDB Atlas Free; no local MongoDB server or Docker installation is required.

## How to run

1. Run `notebooks/01_preprocessing_eda.ipynb`. It cleans the data, answers the analytical questions and writes `outputs/processed/energy_weather_features.csv`.
2. Run `notebooks/02_model_training.ipynb`. It reads that file, trains and compares the models, and saves the final model in `models/`.

Run the notebooks from the project folder or from `notebooks/`; the paths work from either. In Colab, the model notebook asks you to upload `energy_weather_features.csv`.

## Task 1A and 1B: preprocessing and exploratory analysis

The EDA notebook converts the timestamps to UTC, removes empty columns, treats zero dropouts in the generation columns as missing, fills short gaps by interpolation, and cleans the weather file (duplicates, impossible readings, Kelvin to Celsius) before merging it with the energy data into one hourly table. It adds calendar features (in Madrid local time), lags of 1, 24 and 168 hours and moving averages of 24 and 168 hours, then answers seven questions about demand: trend and seasonality, daily and weekly patterns, autocorrelation, lags versus moving averages, weather, price, and the accuracy of the operator's own forecast. Demand is strongly tied to its own past (correlation 0.95 at lag 1 and 0.70 at lag 24) and to the hour and day of the week, and it has a V-shaped relationship with temperature with its minimum around 17 °C.

## Task 1C: model training

### Method
- Target: `total load actual` (hourly electricity demand, MW)
- Chronological split: train 2015-2017, test 2018 (no shuffling)
- 7 experiments: naive baseline, 3 Linear Regression variants, Random Forest (default), Random Forest (tuned with `RandomizedSearchCV` and `TimeSeriesSplit`), and a day-ahead Random Forest
- Features deliberately left out: generation columns (they leak the target), the operator's forecast, prices and `year`

### Results
The tuned Random Forest has a test MAE of about 343 MW (1.18%), against 1,076 MW for the naive baseline and 853 MW for the best Linear Regression.

### Outputs
- `outputs/model/model_experiments.csv`: results of all experiments (MAE, RMSE, MAPE)
- `outputs/model/rf_tuning_results.csv`: every setting tried in the tuning search
- `outputs/model/day_ahead_benchmarks.csv`: simple benchmarks and the operator's day-ahead forecast
- four figures (`.png`): predicted vs actual, feature importance, experiment comparison, error by hour
- `models/best_rf_model.joblib` and `models/feature_list.json`: the final next-hour model

## Using the saved model

The model forecasts the **next hour**. It needs the load of the previous 168 hours (as a series indexed by UTC timestamps) and the mean temperature of the five cities for the hour being forecast. `src/features.py` turns these into the 13 features listed in `models/feature_list.json`, and `src/predict.py` wraps the model:

```python
from src.predict import load_model, predict_next_hour

model = load_model()
forecast = predict_next_hour(model, loads, temp_mean=18.5, when="2018-12-31 22:00")  # MW
```

`loads` must contain every hour from `when` minus 168 hours up to `when` minus 1 hour, otherwise a `ValueError` says which hours are missing. To see it work on the sample data, run this from the project folder:

```
.venv/bin/python -m src.predict
```

Note that `joblib.load` returns a dictionary (`model`, `features`, `target`), not the model itself, which is why `load_model()` is the easiest way in.

## Database and API extension

The extension reads the existing CSVs and uses the saved model directly. The completed notebooks, raw data, trained model and existing analytical outputs remain unchanged. The database import reproduces the notebooks' **modeling** cleaning policy: forward fill, then backfill only leading gaps. The EDA's interpolation policy is retained in the notebook.

The modules have separate responsibilities: preprocessing produces the same observations for both databases, shared schemas validate complete records, storage modules handle persistence, and the API delegates to the selected storage module. Forecasting reuses the existing feature and model functions.

The assignment deliverables are the [database design](docs/database_design.md), [ERD](docs/erd.svg), [SQLite scripts](sql/), [MongoDB scripts and sample documents](mongodb/), and [executed queries and results](docs/query_results.md).

### Configure MongoDB Atlas Free

1. Create an Atlas project and a [Free cluster](https://www.mongodb.com/docs/atlas/tutorial/deploy-free-tier-cluster/).
2. Create a [database user](https://www.mongodb.com/docs/atlas/security-add-mongodb-users/) for the application. Grant `readWrite` and `dbAdmin` on `energy_pipeline`: the application initializes collection validation and indexes, and updating validation requires the [`collMod` privilege](https://www.mongodb.com/docs/manual/reference/command/collmod/). Restrict the user to this project database.
3. Add your current public IP address to the project's network access list. For this development setup, the owner requested `0.0.0.0/0` (all IPv4 addresses); database authentication remains required.
4. Open **Connect → Drivers → Python** and copy the cluster connection string.
5. Copy the example settings, then replace the placeholder URI in `.env` with that connection string. Use the database user's credentials and percent-encode special characters in the username or password.

```bash
cp .env.example .env
```

The available settings are:

| Variable | Default | Purpose |
|---|---|---|
| `SQLITE_PATH` | `data/energy.db` | Local relational database file |
| `MONGODB_URI` | unset | Atlas Drivers connection string |
| `MONGODB_DATABASE` | `energy_pipeline` | MongoDB database |
| `MONGODB_COLLECTION` | `hourly_records` | Hourly document collection |

Local `.env` files and SQLite database files are excluded from Git. A connection in a database administration tool does not replace `MONGODB_URI` for the local Python application.

### Import the dataset

Run from the project folder:

```bash
.venv/bin/python -m src.import_data --backend sql
.venv/bin/python -m src.import_data --backend mongo
```

Alternatively, use `--backend both` to populate both stores from the same cleaned records. `--data-dir data` selects the folder containing `energy_dataset.csv` and `weather_features.csv`. Repeated imports upsert by timestamp, so they refresh source records without creating duplicates. Importing again also replaces manual edits to hours present in the source data.

The source contains 35,064 hourly energy observations. After duplicate removal, each hour has weather for Barcelona, Bilbao, Madrid, Seville and Valencia. Missing source demand is filled for forecasting history and recorded with `target_was_missing`; it is not silently presented as a measured evaluation target.

Run the three example queries on both databases and save fresh results:

```bash
.venv/bin/python -m src.query_databases --backend both --output outputs/database/query_results.json
```

The SQL and MongoDB scripts use the same date bounds as this command. See [recorded queries and results](docs/query_results.md) for the statements and output.

### Start the API

```bash
.venv/bin/python -m uvicorn src.api:app --host 127.0.0.1 --port 8000
```

Open [interactive API documentation](http://127.0.0.1:8000/docs). Each route is available under both `/sql` and `/mongo`; the following table uses `{backend}` for either value.

| Method | Route | Behavior |
|---|---|---|
| POST | `/{backend}/records` | Create a complete hourly observation |
| GET | `/{backend}/records/{timestamp}` | Retrieve one observation |
| PUT | `/{backend}/records/{timestamp}` | Replace an observation and its five city readings |
| DELETE | `/{backend}/records/{timestamp}` | Delete an observation and its weather |
| GET | `/{backend}/records/latest` | Retrieve the latest stored observation |
| GET | `/{backend}/records?start=…&end=…` | Retrieve observations in ascending timestamp order |
| GET | `/{backend}/summary/daily?start=…&end=…` | Daily UTC demand average, minimum, maximum and count |

Timestamps must include a timezone and align to a whole UTC hour, such as `2018-12-31T22:00:00Z`. The timestamp identifies the record and cannot be changed by PUT. Ranges include `start` and exclude `end`. List responses use `limit` (default 100) and `offset` for pagination. An hourly payload includes demand, optional forecast/prices, the missing-target flag, and exactly one weather reading for each of the five cities; see the database design for field names and units.

Retrieve the latest observation and a four-hour range:

```bash
curl 'http://127.0.0.1:8000/sql/records/latest'
curl 'http://127.0.0.1:8000/mongo/records?start=2018-12-31T18:00:00Z&end=2018-12-31T22:00:00Z&limit=100'
```

This Python example exercises POST, GET, PUT and DELETE for each backend using a temporary example hour immediately after its latest stored observation. Run it while the API is running and both stores are imported:

```bash
.venv/bin/python - <<'PY'
from datetime import datetime, timedelta
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000") as client:
    for backend in ("sql", "mongo"):
        route = f"/{backend}/records"
        response = client.get(f"{route}/latest")
        response.raise_for_status()
        record = response.json()
        hour = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
        record["timestamp"] = (hour + timedelta(hours=1)).isoformat()
        key = record["timestamp"].replace("+00:00", "Z")
        client.post(route, json=record).raise_for_status()
        try:
            client.get(f"{route}/{key}").raise_for_status()
            record["load_mw"] += 100
            client.put(f"{route}/{key}", json=record).raise_for_status()
        finally:
            client.delete(f"{route}/{key}").raise_for_status()
        print(f"{backend}: POST, GET, PUT, DELETE completed")
PY
```

### Forecast through the API

For a historical demonstration, fetch an existing hour's temperature and the preceding 168 hours of demand:

```bash
.venv/bin/python -m src.forecast --backend sql --when 2018-12-31T22:00:00Z
.venv/bin/python -m src.forecast --backend mongo --when 2018-12-31T22:00:00Z
```

The target hour's actual demand is excluded from model features and is used only for comparison when it was measured. The script normalizes UTC history, builds the saved model's 13 ordered features, loads the trained model, and outputs demand in MW. Calendar features use Madrid local time, including daylight-saving changes. A missing hour in the required history is an error.

To forecast one hour after the latest stored demand, provide the expected mean temperature in Celsius for the forecast hour:

```bash
.venv/bin/python -m src.forecast --backend mongo --temp-mean 8.5
```

This predicts the hour after the dataset's latest timestamp; the supplied data ends in 2018. A forecast for the present day needs recent demand observations and a weather forecast for that hour. `--temp-mean` is an explicit input, not an automatic weather-service lookup. Use `--base-url http://127.0.0.1:8000` if specifying the API address.

### Focused verification

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/test_preprocessing.py tests/test_sqlite.py tests/test_api.py tests/test_forecast.py
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy src --ignore-missing-imports --check-untyped-defs
```

MongoDB integration tests run against a real server and use disposable collections in a separate test database. Set `TEST_MONGODB_URI` and optionally `TEST_MONGODB_DATABASE` in your local environment before running:

```bash
.venv/bin/python -m pytest tests/test_mongo.py tests/test_api_mongo.py
```

Without `TEST_MONGODB_URI`, those tests are skipped. Grant the test user read/write and schema-management access to the selected test database. Keep test credentials out of source control.
