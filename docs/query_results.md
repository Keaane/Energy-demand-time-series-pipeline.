# Executed database queries and forecast demonstration

Run date: **2026-10-30**. Query capture time: `2026-10-30T07:54:02.742221+00:00`.

> Environment: local SQLite and MongoDB Atlas Free (MongoDB 8.0). Both stores were queried directly after the complete import.

Both databases contained **35,064 hourly records**, produced from the same cleaned energy and five-city weather files. The results below were captured from executed database operations. Tables round temperatures to three decimal places and average demand to six decimal places for readability. The machine-readable [query results](../outputs/database/query_results.json) retain full precision.

To reproduce the capture after importing both databases and configuring Atlas:

```bash
.venv/bin/python -m src.query_databases --backend both --output outputs/database/query_results.json
```

All timestamps and daily groupings use UTC. Every date range includes its start and excludes its end. The source ends at `2018-12-31T22:00:00Z`, so the last UTC day contains **23 observations**; the count column makes this partial day explicit. Across the complete source, **36 demand targets were missing** and were filled using the notebook's modeling policy. These retain `target_was_missing=true` and are excluded from measured-target forecast comparisons.

## SQLite: three queries and their results

The exact executable statements are also in [sql/queries.sql](../sql/queries.sql). The `hourly_summary` view derives `temp_mean_c` from the five related city weather rows.

### 1. Latest record

```sql
SELECT * FROM hourly_summary ORDER BY timestamp DESC LIMIT 1;
```

| Field | Result |
|---|---|
| `timestamp` | `2018-12-31T22:00:00Z` |
| `load_mw` | 24455 |
| `load_forecast_mw` | 24424 |
| `price_actual_eur_mwh` | 69.88 |
| `price_day_ahead_eur_mwh` | 64.27 |
| `target_was_missing` | 0 (`false`) |
| `temp_mean_c` | 5.648 |

### 2. Records by date range

```sql
SELECT * FROM hourly_summary
WHERE timestamp >= '2018-12-31T18:00:00Z'
  AND timestamp < '2018-12-31T22:00:00Z'
ORDER BY timestamp;
```

| Timestamp (UTC) | Load (MW) | Forecast (MW) | Actual price (EUR/MWh) | Day-ahead price (EUR/MWh) | Mean temperature (°C) | Imputed target |
|---|---:|---:|---:|---:|---:|---|
| `2018-12-31T18:00:00Z` | 30653 | 30619 | 77.02 | 68.85 | 11.320 | false |
| `2018-12-31T19:00:00Z` | 29735 | 29932 | 76.16 | 68.40 | 9.144 | false |
| `2018-12-31T20:00:00Z` | 28071 | 27903 | 74.30 | 66.88 | 7.700 | false |
| `2018-12-31T21:00:00Z` | 25801 | 25450 | 69.89 | 63.93 | 6.568 | false |

Four rows were returned. The record at `22:00:00Z` is excluded by the upper boundary. SQLite stores the imputation flag as 0/1; the shared API/report converts it to a boolean.

### 3. Daily demand summaries

```sql
SELECT substr(timestamp, 1, 10) AS date,
       COUNT(*) AS count,
       AVG(load_mw) AS avg_load_mw,
       MIN(load_mw) AS min_load_mw,
       MAX(load_mw) AS max_load_mw
FROM energy_readings
WHERE timestamp >= '2018-12-29T00:00:00Z'
  AND timestamp < '2019-01-01T00:00:00Z'
GROUP BY substr(timestamp, 1, 10)
ORDER BY date;
```

| UTC date | Observations | Average demand (MW) | Minimum demand (MW) | Maximum demand (MW) |
|---|---:|---:|---:|---:|
| 2018-12-29 | 24 | 26603.958333 | 21084 | 30819 |
| 2018-12-30 | 24 | 25238.500000 | 20103 | 30229 |
| 2018-12-31 | 23 | 25966.173913 | 20006 | 30653 |

## MongoDB: three queries and their results

The following statements match [mongodb/queries.js](../mongodb/queries.js). Initialize their shared variables in `mongosh`:

```javascript
const database = db.getSiblingDB(process.env.MONGODB_DATABASE || "energy_pipeline");
const records = database.getCollection(process.env.MONGODB_COLLECTION || "hourly_records");
const start = ISODate("2018-12-31T18:00:00Z");
const end = ISODate("2018-12-31T22:00:00Z");
```

Latest and range queries return full documents, including the five embedded weather objects. The tables show their energy fields and the mean temperature calculated from those five objects by the reporting script. `_id` is excluded from the displayed documents.

### 1. Latest record

```javascript
printjson(records.find({}, { _id: 0 }).sort({ timestamp: -1 }).limit(1).toArray());
```

