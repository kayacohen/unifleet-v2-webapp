# persistence.py
import os, sqlite3, pandas as pd
from typing import List, Dict, Optional
from models import VOUCHER_COLUMNS, SQLITE_PATH, SCHEMA_SQL

MASTER_CSV = "data/master_vouchers.csv"

def _ensure_dirs():
    os.makedirs("data", exist_ok=True)

def get_repo(backend: str):
    backend = (backend or "csv").lower()
    if backend == "db":
        return DBRepo()
    return CSVRepo()

class CSVRepo:
    def __init__(self):
        _ensure_dirs()
    def _ensure_cols(self, df: pd.DataFrame) -> pd.DataFrame:
        for c in VOUCHER_COLUMNS:
            if c not in df.columns:
                df[c] = ""
        return df
    def _read(self) -> pd.DataFrame:
        if not os.path.exists(MASTER_CSV):
            return pd.DataFrame(columns=VOUCHER_COLUMNS)
        df = pd.read_csv(MASTER_CSV, encoding='utf-8-sig')
        return self._ensure_cols(df)
    def _write(self, df: pd.DataFrame):
        self._ensure_cols(df).to_csv(MASTER_CSV, index=False, encoding='utf-8-sig')

    # API
    def list_recent_vouchers(self, limit: int = 50) -> List[Dict]:
        df = self._read()
        if 'transaction_date' in df.columns:
            df = df.sort_values(by='transaction_date', ascending=False, na_position='last')
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
        self._write(df)
    def append_vouchers(self, rows: List[Dict]):
        df = self._read()
        add_df = pd.DataFrame(rows)
        df = pd.concat([df, add_df], ignore_index=True)
        self._write(df)

class DBRepo:
    def __init__(self):
        _ensure_dirs()
        self.conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.executescript(SCHEMA_SQL)
    def _row_to_dict(self, row: sqlite3.Row) -> Dict:
        return {k: row[k] for k in row.keys()}

    # API
    def list_recent_vouchers(self, limit: int = 50) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM vouchers ORDER BY transaction_date DESC, rowid DESC LIMIT ?",
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
