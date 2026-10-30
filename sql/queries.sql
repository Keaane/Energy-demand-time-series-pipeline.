-- Run after importing data: sqlite3 data/energy.db < sql/queries.sql
-- UTC timestamps; ranges include start and exclude end.

-- 1. Latest record, with mean temperature across the five cities.
SELECT * FROM hourly_summary ORDER BY timestamp DESC LIMIT 1;

-- 2. Four hourly records in a date range.
SELECT * FROM hourly_summary
WHERE timestamp >= '2018-12-31T18:00:00Z'
  AND timestamp < '2018-12-31T22:00:00Z'
ORDER BY timestamp;

-- 3. Daily demand aggregates, grouped by UTC calendar date.
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
