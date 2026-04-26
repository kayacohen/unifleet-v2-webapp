# persistence.py
import os, sqlite3, pandas as pd
from typing import List, Dict, Optional
from models import VOUCHER_COLUMNS, SQLITE_PATH, SCHEMA_SQL
from datetime import datetime
import random
import string

MASTER_CSV = "data/master_vouchers.csv"

# Extra columns used by newer UniFleet booking flows.
# Kept here so CSV persistence does not drop fields that are not yet in models.py.
EXTRA_VOUCHER_COLUMNS = [
    "fuel_type"
]

ALL_VOUCHER_COLUMNS = list(dict.fromkeys(VOUCHER_COLUMNS + EXTRA_VOUCHER_COLUMNS))


def _ensure_dirs():
    os.makedirs("data", exist_ok=True)


def get_repo(backend: str):
    backend = (backend or "csv").lower()
    if backend == "db":
        return DBRepo()
    return CSVRepo()


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _gen_voucher_id() -> str:
    salt = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f"UF-{datetime.utcnow().strftime('%Y%m%d')}-{salt}"


def _status_priority(status: str) -> int:
    """
    Dashboard sorting priority:
    0 = Unverified first
    1 = Unredeemed second
    2 = Redeemed third
    9 = anything else
    """
    s = str(status or "").strip().lower()
    if s == "unverified":
        return 0
    if s == "unredeemed":
        return 1
    if s == "redeemed":
        return 2
    return 9


class CSVRepo:
    def __init__(self):
        _ensure_dirs()

    def _ensure_cols(self, df: pd.DataFrame) -> pd.DataFrame:
        for c in ALL_VOUCHER_COLUMNS:
            if c not in df.columns:
                df[c] = ""

        known = [c for c in ALL_VOUCHER_COLUMNS if c in df.columns]
        others = [c for c in df.columns if c not in known]
        return df[known + others]

    def _read(self) -> pd.DataFrame:
        if not os.path.exists(MASTER_CSV):
            return pd.DataFrame(columns=ALL_VOUCHER_COLUMNS)
        df = pd.read_csv(MASTER_CSV, encoding='utf-8-sig', dtype=str)
        return self._ensure_cols(df)

    def _write(self, df: pd.DataFrame):
        self._ensure_cols(df).to_csv(MASTER_CSV, index=False, encoding='utf-8-sig')

    # ===== API =====

    def list_recent_vouchers(self, limit: int = 50) -> List[Dict]:
        df = self._read()

        if df.empty:
            return []

        # Parse created_at safely
        if 'created_at' in df.columns:
            df['_created'] = pd.to_datetime(df['created_at'], errors='coerce')
        else:
            df['_created'] = pd.NaT

        # Fallback: transaction_date if created_at missing
        if 'transaction_date' in df.columns:
            df['_tx'] = pd.to_datetime(df['transaction_date'], errors='coerce')
        else:
            df['_tx'] = pd.NaT

        # Sort priority:
        # 1) created_at DESC
        # 2) transaction_date DESC
        df = df.sort_values(
            by=['_created', '_tx'],
            ascending=[False, False]
        ).drop(columns=['_created', '_tx'])

        return df.head(limit).to_dict(orient='records')

    def list_all_vouchers(self) -> List[Dict]:
        return self._read().to_dict(orient='records')

    def get_voucher(self, voucher_id: str) -> Optional[Dict]:
        df = self._read()
        rows = df[df['voucher_id'] == voucher_id]
        return None if rows.empty else rows.iloc[0].to_dict()

    def set_status(self, voucher_id: str, new_status: str, redemption_timestamp: str):
        df = self._read()
        if voucher_id not in df['voucher_id'].values:
            raise KeyError("voucher not found")
        if new_status == 'Redeemed':
            df.loc[df['voucher_id'] == voucher_id, ['status','redemption_timestamp']] = ['Redeemed', redemption_timestamp]
        else:
            df.loc[df['voucher_id'] == voucher_id, ['status','redemption_timestamp']] = [new_status, ""]
        if 'updated_at' in df.columns:
            df.loc[df['voucher_id'] == voucher_id, 'updated_at'] = _now_iso()
        self._write(df)

    def append_vouchers(self, rows: List[Dict]):
        df = self._read()
        add_df = pd.DataFrame(rows)

        for c in ALL_VOUCHER_COLUMNS:
            if c not in add_df.columns:
                add_df[c] = ""

        # Preserve any extra incoming columns too
        known = [c for c in ALL_VOUCHER_COLUMNS if c in add_df.columns]
        others = [c for c in add_df.columns if c not in known]
        add_df = add_df[known + others]

        df = pd.concat([df, add_df], ignore_index=True)
        self._write(df)

    def update_voucher_fields(self, voucher_id: str, fields: Dict):
        df = self._read()
        if df.empty or 'voucher_id' not in df.columns:
            raise KeyError("voucher not found")

        df['voucher_id'] = df['voucher_id'].astype(str)
        voucher_id = str(voucher_id)
        mask = df['voucher_id'] == voucher_id
        if not mask.any():
            raise KeyError(f"voucher not found: {voucher_id}")

        for col in fields.keys():
            if col not in df.columns:
                df[col] = ""

        for k, v in (fields or {}).items():
            df.loc[mask, k] = v

        if 'discount_total_php' in fields and 'discount_total' in df.columns:
            df.loc[mask, 'discount_total'] = fields['discount_total_php']
        if 'total_dispensed_php' in fields and 'total_dispensed' in df.columns:
            df.loc[mask, 'total_dispensed'] = fields['total_dispensed_php']

        if 'updated_at' not in df.columns:
            df['updated_at'] = ""
        df.loc[mask, 'updated_at'] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        self._write(df)

    def create_unverified_booking(self, data: Dict) -> Dict:
        """
        Create a single Unverified booking row in master_vouchers.csv.
        Returns the created row including voucher_id.
        """
        df = self._read()

        row = {c: "" for c in ALL_VOUCHER_COLUMNS}

        # Copy fields provided by caller. Unlike before, this preserves fuel_type.
        for k, v in (data or {}).items():
            if k not in row:
                row[k] = ""
            row[k] = v

        rd = (data or {}).get("refuel_datetime") or row.get("refuel_datetime") or ""
        if rd:
            if "expected_refill_date" in row and not row.get("expected_refill_date"):
                row["expected_refill_date"] = rd
            if "transaction_date" in row and not row.get("transaction_date"):
                row["transaction_date"] = rd

        # Fuel fallback for old/blank rows
        if not str(row.get("fuel_type") or "").strip():
            row["fuel_type"] = "Diesel"

        vid = (str(row.get('voucher_id') or '').strip()) or _gen_voucher_id()
        row['voucher_id'] = vid

        row['status'] = 'Unverified'
        if 'redemption_timestamp' in row:
            row['redemption_timestamp'] = ""

        now = _now_iso()
        if 'created_at' in row:
            row['created_at'] = now
        if 'updated_at' in row:
            row['updated_at'] = now

        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        self._write(df)

        return row


