"""
scripts/migrate_to_postgres.py — F2.5 data migration.

Reads all CSVs and JSON files from data/, inserts them into the
F2.1 Postgres schema, and writes a verification report. Idempotent:
re-running the script produces the same final state. Designed to
be run once during the Replit -> Railway cutover, before the live
app starts writing to the same tables.

Source data (data/):
  - stations.csv           -> stations (10 rows; legacy_id 1-10)
  - station_prices.json    -> prices (10 rows)
  - customers.csv          -> customers (9 rows; dedup by account_code)
  - ops_audit_log.csv      -> audit_log (48 rows; FK to vouchers may
                              not resolve; missing IDs become NULL)
  - discount_store.json    -> discounts (empty source: {} -> no rows)
  - discount_history.csv   -> discount_history (empty source)
  - presets/*.csv          -> presets (empty source: 0 files)
  - requested_vouchers.csv -> IGNORED (per F2.5 plan: stays as CSV)

Stations from price_store._DEFAULT_STATIONS (the 10 canonical slug-
ID rows: cleanfuel_valenzuela, ecooil_qc, etc.) are also inserted
here as a re-runnable safety net. The F2.1 T3 seed already does
this, so the delta is normally 0.

Usage:
  python scripts/migrate_to_postgres.py \\
      --dsn postgresql://unifleet:pw@db:5432/unifleet \\
      --data-dir data \\
      --report-out /tmp/migration_report.json

  # Or with env var:
  DATABASE_URL=postgresql://... python scripts/migrate_to_postgres.py

Idempotency:
  - stations, prices, customers, discounts: UPSERT (idempotent on PK)
  - audit_log, discount_history: TRUNCATE + INSERT (the source CSV
    is the only source of historical data; re-running produces the
    same result)
  - price_history: empty source; no-op
  - presets: empty source; no-op
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow `from price_store import _DEFAULT_STATIONS` when this script
# is run as `python scripts/migrate_to_postgres.py` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg
from psycopg.rows import dict_row


# ============================================================
# Helpers
# ============================================================

def _slugify(name: str) -> str:
    """Slug a human station name to an id: lowercase, alphanum + underscore.
    Used for the 9 CSV-only stations that don't have a slug yet."""
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "unknown"


def _parse_naive_iso_as_utc(s: str) -> Optional[datetime]:
    """Parse a naive ISO 8601 timestamp and assume UTC.

    The legacy ops_audit_log.csv has timestamps like '2025-08-27T07:01:16'
    (no timezone). Replit ran in UTC, so we treat them as UTC.
    """
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    # datetime.fromisoformat handles 'YYYY-MM-DDTHH:MM:SS' in 3.11+
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Try a couple of common variants
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ============================================================
# Source readers
# ============================================================

def read_stations_csv(path: Path) -> List[Dict[str, Any]]:
    """Read data/stations.csv. Columns: station_id (int), station_name (str).
    Returns: [{legacy_id, display_name, brand, location}]"""
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                legacy_id = int(r["station_id"])
            except (KeyError, ValueError, TypeError):
                continue
            display_name = (r.get("station_name") or "").strip()
            if not display_name:
                continue
            rows.append({
                "legacy_id": str(legacy_id),  # schema is VARCHAR(64)
                "display_name": display_name,
                "brand": "EcoOil",  # all 10 CSV rows are EcoOil per stations.csv
                "location": None,
            })
    return rows


def read_station_prices_json(path: Path) -> List[Dict[str, Any]]:
    """Read data/station_prices.json. Returns: [{id, brand, name, location,
    price_php_per_liter, updated_at}]"""
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("stations", [])


def read_price_store_defaults() -> List[Dict[str, Any]]:
    """Read price_store._DEFAULT_STATIONS (the canonical 10 slug-ID rows
    with em-dash names). This is the source of truth for the 10 'new'
    stations that the F2.1 T3 seed also inserts."""
    try:
        from price_store import _DEFAULT_STATIONS
    except ImportError:
        return []
    return list(_DEFAULT_STATIONS)


