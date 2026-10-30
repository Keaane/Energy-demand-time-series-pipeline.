PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS energy_readings (
    timestamp TEXT PRIMARY KEY NOT NULL,
    load_mw REAL NOT NULL CHECK (load_mw > 0),
    load_forecast_mw REAL CHECK (load_forecast_mw >= 0),
    price_actual_eur_mwh REAL,
    price_day_ahead_eur_mwh REAL,
    target_was_missing INTEGER NOT NULL DEFAULT 0
        CHECK (target_was_missing IN (0, 1)),
    CHECK (length(timestamp) = 20 AND timestamp GLOB '????-??-??T??:00:00Z')
);

CREATE TABLE IF NOT EXISTS cities (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
        CHECK (name IN ('Barcelona', 'Bilbao', 'Madrid', 'Seville', 'Valencia'))
);

INSERT OR IGNORE INTO cities (id, name) VALUES
    (1, 'Barcelona'), (2, 'Bilbao'), (3, 'Madrid'),
    (4, 'Seville'), (5, 'Valencia');

CREATE TABLE IF NOT EXISTS weather_readings (
    timestamp TEXT NOT NULL REFERENCES energy_readings(timestamp) ON DELETE CASCADE,
    city_id INTEGER NOT NULL REFERENCES cities(id),
    temperature_c REAL NOT NULL CHECK (temperature_c BETWEEN -100 AND 70),
    humidity_pct REAL NOT NULL CHECK (humidity_pct BETWEEN 0 AND 100),
    pressure_hpa REAL NOT NULL CHECK (pressure_hpa BETWEEN 900 AND 1100),
    wind_speed_ms REAL NOT NULL CHECK (wind_speed_ms BETWEEN 0 AND 50),
    cloud_cover_pct REAL NOT NULL CHECK (cloud_cover_pct BETWEEN 0 AND 100),
    rain_1h_mm REAL NOT NULL CHECK (rain_1h_mm >= 0),
    PRIMARY KEY (timestamp, city_id)
);

CREATE INDEX IF NOT EXISTS weather_city_time_idx
    ON weather_readings (city_id, timestamp);

CREATE VIEW IF NOT EXISTS hourly_summary AS
SELECT e.*, AVG(w.temperature_c) AS temp_mean_c
FROM energy_readings AS e
JOIN weather_readings AS w ON w.timestamp = e.timestamp
GROUP BY e.timestamp;