| Field | Result |
|---|---|
| `timestamp` | `2018-12-31T22:00:00Z` |
| `load_mw` | 24455 |
| `load_forecast_mw` | 24424 |
| `price_actual_eur_mwh` | 69.88 |
| `price_day_ahead_eur_mwh` | 64.27 |
| `target_was_missing` | false |
| Mean of `weather.temperature_c` | 5.648 °C |

### 2. Records by date range

```javascript
printjson(records.find(
  { timestamp: { $gte: start, $lt: end } },
  { _id: 0 }
).sort({ timestamp: 1 }).toArray());
```

| Timestamp (UTC) | Load (MW) | Forecast (MW) | Actual price (EUR/MWh) | Day-ahead price (EUR/MWh) | Mean temperature (°C) | Imputed target |
|---|---:|---:|---:|---:|---:|---|
| `2018-12-31T18:00:00Z` | 30653 | 30619 | 77.02 | 68.85 | 11.320 | false |
| `2018-12-31T19:00:00Z` | 29735 | 29932 | 76.16 | 68.40 | 9.144 | false |
| `2018-12-31T20:00:00Z` | 28071 | 27903 | 74.30 | 66.88 | 7.700 | false |
| `2018-12-31T21:00:00Z` | 25801 | 25450 | 69.89 | 63.93 | 6.568 | false |

Four documents were returned, in the same order and with the same values as SQLite. The `22:00:00Z` document is excluded.

### 3. Daily demand summaries

```javascript
printjson(records.aggregate([
  { $match: { timestamp: { $gte: ISODate("2018-12-29T00:00:00Z"), $lt: ISODate("2019-01-01T00:00:00Z") } } },
  { $group: {
    _id: { $dateToString: { date: "$timestamp", format: "%Y-%m-%d", timezone: "UTC" } },
    count: { $sum: 1 },
    avg_load_mw: { $avg: "$load_mw" },
    min_load_mw: { $min: "$load_mw" },
    max_load_mw: { $max: "$load_mw" }
  } },
  { $sort: { _id: 1 } },
  { $project: { _id: 0, date: "$_id", count: 1, avg_load_mw: 1, min_load_mw: 1, max_load_mw: 1 } }
]).toArray());
```

| UTC date | Observations | Average demand (MW) | Minimum demand (MW) | Maximum demand (MW) |
|---|---:|---:|---:|---:|
| 2018-12-29 | 24 | 26603.958333 | 21084 | 30819 |
| 2018-12-30 | 24 | 25238.500000 | 20103 | 30229 |
| 2018-12-31 | 23 | 25966.173913 | 20006 | 30653 |

The two databases returned identical latest-record fields, range records and daily aggregates in the captured run.

## API-driven model demonstration

The following forecasts were exercised against the running HTTP API with both database backends. The model is the existing saved next-hour Random Forest; no retraining or notebook execution was required.

Historical commands:

```bash
.venv/bin/python -m src.forecast --backend sql --when 2018-12-31T22:00:00Z
.venv/bin/python -m src.forecast --backend mongo --when 2018-12-31T22:00:00Z
```

| Result | SQLite API | MongoDB API |
|---|---:|---:|
| Forecast hour (UTC) | `2018-12-31T22:00:00Z` | `2018-12-31T22:00:00Z` |
| Prior history observations | 168 | 168 |
| Target-hour five-city mean temperature (°C) | 5.648 | 5.648 |
| Predicted demand (MW) | 23920.288233821728 | 23920.288233821728 |
| Measured actual demand (MW) | 24455 | 24455 |
| Absolute error (MW) | 534.7117661782722 | 534.7117661782722 |

Only observations in `[2018-12-24T22:00:00Z, 2018-12-31T22:00:00Z)` supplied demand history. The requested hour's actual demand was withheld from all lags and rolling averages and used only for the final error comparison. Its observed weather supplied the historical demonstration's temperature input. A real future forecast needs expected weather for the future hour.

Future-mode commands:

```bash
.venv/bin/python -m src.forecast --backend sql --temp-mean 8.5
.venv/bin/python -m src.forecast --backend mongo --temp-mean 8.5
```

Both backends forecast `2018-12-31T23:00:00Z` from 168 prior hours and the explicitly supplied **8.5 °C** mean temperature, returning **24632.264427669557 MW**. This hour is immediately after the archived dataset; it is not a present-day prediction.

The [machine-readable forecast results](../outputs/database/forecast_results.json) were captured through the running HTTP API using SQLite and Atlas Free. All 35,064 complete records were compared between the stores and matched exactly. The eight MongoDB repository and API integration tests passed against Atlas using disposable collections; those collections were removed after testing.
