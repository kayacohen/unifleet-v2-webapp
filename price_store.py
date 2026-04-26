# price_store.py
import os, json, time, tempfile, shutil
from typing import List, Dict, Any, Optional

DATA_DIR = "data"
PRICE_PATH = os.path.join(DATA_DIR, "station_prices.json")

# Seed with YOUR provided stations exactly
_DEFAULT_STATIONS = [
    {
      "id": "cleanfuel_valenzuela",
      "brand": "Cleanfuel",
      "name": "Cleanfuel – Valenzuela",
      "location": "NLEX Southbound",
      "price_php_per_liter": 60.0,
      "updated_at": 1756654640,
      "fuel_prices": {
        "diesel": {
          "label": "Diesel",
          "price_php_per_liter": 60.0,
          "updated_at": 1756654640
        },
        "gasoline": {
          "label": "Gasoline",
          "price_php_per_liter": 0.0,
          "updated_at": 0
        }
      }
    },
    {
      "id": "unioil_mandaluyong",
      "brand": "Unioil",
      "name": "Unioil – Mandaluyong",
      "location": "EDSA",
      "price_php_per_liter": 59.1,
      "updated_at": 0,
      "fuel_prices": {
        "diesel": {
          "label": "Diesel",
          "price_php_per_liter": 59.1,
          "updated_at": 0
        },
        "gasoline": {
          "label": "Gasoline",
          "price_php_per_liter": 0.0,
          "updated_at": 0
        }
      }
    },
    {
      "id": "seaoil_bicutan",
      "brand": "Seaoil",
      "name": "Seaoil – Bicutan",
      "location": "SLEX Northbound",
      "price_php_per_liter": 58.9,
      "updated_at": 0,
      "fuel_prices": {
        "diesel": {
          "label": "Diesel",
          "price_php_per_liter": 58.9,
          "updated_at": 0
        },
        "gasoline": {
          "label": "Gasoline",
          "price_php_per_liter": 0.0,
          "updated_at": 0
        }
      }
    },
    {
      "id": "ecooil_qc",
      "brand": "EcoOil",
      "name": "EcoOil – QC",
      "location": "Commonwealth",
      "price_php_per_liter": 58.3,
      "updated_at": 0,
      "fuel_prices": {
        "diesel": {
          "label": "Diesel",
          "price_php_per_liter": 58.3,
          "updated_at": 0
        },
        "gasoline": {
          "label": "Gasoline",
          "price_php_per_liter": 0.0,
          "updated_at": 0
        }
      }
    },
    {
      "id": "maximumfuel_val",
      "brand": "Maximum Fuel",
      "name": "Maximum Fuel – Valenzuela",
      "location": "Punturin",
      "price_php_per_liter": 57.95,
      "updated_at": 0,
      "fuel_prices": {
        "diesel": {
          "label": "Diesel",
          "price_php_per_liter": 57.95,
          "updated_at": 0
        },
        "gasoline": {
          "label": "Gasoline",
          "price_php_per_liter": 0.0,
          "updated_at": 0
        }
      }
    },
    {
      "id": "phoenix_meyc",
      "brand": "Phoenix",
      "name": "Phoenix – Meycauayan",
      "location": "NLEX",
      "price_php_per_liter": 58.2,
      "updated_at": 0,
      "fuel_prices": {
        "diesel": {
          "label": "Diesel",
          "price_php_per_liter": 58.2,
          "updated_at": 0
        },
        "gasoline": {
          "label": "Gasoline",
          "price_php_per_liter": 0.0,
          "updated_at": 0
        }
      }
    },
    {
      "id": "petro_gsanj",
      "brand": "Petro G",
      "name": "Petro G – San Jose",
      "location": "Bulacan",
      "price_php_per_liter": 58.0,
      "updated_at": 0,
      "fuel_prices": {
        "diesel": {
          "label": "Diesel",
          "price_php_per_liter": 58.0,
          "updated_at": 0
        },
        "gasoline": {
          "label": "Gasoline",
          "price_php_per_liter": 0.0,
          "updated_at": 0
        }
      }
    },
    {
      "id": "gazz_binan",
      "brand": "Gazz",
      "name": "Gazz – Biñan",
      "location": "SLEX Southbound",
      "price_php_per_liter": 57.8,
      "updated_at": 0,
      "fuel_prices": {
        "diesel": {
          "label": "Diesel",
          "price_php_per_liter": 57.8,
          "updated_at": 0
        },
        "gasoline": {
          "label": "Gasoline",
          "price_php_per_liter": 0.0,
          "updated_at": 0
        }
      }
    },
    {
      "id": "filoil_stamesa",
      "brand": "FilOil",
      "name": "FilOil – Sta. Mesa",
      "location": "Manila",
      "price_php_per_liter": 59.4,
      "updated_at": 0,
      "fuel_prices": {
        "diesel": {
          "label": "Diesel",
          "price_php_per_liter": 59.4,
          "updated_at": 0
        },
        "gasoline": {
          "label": "Gasoline",
          "price_php_per_liter": 0.0,
          "updated_at": 0
        }
      }
    },
    {
      "id": "petron_port",
      "brand": "Petron",
      "name": "Petron – Port Area",
      "location": "Port of Manila",
      "price_php_per_liter": 59.9,
      "updated_at": 0,
      "fuel_prices": {
        "diesel": {
          "label": "Diesel",
          "price_php_per_liter": 59.9,
          "updated_at": 0
        },
        "gasoline": {
          "label": "Gasoline",
          "price_php_per_liter": 0.0,
          "updated_at": 0
        }
      }
    }
]