def read_customers_csv(path: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Read data/customers.csv. Dedups by account_code (keeps LAST
    occurrence per the F2.5 plan). Returns (kept_rows, dropped_rows)."""
    if not path.exists():
        return [], []
    by_code: Dict[str, Tuple[int, Dict[str, Any]]] = {}  # code -> (row_index, row)
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, r in enumerate(reader, start=2):  # 2 = first data row (after header)
            code = (r.get("account_code") or "").strip()
            if not code:
                continue
            by_code[code] = (i, r)
    kept = [r for _, r in by_code.values()]
    # dropped = rows that lost the dedup race
    seen = set()
    dropped: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, r in enumerate(reader, start=2):
            code = (r.get("account_code") or "").strip()
            if not code:
                continue
            if code in seen:
                dropped.append({**r, "_source_row": i})
            else:
                seen.add(code)
    return kept, dropped


def read_audit_log_csv(path: Path) -> List[Dict[str, Any]]:
    """Read data/ops_audit_log.csv. Returns: [{timestamp (datetime UTC),
    action, voucher_id, from_status, to_status, route, actor_ip,
    user_agent, note}]"""
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            ts = _parse_naive_iso_as_utc(r.get("timestamp", ""))
            rows.append({
                "timestamp": ts,
                "action": (r.get("action") or "").strip(),
                "voucher_id": (r.get("voucher_id") or "").strip() or None,
                "from_status": (r.get("from_status") or "").strip() or None,
                "to_status": (r.get("to_status") or "").strip() or None,
                "route": (r.get("route") or "").strip()[:200] or None,
                "actor_ip": (r.get("actor_ip") or "").strip()[:50] or None,
                "user_agent": (r.get("user_agent") or "").strip() or None,
                "note": (r.get("note") or "").strip() or None,
            })
    return rows


# ============================================================
# Migration steps
# ============================================================

def count_table(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def migrate_stations(conn, csv_rows: List[Dict], price_json_rows: List[Dict],
                     price_store_defaults: List[Dict]) -> Dict[str, Any]:
    """Migrate stations + prices.

    Stations come from TWO sources that overlap:
      - data/stations.csv: 10 EcoOil rows (legacy_id 1-10, regular hyphen)
      - price_store._DEFAULT_STATIONS: 10 canonical slug rows (em-dash)
      - data/station_prices.json: same 10 canonical rows (alias)

    The CSV-only rows get new slug IDs (ecooil_edsa_mandaluyong etc.) and
    a legacy_id (1,3,4,5,6,7,8,9,10). The ecooil_qc row appears in BOTH
    sources; we use the canonical price_store slug 'ecooil_qc' and assign
    it legacy_id=2 from the CSV.

    The 9 NEW rows (from the CSV, not in price_store) are: legacy_id
    1,3,4,5,6,7,8,9,10. They get slugs:
      ecooil_edsa_mandaluyong, ecooil_pasay, ecooil_bulacan,
      ecooil_pampanga, ecooil_marikina, ecooil_rizal, ecooil_silang,
      ecooil_calamba, ecooil_cabuyao.
    """
    before = count_table(conn, "stations")
    anomalies: List[Dict[str, Any]] = []

    # Build a map: legacy_id -> csv_row (for the 10 CSV rows)
    csv_by_legacy: Dict[str, Dict] = {r["legacy_id"]: r for r in csv_rows}

    # Build a map: slug -> price_store_row (for the 10 canonical rows)
    price_defaults_by_slug: Dict[str, Dict] = {r["id"]: r for r in price_store_defaults}

    # Step 1: insert the 10 canonical price_store rows (with em-dash names).
    # These are idempotent via the PK (id).
    for r in price_store_defaults:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO stations (id, brand, display_name, location, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
                ON CONFLICT (id) DO UPDATE
                SET brand = EXCLUDED.brand,
                    display_name = EXCLUDED.display_name,
                    location = EXCLUDED.location,
                    updated_at = NOW()
            """, (r["id"], r["brand"], r["name"], r.get("location")))

    # Step 2: insert the 9 NEW CSV-only rows (slug from name, legacy_id assigned).
    # Skip legacy_id=2 (it overlaps with price_store's ecooil_qc).
    for legacy_id, csv_row in csv_by_legacy.items():
        if legacy_id == "2":
            # Already represented by price_store's ecooil_qc. Update the
            # legacy_id column on the existing row instead of inserting.
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE stations SET legacy_id = %s WHERE id = 'ecooil_qc'
                """, (legacy_id,))
            continue
        slug = f"ecooil_{_slugify(csv_row['display_name']).replace('ecooil_', '')}"
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO stations (id, legacy_id, brand, display_name, location, is_active)
                VALUES (%s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (id) DO UPDATE
                SET legacy_id = EXCLUDED.legacy_id,
                    brand = EXCLUDED.brand,
                    display_name = EXCLUDED.display_name,
                    location = EXCLUDED.location,
                    updated_at = NOW()
            """, (slug, legacy_id, csv_row["brand"], csv_row["display_name"], csv_row["location"]))

    # Step 3: insert prices from station_prices.json (idempotent via PK).
    for r in price_json_rows:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO prices (station_id, price_php_per_liter, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (station_id) DO UPDATE
                SET price_php_per_liter = EXCLUDED.price_php_per_liter,
                    updated_at = NOW()
            """, (r["id"], r["price_php_per_liter"]))

    conn.commit()
    after = count_table(conn, "stations")
    return {
        "source": "stations.csv + price_store._DEFAULT_STATIONS + station_prices.json",
        "rows_in_source": len(csv_rows) + len(price_store_defaults),
        "rows_in_db_before": before,
        "rows_in_db_after": after,
        "delta": after - before,
        "anomalies": anomalies,
    }


def migrate_customers(conn, customers: List[Dict], dropped: List[Dict]) -> Dict[str, Any]:
    """Migrate customers with dedup. Idempotent via ON CONFLICT (account_code)."""
    before = count_table(conn, "customers")
    anomalies: List[Dict[str, Any]] = []

    for r in customers:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO customers
                    (account_code, contact_name, contact_number, email, company_name,
                     fleet_size, areas, refuel_locations, hq_locations)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (account_code) DO UPDATE
                SET contact_name = EXCLUDED.contact_name,
                    contact_number = EXCLUDED.contact_number,
                    email = EXCLUDED.email,
                    company_name = EXCLUDED.company_name,
                    fleet_size = EXCLUDED.fleet_size,
                    areas = EXCLUDED.areas,
                    refuel_locations = EXCLUDED.refuel_locations,
                    hq_locations = EXCLUDED.hq_locations
            """, (
                (r.get("account_code") or "").strip() or None,
                (r.get("contact_name") or "").strip() or None,
                (r.get("contact_number") or "").strip() or None,
                (r.get("email") or "").strip() or None,
                (r.get("company_name") or "").strip() or None,
                _nullable_int(r.get("fleet_size")),
                (r.get("areas") or "").strip() or None,
                (r.get("refuel_locations") or "").strip() or None,
                (r.get("hq_locations") or "").strip() or None,
            ))

    # Report dedup races
    by_code: Dict[str, List[int]] = {}
    for d in dropped:
        by_code.setdefault(d.get("account_code", ""), []).append(d["_source_row"])
    for code, rows in by_code.items():
        anomalies.append({
            "table": "customers",
            "type": "duplicate_account_code",
            "code": code,
            "rows_dropped": rows,
            "resolution": "kept last occurrence",
        })

    conn.commit()
    after = count_table(conn, "customers")
    return {
        "source": "customers.csv",
        "rows_in_source": len(customers) + len(dropped),
        "rows_kept_after_dedup": len(customers),
        "rows_dropped": len(dropped),
        "rows_in_db_before": before,
        "rows_in_db_after": after,
        "delta": after - before,
        "anomalies": anomalies,
    }


