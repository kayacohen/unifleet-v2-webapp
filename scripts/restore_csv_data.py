#!/usr/bin/env python3
"""
restore_csv_data.py — Idempotent CSV/JSON → Postgres re-sync.

Run from repo root:
  python scripts/restore_csv_data.py [--dry-run]

On a fresh DB this behaves like the F2.5 migration plus converts the
4 booking requests in data/requested_vouchers.csv into Unverified
vouchers. On an already-migrated DB it only inserts missing rows,
so it's safe to run any time the CSVs change.

Sources handled:
  data/stations.csv           → stations    (UPSERT by id or legacy_id)
  data/station_prices.json    → prices      (UPSERT by station_id)
  data/customers.csv          → customers   (UPSERT by account_code, last-write-wins)
  data/ops_audit_log.csv      → audit_log   (delta-aware INSERT)
  data/requested_vouchers.csv → vouchers    (status='Unverified', deterministic id)
"""
import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import psycopg
import psycopg.rows

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
DB_DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://unifleet:unifleet_dev_pw@db:5432/unifleet",
)


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s*[-–]\s*", "_", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def step(label, fn, cur, dry):
    print(f"[{label}] ...", flush=True)
    try:
        res = fn(cur, dry)
    except Exception as e:
        cur.connection.rollback()
        res = {"ERROR": str(e).splitlines()[0], "DETAIL": (str(e).splitlines()[1] if len(str(e).splitlines()) > 1 else "")}
    print(f"  {res}", flush=True)
    return res


