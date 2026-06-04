-- db/seed_prices.sql — seed the `prices` table with the 10 default station prices.
--
-- Mirror's price_store._DEFAULT_STATIONS[].price_php_per_liter. The
-- `updated_at` is set to NOW() at seed time; subsequent price changes
-- (via the admin UI) will update this row and append to `price_history`.
--
-- Apply with: python db/apply.py db/schema.sql db/seed_stations.sql db/seed_prices.sql --dsn <DSN>
-- (one apply invocation, files applied in order).
--
-- Idempotency: ON CONFLICT (station_id) DO UPDATE. Re-running the seed
-- will overwrite prices with the seed values, which is the intended
-- behavior for a fresh local/staging database.

INSERT INTO prices (station_id, price_php_per_liter, updated_at) VALUES
  ('cleanfuel_valenzuela', 60.00, NOW()),
  ('unioil_mandaluyong',   59.10, NOW()),
  ('seaoil_bicutan',       58.90, NOW()),
  ('ecooil_qc',            58.30, NOW()),
  ('maximumfuel_val',      57.95, NOW()),
  ('phoenix_meyc',         58.20, NOW()),
  ('petro_gsanj',          58.00, NOW()),
  ('gazz_binan',           57.80, NOW()),
  ('filoil_stamesa',       59.40, NOW()),
  ('petron_port',          59.90, NOW())
ON CONFLICT (station_id) DO UPDATE SET
  price_php_per_liter = EXCLUDED.price_php_per_liter,
  updated_at          = NOW();