def _nullable_int(s: Any) -> Optional[int]:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def migrate_audit_log(conn, rows: List[Dict]) -> Dict[str, Any]:
    """Migrate ops_audit_log.csv -> audit_log table. TRUNCATE-then-INSERT
    for idempotency (the source CSV is the only source of historical
    data; re-running produces the same result).

    For voucher_id: if the voucher doesn't exist in the vouchers table,
    set voucher_id=NULL and log an anomaly. The FK to vouchers is
    enforced by the schema, so we look up valid voucher_ids first.
    """
    before = count_table(conn, "audit_log")
    anomalies: List[Dict[str, Any]] = []

    # Collect all non-null voucher_ids from the source
    source_voucher_ids = {r["voucher_id"] for r in rows if r.get("voucher_id")}

    # Find which ones exist in vouchers
    existing_voucher_ids: set = set()
    if source_voucher_ids:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT voucher_id FROM vouchers WHERE voucher_id = ANY(%s)",
                (list(source_voucher_ids),),
            )
            existing_voucher_ids = {row[0] for row in cur.fetchall()}

    missing = source_voucher_ids - existing_voucher_ids
    for vid in sorted(missing):
        anomalies.append({
            "table": "audit_log",
            "type": "missing_voucher_fk",
            "voucher_id": vid,
            "resolution": "set voucher_id=NULL",
        })

    # TRUNCATE before INSERT for idempotency
    with conn.cursor() as cur:
        cur.execute("TRUNCATE audit_log RESTART IDENTITY")

    for r in rows:
        vid = r.get("voucher_id")
        if vid and vid not in existing_voucher_ids:
            vid = None  # drop the FK target; the anomaly is logged
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO audit_log
                    (timestamp, action, voucher_id, from_status, to_status,
                     route, actor_ip, user_agent, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                r.get("timestamp"),
                r.get("action"),
                vid,
                r.get("from_status"),
                r.get("to_status"),
                r.get("route"),
                r.get("actor_ip"),
                r.get("user_agent"),
                r.get("note"),
            ))

    conn.commit()
    after = count_table(conn, "audit_log")
    return {
        "source": "ops_audit_log.csv",
        "rows_in_source": len(rows),
        "missing_voucher_fks": len(missing),
        "rows_in_db_before": before,
        "rows_in_db_after": after,
        "delta": after - before,
        "anomalies": anomalies,
    }