class DBRepo:
    def __init__(self):
        _ensure_dirs()
        self.conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.executescript(SCHEMA_SQL)

    def _row_to_dict(self, row: sqlite3.Row) -> Dict:
        return {k: row[k] for k in row.keys()}

    # ===== API =====

    def list_recent_vouchers(self, limit: int = 50) -> List[Dict]:
        rows = self.conn.execute(
            """
            SELECT * FROM vouchers
            ORDER BY
              CASE status
                WHEN 'Unverified' THEN 0
                WHEN 'Unredeemed' THEN 1
                WHEN 'Redeemed' THEN 2
                ELSE 9
              END ASC,
              CASE WHEN created_at IS NOT NULL AND created_at <> '' THEN 0 ELSE 1 END,
              datetime(created_at) DESC,
              CASE WHEN transaction_date IS NOT NULL AND transaction_date <> '' THEN 0 ELSE 1 END,
              transaction_date DESC,
              rowid DESC
            LIMIT ?
            """,
            (int(limit),)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_all_vouchers(self) -> List[Dict]:
        rows = self.conn.execute("SELECT * FROM vouchers").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_voucher(self, voucher_id: str) -> Optional[Dict]:
        row = self.conn.execute("SELECT * FROM vouchers WHERE voucher_id = ?", (voucher_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def set_status(self, voucher_id: str, new_status: str, redemption_timestamp: str):
        if new_status == 'Redeemed':
            self.conn.execute(
                "UPDATE vouchers SET status = ?, redemption_timestamp = ? WHERE voucher_id = ?",
                ('Redeemed', redemption_timestamp, voucher_id)
            )
        else:
            self.conn.execute(
                "UPDATE vouchers SET status = ?, redemption_timestamp = '' WHERE voucher_id = ?",
                (new_status, voucher_id)
            )
        self.conn.commit()

    def append_vouchers(self, rows: List[Dict]):
        cols = VOUCHER_COLUMNS
        placeholders = ",".join(["?"] * len(cols))
        sql = f"INSERT OR REPLACE INTO vouchers ({','.join(cols)}) VALUES ({placeholders})"
        vals = [tuple(r.get(c, None) for c in cols) for r in rows]
        with self.conn:
            self.conn.executemany(sql, vals)