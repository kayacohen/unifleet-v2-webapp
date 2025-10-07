#!/usr/bin/env python3
import json, os, shutil, re
import pandas as pd

CSV_PATH = "data/stations.csv"
JSON_PATH = "data/station_prices.json"

def slugify(s: str) -> str:
    s = (s or "").lower()
    # normalize various dashes to spaces
    s = s.replace("—", " ").replace("–", " ").replace("-", " ")
    # remove characters except letters/digits/spaces
    s = re.sub(r"[^a-z0-9\s]", "", s)
    # collapse spaces to single underscore
    s = re.sub(r"\s+", "_", s).strip("_")
    # collapse multiple underscores
    s = re.sub(r"_+", "_", s)
    return s

def derive_brand_location(station_name: str):
    # Split on first '-' (regular or en/em dash variants)
    if not station_name:
        return ("", "")
    normalized = station_name.replace("—", "-").replace("–", "-")
    parts = [p.strip() for p in normalized.split("-", 1)]
    if len(parts) == 2:
        return (parts[0], parts[1])
    return (parts[0], "")

def main():
    if not os.path.isfile(CSV_PATH):
        raise SystemExit(f"Cannot find {CSV_PATH}")

    df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
    cols = {c.lower().strip(): c for c in df.columns}
    sid_col = cols.get("station_id") or cols.get("id")  # optional; not used for slug id
    sname_col = cols.get("station_name") or cols.get("name")
    if not sname_col:
        raise SystemExit("CSV must include 'station_name' (or 'name') column")

    records = []
    for _, row in df.iterrows():
        station_name = str(row[sname_col]).strip()
        if not station_name:
            continue

        # Prefer CSV brand/location if present, else derive
        brand = str(row[cols["brand"]]).strip() if "brand" in cols else ""
        location = str(row[cols["location"]]).strip() if "location" in cols else ""
        if not brand or not location:
            b, loc = derive_brand_location(station_name)
            brand = brand or b
            location = location or loc

        records.append({
            "id": slugify(station_name),  # slug id (matches price_store style)
            "brand": brand,
            "name": station_name,
            "location": location,
            "price_php_per_liter": 0.0,
            "updated_at": 0
        })

    os.makedirs(os.path.dirname(JSON_PATH), exist_ok=True)
    if os.path.exists(JSON_PATH):
        backup = JSON_PATH + ".bak"
        shutil.copyfile(JSON_PATH, backup)
        print(f"Backed up existing JSON to {backup}")

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(records)} stations to {JSON_PATH}")

if __name__ == "__main__":
    main()
