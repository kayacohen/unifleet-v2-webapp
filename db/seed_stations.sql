-- db/seed_stations.sql — seed the `stations` table with the 19 known stations.
--
-- 10 rows from price_store._DEFAULT_STATIONS (the new canonical slug IDs
-- from the Phase 2 backend). 9 NEW rows from data/stations.csv (legacy
-- EcoOil branches that aren't in price_store). The one overlap (EcoOil - QC)
-- shares a row: the price_store slug `ecooil_qc` is the PK and
-- `legacy_id = 2` (the int ID from the CSV).
--
-- Apply with: python db/apply.py db/schema.sql db/seed_stations.sql --dsn <DSN>
-- (or apply after the schema in the same command — see T3 multi-file flow).
--
-- Idempotency: every INSERT uses ON CONFLICT (id) DO UPDATE so re-running
-- the seed is a no-op (counts and values stay the same).

INSERT INTO stations (id, legacy_id, brand, display_name, location) VALUES
  ('cleanfuel_valenzuela', NULL, 'Cleanfuel',   'Cleanfuel – Valenzuela',    'NLEX Southbound'),
  ('unioil_mandaluyong',   NULL, 'Unioil',       'Unioil – Mandaluyong',      'EDSA'),
  ('seaoil_bicutan',       NULL, 'Seaoil',       'Seaoil – Bicutan',          'SLEX Northbound'),
  ('ecooil_qc',            '2',  'EcoOil',       'EcoOil – QC',               'Commonwealth'),
  ('maximumfuel_val',      NULL, 'Maximum Fuel', 'Maximum Fuel – Valenzuela', 'Punturin'),
  ('phoenix_meyc',         NULL, 'Phoenix',      'Phoenix – Meycauayan',      'NLEX'),
  ('petro_gsanj',          NULL, 'Petro G',      'Petro G – San Jose',        'Bulacan'),
  ('gazz_binan',           NULL, 'Gazz',         'Gazz – Biñan',              'SLEX Southbound'),
  ('filoil_stamesa',       NULL, 'FilOil',       'FilOil – Sta. Mesa',        'Manila'),
  ('petron_port',          NULL, 'Petron',       'Petron – Port Area',        'Port of Manila')
ON CONFLICT (id) DO UPDATE SET
  legacy_id    = EXCLUDED.legacy_id,
  brand        = EXCLUDED.brand,
  display_name = EXCLUDED.display_name,
  location     = EXCLUDED.location,
  updated_at   = NOW();

INSERT INTO stations (id, legacy_id, brand, display_name, location) VALUES
  ('ecooil_edsa_mandaluyong', '1',  'EcoOil', 'EcoOil - EDSA Mandaluyong', NULL),
  ('ecooil_pasay',            '3',  'EcoOil', 'EcoOil - Pasay',            NULL),
  ('ecooil_bulacan',          '4',  'EcoOil', 'EcoOil - Bulacan',          NULL),
  ('ecooil_pampanga',         '5',  'EcoOil', 'EcoOil - Pampanga',         NULL),
  ('ecooil_marikina',         '6',  'EcoOil', 'EcoOil - Marikina',         NULL),
  ('ecooil_rizal',            '7',  'EcoOil', 'EcoOil - Rizal',            NULL),
  ('ecooil_silang',           '8',  'EcoOil', 'EcoOil - Silang',           NULL),
  ('ecooil_calamba',          '9',  'EcoOil', 'EcoOil - Calamba',          NULL),
  ('ecooil_cabuyao',          '10', 'EcoOil', 'EcoOil - Cabuyao',          NULL)
ON CONFLICT (id) DO UPDATE SET
  legacy_id    = EXCLUDED.legacy_id,
  brand        = EXCLUDED.brand,
  display_name = EXCLUDED.display_name,
  location     = EXCLUDED.location,
  updated_at   = NOW();