# ============================================================
# Invariant checks
# ============================================================

def check_invariants(conn) -> List[Dict[str, Any]]:
    """Run cross-table sanity checks after migration."""
    invariants: List[Dict[str, Any]] = []

    # Every row in prices has a matching row in stations
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM prices p
            LEFT JOIN stations s ON s.id = p.station_id
            WHERE s.id IS NULL
        """)
        orphan_prices = cur.fetchone()[0]
    invariants.append({
        "name": "every prices row has a matching stations row",
        "passed": orphan_prices == 0,
        "details": {"orphan_prices": orphan_prices} if orphan_prices else {},
    })

    # Every customer.account_code is unique
    with conn.cursor() as cur:
        cur.execute("""
            SELECT account_code, COUNT(*) AS n FROM customers
            GROUP BY account_code HAVING COUNT(*) > 1
        """)
        dup_codes = cur.fetchall()
    invariants.append({
        "name": "every customer.account_code is unique",
        "passed": not dup_codes,
        "details": {"duplicates": [r[0] for r in dup_codes]} if dup_codes else {},
    })

    # audit_log.action is non-empty (the schema has NOT NULL on action)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM audit_log WHERE action IS NULL OR action = ''")
        null_actions = cur.fetchone()[0]
    invariants.append({
        "name": "every audit_log row has a non-empty action",
        "passed": null_actions == 0,
        "details": {"null_action_rows": null_actions} if null_actions else {},
    })

    # audit_log.voucher_id either NULL or exists in vouchers
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM audit_log a
            WHERE a.voucher_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM vouchers v WHERE v.voucher_id = a.voucher_id)
        """)
        dangling_audit_fks = cur.fetchone()[0]
    invariants.append({
        "name": "every audit_log.voucher_id either NULL or exists in vouchers",
        "passed": dangling_audit_fks == 0,
        "details": {"dangling_audit_fks": dangling_audit_fks} if dangling_audit_fks else {},
    })

    return invariants