def _atomic_write(path: str, data: str) -> None:
    """Write a file atomically to avoid corruption."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".prices.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        shutil.move(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass

def _now_ts() -> int:
    return int(time.time())

def _normalize_fuel_type(fuel_type: str) -> str:
    ft = (fuel_type or "diesel").strip().lower()
    if ft not in ("diesel", "gasoline"):
        ft = "diesel"
    return ft

def _ensure_fuel_prices(station: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure a station has fuel_prices while preserving legacy diesel fields.
    Diesel mirrors existing price_php_per_liter / updated_at as fallback.
    """
    station.setdefault("updated_at", 0)

    legacy_price = station.get("price_php_per_liter", 0)
    legacy_updated_at = station.get("updated_at", 0)

    fuel_prices = station.get("fuel_prices")
    if not isinstance(fuel_prices, dict):
        fuel_prices = {}

    diesel = fuel_prices.get("diesel")
    if not isinstance(diesel, dict):
        diesel = {}

    diesel.setdefault("label", "Diesel")
    diesel.setdefault("price_php_per_liter", legacy_price)
    diesel.setdefault("updated_at", legacy_updated_at)

    gasoline = fuel_prices.get("gasoline")
    if not isinstance(gasoline, dict):
        gasoline = {}

    gasoline.setdefault("label", "Gasoline")
    gasoline.setdefault("price_php_per_liter", 0.0)
    gasoline.setdefault("updated_at", 0)

    fuel_prices["diesel"] = diesel
    fuel_prices["gasoline"] = gasoline
    station["fuel_prices"] = fuel_prices

    # Keep old diesel fields available for backwards compatibility
    try:
        station["price_php_per_liter"] = round(float(diesel.get("price_php_per_liter") or 0), 2)
    except Exception:
        station["price_php_per_liter"] = 0.0

    try:
        station["updated_at"] = int(diesel.get("updated_at") or 0)
    except Exception:
        station["updated_at"] = 0

    return station

def init_if_missing() -> None:
    """Create data/station_prices.json with your defaults if it doesn't exist."""
    if not os.path.exists(PRICE_PATH):
        save_all({"stations": _DEFAULT_STATIONS})

def load_all() -> Dict[str, Any]:
    """Load the whole JSON structure."""
    init_if_missing()
    with open(PRICE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_all(obj: Dict[str, Any]) -> None:
    """Save the whole JSON structure atomically."""
    _atomic_write(PRICE_PATH, json.dumps(obj, ensure_ascii=False, indent=2))

def list_stations() -> List[Dict[str, Any]]:
    """Return all stations and ensure fuel price compatibility fields exist."""
    data = load_all()
    stations = data.get("stations", [])
    for s in stations:
        _ensure_fuel_prices(s)
    return stations

def get_station(station_id: str) -> Optional[Dict[str, Any]]:
    """Return a single station dict by id, or None if not found."""
    for s in list_stations():
        if s.get("id") == station_id:
            return s
    return None

def get_fuel_price(station_id: str, fuel_type: str = "diesel") -> Optional[Dict[str, Any]]:
    """Return a station's fuel-specific price object."""
    fuel_type = _normalize_fuel_type(fuel_type)
    station = get_station(station_id)
    if not station:
        return None
    station = _ensure_fuel_prices(station)
    return station.get("fuel_prices", {}).get(fuel_type)

def set_price(station_id: str, new_price: float) -> Dict[str, Any]:
    """
    Backward-compatible diesel price update.
    Existing callers use this for Diesel.
    """
    return set_fuel_price(station_id, "diesel", new_price)

def set_fuel_price(station_id: str, fuel_type: str, new_price: float) -> Dict[str, Any]:
    """
    Update a station's fuel-specific price; sets updated_at = current epoch seconds.
    Returns the updated station dict.
    """
    fuel_type = _normalize_fuel_type(fuel_type)

    if new_price <= 0 or new_price > 200:
        raise ValueError("Unreasonable price. Must be 0 < price ≤ 200.")

    data = load_all()
    found = False
    updated_ts = _now_ts()

    for s in data.get("stations", []):
        if s.get("id") == station_id:
            _ensure_fuel_prices(s)

            s["fuel_prices"][fuel_type]["price_php_per_liter"] = round(float(new_price), 2)
            s["fuel_prices"][fuel_type]["updated_at"] = updated_ts

            # Keep legacy top-level fields synced to Diesel only
            if fuel_type == "diesel":
                s["price_php_per_liter"] = round(float(new_price), 2)
                s["updated_at"] = updated_ts

            found = True
            break

    if not found:
        raise KeyError(f"Station '{station_id}' not found")

    save_all(data)

    updated = get_station(station_id)
    assert updated is not None
    return updated

def upsert_station(st: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add or replace a station. Not used by the admin UI, but handy for scripts.
    Required keys: id, brand, name, location, price_php_per_liter
    """
    required = {"id", "brand", "name", "location", "price_php_per_liter"}
    if not required.issubset(st.keys()):
        missing = required - set(st.keys())
        raise ValueError(f"Missing keys: {missing}")

    st = dict(st)
    st.setdefault("updated_at", _now_ts())
    _ensure_fuel_prices(st)

    data = load_all()
    stations = data.get("stations", [])
    for i, s in enumerate(stations):
        if s.get("id") == st["id"]:
            stations[i] = st
            save_all(data)
            return st

    stations.append(st)
    data["stations"] = stations
    save_all(data)
    return st