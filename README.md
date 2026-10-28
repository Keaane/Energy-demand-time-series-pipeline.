# Energy-demand-time-series-pipeline.

## Task 1C: Model training

Notebook: `task1c_model/Task1C_model_training.ipynb` 

### How to reproduce
1. Run `task1_eda/Task1_Preprocessing_EDA.ipynb` to create `energy_weather_features.csv`
   (the `processed` output folder is gitignored, so the file has to be regenerated).
2. Put `energy_weather_features.csv` next to the training notebook (it is gitignored there too).
   In Colab, the notebook asks you to upload it.
3. Open `task1c_model/Task1C_model_training.ipynb` and run all cells.
   Outputs are saved to `task1c_model/task1c_outputs/`.

### Method
- Target: `total load actual` (hourly electricity demand, MW)
- Chronological split: train 2015-2017, test 2018 (no shuffling)
- 7 experiments: naive baseline, 3 Linear Regression variants, Random Forest (default),
  Random Forest (tuned with `RandomizedSearchCV` and `TimeSeriesSplit`), and a day-ahead Random Forest
- Features deliberately left out: generation columns (they leak the target), the operator's
  forecast, prices and `year`

### Outputs (`task1c_model/task1c_outputs/`)
- `model_experiments.csv`: results of all experiments (MAE, RMSE, MAPE)
- `rf_tuning_results.csv`: every setting tried in the tuning search
- `day_ahead_benchmarks.csv`: simple benchmarks and the operator's day-ahead forecast
- four figures (`.png`): predicted vs actual, feature importance, experiment comparison, error by hour
- `best_rf_model.joblib` and `feature_list.json`: the final next-hour model for the forecast script (Task 4)

### Using the saved model
Trained with scikit-learn 1.9.1 on Python 3.13. Load it with the same scikit-learn version:
`pip install scikit-learn==1.9.1`

The model predicts the **next hour** and expects the 13 features in `feature_list.json`, built from the
last 168 hourly loads, the local calendar fields and the mean temperature of the five cities.

### Results
Random Forest (tuned): test MAE about 343 MW (1.18%), against 1,076 MW for the naive baseline
and 853 MW for the best Linear Regression.