# ============================================================
# Main
# ============================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL"),
                   help="Postgres DSN (or $DATABASE_URL)")
    p.add_argument("--data-dir", default="data",
                   help="Directory containing the source CSVs/JSON (default: data)")
    p.add_argument("--report-out", default="data/migration_report.json",
                   help="Path to write the JSON verification report")
    p.add_argument("--skip-audit-log", action="store_true",
                   help="Skip the audit_log backfill (for re-runs after the "
                        "app has started writing live audit rows)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.dsn:
        print("ERROR: --dsn or $DATABASE_URL required", file=sys.stderr)
        return 2

    data_dir = Path(args.data_dir)
    report_path = Path(args.report_out)

    print(f"== F2.5 migration to Postgres ==")
    print(f"DSN:        {args.dsn}")
    print(f"Data dir:   {data_dir}")
    print(f"Report:     {report_path}")
    print()

    # 1. Read all sources
    print("[1/4] Reading source files...")
    csv_stations = read_stations_csv(data_dir / "stations.csv")
    price_json = read_station_prices_json(data_dir / "station_prices.json")
    price_defaults = read_price_store_defaults()
    customers, customers_dropped = read_customers_csv(data_dir / "customers.csv")
    audit_rows = read_audit_log_csv(data_dir / "ops_audit_log.csv")
    print(f"  stations.csv:            {len(csv_stations)} rows")
    print(f"  station_prices.json:     {len(price_json)} rows")
    print(f"  price_store._DEFAULT:    {len(price_defaults)} rows")
    print(f"  customers.csv (raw):     {len(customers) + len(customers_dropped)} rows")
    print(f"  customers.csv (deduped): {len(customers)} rows ({len(customers_dropped)} dropped)")
    print(f"  ops_audit_log.csv:       {len(audit_rows)} rows")
    print()

    # 2. Connect and migrate
    print("[2/4] Connecting to Postgres...")
    with psycopg.connect(args.dsn) as conn:
        stations_result = migrate_stations(conn, csv_stations, price_json, price_defaults)
        print(f"  stations/prices: before={stations_result['rows_in_db_before']} "
              f"after={stations_result['rows_in_db_after']} delta={stations_result['delta']}")

        customers_result = migrate_customers(conn, customers, customers_dropped)
        print(f"  customers:    before={customers_result['rows_in_db_before']} "
              f"after={customers_result['rows_in_db_after']} delta={customers_result['delta']}")

        if args.skip_audit_log:
            audit_log_result = {"skipped": True, "anomalies": []}
            print(f"  audit_log:    SKIPPED (--skip-audit-log)")
        else:
            audit_log_result = migrate_audit_log(conn, audit_rows)
            print(f"  audit_log:    before={audit_log_result['rows_in_db_before']} "
                  f"after={audit_log_result['rows_in_db_after']} delta={audit_log_result['delta']}")

        # 3. Invariants
        print()
        print("[3/4] Running invariant checks...")
        invariants = check_invariants(conn)
        for inv in invariants:
            mark = "OK " if inv["passed"] else "FAIL"
            print(f"  [{mark}] {inv['name']}")
            if not inv["passed"]:
                for k, v in inv["details"].items():
                    print(f"          {k}: {v}")

    # 4. Write report
    print()
    print("[4/4] Writing verification report...")
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dsn_redacted": _redact_dsn(args.dsn),
        "data_dir": str(data_dir),
        "sources": {
            "data/stations.csv":            len(csv_stations),
            "data/station_prices.json":     len(price_json),
            "price_store._DEFAULT_STATIONS": len(price_defaults),
            "data/customers.csv":           len(customers) + len(customers_dropped),
            "data/ops_audit_log.csv":       len(audit_rows),
        },
        "results": {
            "stations":   stations_result,
            "customers":  customers_result,
            "audit_log":  audit_log_result,
        },
        "invariants": invariants,
        "all_invariants_passed": all(i["passed"] for i in invariants),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  Wrote {report_path}")

    # Summary
    print()
    print("== Summary ==")
    for r in (stations_result, customers_result, audit_log_result):
        if "skipped" in r:
            print(f"  audit_log: SKIPPED")
            continue
        print(f"  {r.get('source', '?')}: "
              f"db_before={r.get('rows_in_db_before', '?')} "
              f"db_after={r.get('rows_in_db_after', '?')} "
              f"delta={r.get('delta', '?')}")
    n_anomalies = sum(len(r.get("anomalies", [])) for r in (stations_result, customers_result, audit_log_result))
    n_invariant_fail = sum(1 for i in invariants if not i["passed"])
    print(f"  anomalies:        {n_anomalies}")
    print(f"  invariant fails:  {n_invariant_fail}")
    print()
    if n_invariant_fail == 0:
        print("MIGRATION OK")
        return 0
    else:
        print("MIGRATION HAS INVARIANT FAILURES — see report")
        return 1


def _redact_dsn(dsn: str) -> str:
    """Replace the password in a DSN with *** for safe logging."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", dsn)


if __name__ == "__main__":
    sys.exit(main())
