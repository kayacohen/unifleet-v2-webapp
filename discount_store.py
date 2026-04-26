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
    Persists station discount values to JSON and appends all mutations to a CSV audit log.

    Backward-compatible supported formats:

    Old format:
    {
      "EcoOil - Pasay": 2.5
    }

    New fuel-specific format:
    {
      "EcoOil - Pasay": {
        "diesel": 2.5,
        "gasoline": 1.0
      }
    }
    """

    DEFAULT_JSON_PATH = "data/discount_store.json"
    DEFAULT_HISTORY_CSV_PATH = "data/discount_history.csv"
    VALUE_PRECISION_DECIMALS = 4

    def __init__(self, json_path: str = None, history_csv_path: str = None):
        self.json_path = json_path or self.DEFAULT_JSON_PATH
        self.history_csv_path = history_csv_path or self.DEFAULT_HISTORY_CSV_PATH
        self._lock = Lock()
        self._ensure_files()

    # -------------------------
    # Public API
    # -------------------------
    def get_all(self) -> Dict[str, Any]:
        """Return a copy of all discount mappings."""
        with self._lock:
            return dict(self._load())

    def get(self, station: str, fuel_type: str = None) -> Optional[float]:
        """
        Return discount for a station.

        If fuel_type is provided and the station has fuel-specific discounts,
        return that fuel-specific value.

        If old flat format exists, return the flat station-level value.
        """
        key = self._normalize_station(station)
        fuel_key = self._normalize_fuel_type(fuel_type) if fuel_type else None

        with self._lock:
            data = self._load()
            val = data.get(key)

            if val is None:
                return None

            # Old format: station -> float
            if isinstance(val, (int, float)):
                return float(val)

            # New format: station -> {diesel: x, gasoline: y}
            if isinstance(val, dict):
                if fuel_key and fuel_key in val:
                    return float(val[fuel_key])

                # Safe fallback if no fuel_type supplied
                if "diesel" in val:
                    return float(val["diesel"])

                # Last-resort fallback: first valid numeric value
                for _, v in val.items():
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue

            return None

    def set(
        self,
        station: str,
        discount_per_liter: Optional[float],
        actor: str = "system",
        reason: str = "",
        fuel_type: str = None
    ) -> None:
        """
        Set or clear a discount.

        If fuel_type is None:
        - preserves old behavior: station -> float

        If fuel_type is provided:
        - writes new behavior: station -> {fuel_type: float}
        """
        key = self._normalize_station(station)
        fuel_key = self._normalize_fuel_type(fuel_type) if fuel_type else None

        with self._lock:
            data = self._load()
            old = data.get(key)

            if fuel_key is None:
                if discount_per_liter is None:
                    if key in data:
                        del data[key]
                        self._save(data)
                        self._append_history(key, "", old, None, actor, reason)
                    return

                new_val = self._validate_and_round(discount_per_liter)
                data[key] = new_val
                self._save(data)
                self._append_history(key, "", old, new_val, actor, reason)
                return

            # Fuel-specific set
            existing = data.get(key)

            if not isinstance(existing, dict):
                existing = {}

            old_fuel_val = existing.get(fuel_key)

            if discount_per_liter is None:
                if fuel_key in existing:
                    del existing[fuel_key]

                if existing:
                    data[key] = existing
                elif key in data:
                    del data[key]

                self._save(data)
                self._append_history(key, fuel_key, old_fuel_val, None, actor, reason)
                return

            new_val = self._validate_and_round(discount_per_liter)
            existing[fuel_key] = new_val
            data[key] = existing
            self._save(data)
            self._append_history(key, fuel_key, old_fuel_val, new_val, actor, reason)

    def set_many(
        self,
        updates: Dict[str, Optional[float]],
        actor: str = "system",
        reason: str = "",
        fuel_type: str = None
    ) -> None:
        """
        Bulk upsert/remove.

        For backward compatibility, updates is still:
        {
          "EcoOil - Pasay": 2.5
        }

        If fuel_type is provided, values are written under that fuel type.
        """
        for station, value in updates.items():
            self.set(
                station=station,
                discount_per_liter=value,
                actor=actor,
                reason=reason,
                fuel_type=fuel_type
            )

    def clear_all(self, actor: str = "system", reason: str = "clear_all") -> None:
        """Remove all discounts."""
        with self._lock:
            data = self._load()
            if not data:
                return
            now_iso = self._now_iso()
            rows = []
            for station, value in data.items():
                if isinstance(value, dict):
                    for fuel_type, fuel_value in value.items():
                        rows.append((now_iso, station, fuel_type, fuel_value, None, actor, reason))
                else:
                    rows.append((now_iso, station, "", value, None, actor, reason))

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
                    "fuel_type",
                    "old_discount_per_liter",
                    "new_discount_per_liter",
                    "actor",
                    "reason"
                ])

    def _load(self) -> Dict[str, Any]:
        with open(self.json_path, "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
            out: Dict[str, Any] = {}

            for k, v in raw.items():
                station_key = self._normalize_station(k)

                # Old flat format
                if isinstance(v, (int, float, str)):
                    try:
                        out[station_key] = float(v)
                    except (TypeError, ValueError):
                        continue

                # New fuel-specific format
                elif isinstance(v, dict):
                    fuel_map = {}
                    for fuel_type, fuel_value in v.items():
                        try:
                            fuel_map[self._normalize_fuel_type(fuel_type)] = float(fuel_value)
                        except (TypeError, ValueError):
                            continue
                    if fuel_map:
                        out[station_key] = fuel_map

            return out

    def _save(self, data: Dict[str, Any]) -> None:
        tmp_path = f"{self.json_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self.json_path)

    def _append_history(
        self,
        station: str,
        fuel_type: str,
        old: Any,
        new: Any,
        actor: str,
        reason: str
    ) -> None:
        with open(self.history_csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                self._now_iso(),
                station,
                fuel_type or "",
                "" if old is None else old,
                "" if new is None else new,
                actor,
                reason
            ])

    def _append_history_rows(
        self,
        rows: Iterable[Tuple[str, str, str, Any, Any, str, str]]
    ) -> None:
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
    def _normalize_fuel_type(fuel_type: str) -> str:
        return (fuel_type or "").strip().lower()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(ZoneInfo("Asia/Manila")).isoformat(timespec="seconds")