def restore_stations(cur, dry):
    with open(DATA / "stations.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    inserted = updated = 0
    for r in rows:
        sid = slugify(r["station_name"])
        legacy_id = r["station_id"]
        cur.execute(
            "SELECT id FROM stations WHERE id = %s OR legacy_id = %s",
            (sid, legacy_id),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                """
                UPDATE stations
                   SET display_name = %s,
                       brand = 'EcoOil',
                       legacy_id = COALESCE(stations.legacy_id, %s)
                 WHERE id = %s
                """,
                (r["station_name"], legacy_id, existing["id"]),
            )
            updated += 1
        else:
            cur.execute(
                """
                INSERT INTO stations (id, display_name, brand, location, legacy_id)
                VALUES (%s, %s, 'EcoOil', NULL, %s)
                """,
                (sid, r["station_name"], legacy_id),
            )
            inserted += 1
    return {"src": len(rows), "inserted": inserted, "updated": updated}


def restore_prices(cur, dry):
    with open(DATA / "station_prices.json") as f:
        data = json.load(f)
    entries = data.get("stations", [])
    inserted = updated = 0
    for e in entries:
        sid = e["id"]
        price = float(e.get("price_php_per_liter") or 0)
        if price <= 0:
            continue
        cur.execute("SELECT 1 FROM prices WHERE station_id = %s", (sid,))
        if cur.fetchone():
            cur.execute(
                "UPDATE prices SET price_php_per_liter = %s, updated_at = now() "
                "WHERE station_id = %s",
                (price, sid),
            )
            updated += 1
        else:
            cur.execute(
                "INSERT INTO prices (station_id, price_php_per_liter, updated_at) "
                "VALUES (%s, %s, now())",
                (sid, price),
            )
            inserted += 1
    return {"src": len(entries), "inserted": inserted, "updated": updated}


def _cust_cols(r):
    def _i(v):
        try:
            return int(v) if v else None
        except ValueError:
            return None
    return dict(
        contact_name=r.get("contact_name") or None,
        contact_number=r.get("contact_number") or None,
        email=r.get("email") or None,
        company_name=r.get("company_name") or None,
        fleet_size=_i(r.get("fleet_size")),
        areas=r.get("areas") or None,
        refuel_locations=r.get("refuel_locations") or None,
        hq_locations=r.get("hq_locations") or None,
    )


def restore_customers(cur, dry):
    with open(DATA / "customers.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    by_code = {}
    for r in rows:
        by_code[r["account_code"]] = r
    inserted = updated = 0
    for code, r in by_code.items():
        cols = _cust_cols(r)
        cur.execute("SELECT 1 FROM customers WHERE account_code = %s", (code,))
        if cur.fetchone():
            cur.execute(
                """
                UPDATE customers SET
                    contact_name = %(contact_name)s,
                    contact_number = %(contact_number)s,
                    email = %(email)s,
                    company_name = %(company_name)s,
                    fleet_size = %(fleet_size)s,
                    areas = %(areas)s,
                    refuel_locations = %(refuel_locations)s,
                    hq_locations = %(hq_locations)s
                WHERE account_code = %(account_code)s
                """,
                dict(account_code=code, **cols),
            )
            updated += 1
        else:
            cur.execute(
                """
                INSERT INTO customers (
                    account_code, contact_name, contact_number, email,
                    company_name, fleet_size, areas, refuel_locations, hq_locations
                ) VALUES (
                    %(account_code)s, %(contact_name)s, %(contact_number)s, %(email)s,
                    %(company_name)s, %(fleet_size)s, %(areas)s, %(refuel_locations)s, %(hq_locations)s
                )
                """,
                dict(account_code=code, **cols),
            )
            inserted += 1
    return {"src": len(rows), "unique": len(by_code), "inserted": inserted, "updated": updated}


def restore_audit_log(cur, dry):
    with open(DATA / "ops_audit_log.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    inserted = 0
    skipped_orphan = 0
    cur.execute("SELECT voucher_id FROM vouchers")
    known_vouchers = {row["voucher_id"] for row in cur.fetchall()}
    for r in rows:
        voucher_id = r.get("voucher_id") or None
        if voucher_id and voucher_id not in known_vouchers:
            voucher_id = None
            skipped_orphan += 1
        cur.execute(
            """
            SELECT 1 FROM audit_log
             WHERE action = %s
               AND route = %s
               AND "timestamp" = %s
               AND ((voucher_id IS NULL AND %s::text IS NULL) OR voucher_id = %s)
            LIMIT 1
            """,
            (r["action"], r["route"], r["timestamp"], voucher_id, voucher_id),
        )
        if cur.fetchone():
            continue
        cur.execute(
            """
            INSERT INTO audit_log (
                "timestamp", action, voucher_id, from_status, to_status,
                route, actor_ip, user_agent, note
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                r["timestamp"],
                r["action"],
                voucher_id,
                r.get("from_status") or None,
                r.get("to_status") or None,
                r["route"],
                r.get("actor_ip") or None,
                r.get("user_agent") or None,
                r.get("note") or None,
            ),
        )
        inserted += 1
    return {"src": len(rows), "inserted": inserted, "orphan_voucher_id": skipped_orphan}


def restore_requested_vouchers(cur, dry):
    path = DATA / "requested_vouchers.csv"
    if not path.exists():
        return {"src": 0, "inserted": 0, "skipped": 0}
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    inserted = skipped = 0
    for r in rows:
        account = r["account_code"]
        station_name = r["station"]
        sid = slugify(station_name)
        amount = float(r["requested_amount_php"])
        refuel_dt = r["refuel_datetime"]
        date_part = refuel_dt[:10].replace("-", "")
        h = hashlib.md5(
            f"{account}|{r['vehicle_plate']}|{refuel_dt}|{amount}".encode()
        ).hexdigest()[:5].upper()
        voucher_id = f"UF-{date_part}-{h}"
        cur.execute("SELECT 1 FROM vouchers WHERE voucher_id = %s", (voucher_id,))
        if cur.fetchone():
            skipped += 1
            continue
        cur.execute(
            "SELECT price_php_per_liter FROM prices WHERE station_id = %s", (sid,)
        )
        price_row = cur.fetchone()
        price = float(price_row["price_php_per_liter"]) if price_row else 0.0
        liters = round(amount / price, 4) if price > 0 else 0.0
        def _i(v):
            try:
                return int(v) if v else None
            except ValueError:
                return None
        cur.execute(
            """
            INSERT INTO vouchers (
                voucher_id, station_id, station, account_code,
                requested_amount_php, liters_requested,
                transaction_date,
                live_price_php_per_liter, status,
                driver_name, vehicle_plate, truck_make, truck_model, number_of_wheels,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s,
                %s,
                %s, 'Unverified',
                %s, %s, %s, %s, %s,
                now(), now()
            )
            """,
            (
                voucher_id,
                sid,
                station_name,
                account,
                amount,
                liters,
                refuel_dt,
                price,
                r.get("driver_name") or None,
                r.get("vehicle_plate") or None,
                r.get("truck_make") or None,
                r.get("truck_model") or None,
                _i(r.get("number_of_wheels")),
            ),
        )
        inserted += 1
    return {"src": len(rows), "inserted": inserted, "skipped": skipped}


def main():
    dry = "--dry-run" in sys.argv
    if dry:
        print("=== DRY RUN — no changes will be committed ===")
    print(f"DB: {DB_DSN}\n")

    with psycopg.connect(DB_DSN, autocommit=False) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            r1 = step("1/5 stations",          restore_stations,          cur, dry)
            r2 = step("2/5 prices",            restore_prices,            cur, dry)
            r3 = step("3/5 customers",         restore_customers,         cur, dry)
            r4 = step("4/5 audit_log",         restore_audit_log,         cur, dry)
            r5 = step("5/5 requested_vouchers", restore_requested_vouchers, cur, dry)
        if dry:
            conn.rollback()
        else:
            conn.commit()
            print("\n✓ Committed.\n")

    print("=== summary ===")
    for k, v in [(r1, "stations"), (r2, "prices"), (r3, "customers"),
                 (r4, "audit_log"), (r5, "vouchers (from requested)")]:
        print(f"  {v}: {k}")


if __name__ == "__main__":
    main()
