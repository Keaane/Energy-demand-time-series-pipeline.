# Energy demand time-series pipeline

Forecasting Spain's hourly electricity demand (`total load actual`, in MW) from its own recent history, the clock and the temperature. The data is the Kaggle dataset [Energy consumption, generation, prices and weather](https://www.kaggle.com/datasets/nicholasjhana/energy-consumption-generation-prices-and-weather) (hourly, 2015 to 2018).

## Project structure

```
data/                 the two Kaggle files and a small sample for testing the forecast code
notebooks/
  01_preprocessing_eda.ipynb   cleaning, features and the seven analytical questions
  02_model_training.ipynb      baselines, linear regression and random forest
src/
  features.py         builds the model's 13 input features for one hour
  predict.py          loads the saved model and forecasts the next hour
models/               final model (best_rf_model.joblib) and feature_list.json
outputs/
  eda/                figures and tables from the EDA notebook
  model/              results, figures and tuning tables from the model notebook
  processed/          cleaned datasets created by the EDA notebook (not committed)
requirements.txt
```

## Setup

```
pip install -r requirements.txt
```

The saved model was trained with scikit-learn 1.9.1 on Python 3.13, so keep that version when loading it. If the two CSV files are missing from `data/`, the EDA notebook downloads them from Kaggle with `kagglehub` (it may ask for a Kaggle API token).

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
python -m src.predict
```

Note that `joblib.load` returns a dictionary (`model`, `features`, `target`), not the model itself, which is why `load_model()` is the easiest way in.
