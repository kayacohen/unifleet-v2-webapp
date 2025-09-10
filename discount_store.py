import json
import csv
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Lock
from typing import Dict, Optional, Iterable, Tuple, Any

class DiscountValueError(ValueError):
    """Raised when an invalid discount value is provided."""
    pass


class DiscountStore:
    """
    Persists per-station `discount_per_liter` values to JSON,
    and appends all mutations to a CSV audit log.

    - Current state: data/discount_store.json
      Example:
      {
        "Cleanfuel - Balagtas": 2.5,
        "Unioil - Buendia": 1.75
      }

    - Audit log: data/discount_history.csv
      Columns:
      timestamp_iso, station, old_discount_per_liter, new_discount_per_liter, actor, reason

    NOTE: timestamp_iso is logged in Asia/Manila local time (ISO 8601), e.g. 2025-09-01T21:30:00+08:00
    """

    DEFAULT_JSON_PATH = "data/discount_store.json"
    DEFAULT_HISTORY_CSV_PATH = "data/discount_history.csv"
    VALUE_PRECISION_DECIMALS = 4  # keep small but precise enough

    def __init__(self,
                 json_path: str = None,
                 history_csv_path: str = None):
        self.json_path = json_path or self.DEFAULT_JSON_PATH
        self.history_csv_path = history_csv_path or self.DEFAULT_HISTORY_CSV_PATH
        self._lock = Lock()
        self._ensure_files()

    # -------------------------
    # Public API
    # -------------------------
    def get_all(self) -> Dict[str, float]:
        """Return a copy of all station → discount_per_liter mappings."""
        with self._lock:
            return dict(self._load())

    def get(self, station: str) -> Optional[float]:
        """Return discount for a station (or None if not set)."""
        key = self._normalize_station(station)
        with self._lock:
            return self._load().get(key)

    def set(self,
            station: str,
            discount_per_liter: Optional[float],
            actor: str = "system",
            reason: str = "") -> None:
        """
        Set (or clear) a station's discount. If `discount_per_liter` is None,
        the station entry is removed. Writes to JSON and logs to CSV.
        """
        key = self._normalize_station(station)
        with self._lock:
            data = self._load()
            old = data.get(key)

            if discount_per_liter is None:
                # Remove entry if exists
                if key in data:
                    del data[key]
                    self._save(data)
                    self._append_history(key, old, None, actor, reason)
                return

            new_val = self._validate_and_round(discount_per_liter)
            data[key] = new_val
            self._save(data)
            self._append_history(key, old, new_val, actor, reason)

    def set_many(self,
                 updates: Dict[str, Optional[float]],
                 actor: str = "system",
                 reason: str = "") -> None:
        """
        Bulk upsert/remove. Pass None to remove a station's discount.
        All updates are applied atomically under a single lock.
        """
        now_iso = self._now_iso()
        with self._lock:
            data = self._load()
            history_rows: Iterable[Tuple[str, str, Any, Any, str, str]] = []

            for station, value in updates.items():
                key = self._normalize_station(station)
                old = data.get(key)

                if value is None:
                    if key in data:
                        del data[key]
                        history_rows = (*history_rows, (now_iso, key, old, None, actor, reason))
                    continue

                new_val = self._validate_and_round(value)
                data[key] = new_val
                history_rows = (*history_rows, (now_iso, key, old, new_val, actor, reason))

            self._save(data)
            if history_rows:
                self._append_history_rows(history_rows)

    def clear_all(self,
                  actor: str = "system",
                  reason: str = "clear_all") -> None:
        """
        Remove all discounts. Appends one CSV row per cleared station.
        """
        with self._lock:
            data = self._load()
            if not data:
                return
            now_iso = self._now_iso()
            rows = [(now_iso, k, v, None, actor, reason) for k, v in data.items()]
            self._save({})
            self._append_history_rows(rows)

    # -------------------------
    # Internal helpers
    # -------------------------
    def _ensure_files(self) -> None:
        os.makedirs(os.path.dirname(self.json_path), exist_ok=True)
        if not os.path.exists(self.json_path):
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=2)

        os.makedirs(os.path.dirname(self.history_csv_path), exist_ok=True)
        if not os.path.exists(self.history_csv_path):
            with open(self.history_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp_iso",
                    "station",
                    "old_discount_per_liter",
                    "new_discount_per_liter",
                    "actor",
                    "reason"
                ])

    def _load(self) -> Dict[str, float]:
        with open(self.json_path, "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
            # Normalize to floats
            out: Dict[str, float] = {}
            for k, v in raw.items():
                try:
                    out[self._normalize_station(k)] = float(v)
                except (TypeError, ValueError):
                    continue
            return out

    def _save(self, data: Dict[str, float]) -> None:
        # Write atomically: write to tmp then move
        tmp_path = f"{self.json_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self.json_path)

    def _append_history(self,
                        station: str,
                        old: Optional[float],
                        new: Optional[float],
                        actor: str,
                        reason: str) -> None:
        with open(self.history_csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                self._now_iso(),
                station,
                "" if old is None else old,
                "" if new is None else new,
                actor,
                reason
            ])

    def _append_history_rows(self,
                             rows: Iterable[Tuple[str, str, Any, Any, str, str]]) -> None:
        with open(self.history_csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for row in rows:
                writer.writerow(list(row))

    def _validate_and_round(self, value: float) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise DiscountValueError("discount_per_liter must be a number (float).")
        if v < 0:
            raise DiscountValueError("discount_per_liter cannot be negative.")
        return round(v, self.VALUE_PRECISION_DECIMALS)

    @staticmethod
    def _normalize_station(station: str) -> str:
        return (station or "").strip()

    @staticmethod
    def _now_iso() -> str:
        # Manila local time (ISO 8601 with +08:00 offset), seconds precision
        return datetime.now(ZoneInfo("Asia/Manila")).isoformat(timespec="seconds")
