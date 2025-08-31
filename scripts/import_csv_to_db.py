# scripts/import_csv_to_db.py
import os, pandas as pd
from persistence import get_repo
from models import VOUCHER_COLUMNS

CSV_PATH = "data/master_vouchers.csv"

def main():
    if not os.path.exists(CSV_PATH):
        print("No CSV to import:", CSV_PATH); return
    df = pd.read_csv(CSV_PATH, encoding='utf-8-sig')
    for c in VOUCHER_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    rows = df[VOUCHER_COLUMNS].to_dict(orient='records')
    repo = get_repo("db")
    repo.append_vouchers(rows)
    print(f"Imported {len(rows)} rows into DB.")

if __name__ == "__main__":
    main()
