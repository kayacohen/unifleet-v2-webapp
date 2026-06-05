"""
tests/test_data_paths.py — unit tests for the data_paths module.

Verifies:
  - All path constants resolve to expected locations under DATA_DIR
  - ensure_dirs() creates the full subdirectory tree
  - preset_csv_path() / qr_png_path() / official_qr_png_path() helpers
    compose paths correctly
  - The default DATA_DIR is /data (Railway Volume) and is overridable
    via UNIFLEET_DATA_DIR
  - QR_ROUTE constant is the URL prefix used by main.py

These tests do NOT need a running Postgres. They only need a temp
directory (or the host's /data, which is created if missing).
"""

import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_data_dir(monkeypatch, tmp_path):
    """Point data_paths.DATA_DIR at a temp directory for isolation."""
    monkeypatch.setenv("UNIFLEET_DATA_DIR", str(tmp_path))
    # Re-import data_paths so the module-level DATA_DIR is recomputed.
    if "data_paths" in sys.modules:
        del sys.modules["data_paths"]
    mod = importlib.import_module("data_paths")
    yield tmp_path, mod
    # Reload once more with the original env so other tests see /data.
    if "data_paths" in sys.modules:
        del sys.modules["data_paths"]
    importlib.import_module("data_paths")


def test_default_data_dir_is_slash_data(monkeypatch):
    """Without UNIFLEET_DATA_DIR set, the default must be /data (Railway Volume)."""
    monkeypatch.delenv("UNIFLEET_DATA_DIR", raising=False)
    if "data_paths" in sys.modules:
        del sys.modules["data_paths"]
    mod = importlib.import_module("data_paths")
    assert str(mod.DATA_DIR) == "/data"


def test_data_dir_overrides_via_env(monkeypatch, tmp_path):
    """UNIFLEET_DATA_DIR overrides the default."""
    monkeypatch.setenv("UNIFLEET_DATA_DIR", str(tmp_path))
    if "data_paths" in sys.modules:
        del sys.modules["data_paths"]
    mod = importlib.import_module("data_paths")
    assert str(mod.DATA_DIR) == str(tmp_path)
    if "data_paths" in sys.modules:
        del sys.modules["data_paths"]
    importlib.import_module("data_paths")


def test_subdirs_resolve_under_data_dir(temp_data_dir):
    tmp, mod = temp_data_dir
    assert mod.ASSETS_DIR == tmp / "assets"
    assert mod.QR_DIR == tmp / "assets" / "qr"
    assert mod.VOUCHER_PNG_DIR == tmp / "assets" / "vouchers"
    assert mod.PDF_DIR == tmp / "assets" / "pdfs"
    assert mod.UPLOADS_DIR == tmp / "uploads"
    assert mod.EXPORTS_DIR == tmp / "exports"
    assert mod.PRESETS_DIR == tmp / "presets"
    assert mod.LEGACY_DIR == tmp / "legacy"


def test_specific_files_resolve_under_data_dir(temp_data_dir):
    tmp, mod = temp_data_dir
    assert mod.PRICE_HISTORY_CSV == tmp / "price_history.csv"
    assert mod.UPLOADED_REDEMPTIONS_CSV == tmp / "uploads" / "unifleet_web_redemptions_input.csv"
    assert mod.SUPPLIER_EXPORT_CSV == tmp / "exports" / "supplier_export.csv"
    assert mod.CUSTOMERS_CSV == tmp / "customers.csv"
    assert mod.LEGACY_STATIONS_CSV == tmp / "legacy" / "stations.csv"
    assert mod.LEGACY_CUSTOMERS_CSV == tmp / "legacy" / "customers.csv"
    assert mod.LEGACY_OPS_AUDIT_LOG_CSV == tmp / "legacy" / "ops_audit_log.csv"
    assert mod.LEGACY_UNIFLEET_DB == tmp / "legacy" / "unifleet.db"


def test_qr_helpers(temp_data_dir):
    tmp, mod = temp_data_dir
    assert mod.qr_png_path("UF-ABC") == tmp / "assets" / "qr" / "UF-ABC.png"
    assert mod.official_qr_png_path("UF-ABC") == tmp / "assets" / "qr" / "UF-ABC_Official.png"


def test_preset_csv_helper(temp_data_dir):
    tmp, mod = temp_data_dir
    assert mod.preset_csv_path("ACME01") == tmp / "presets" / "ACME01_presets.csv"


def test_qr_route_constant(temp_data_dir):
    _, mod = temp_data_dir
    assert mod.QR_ROUTE == "/assets/qr"


def test_ensure_dirs_creates_all_subdirs(temp_data_dir):
    tmp, mod = temp_data_dir
    # Pre-condition: tmp_path exists, but its subdirs do not
    assert not (tmp / "assets").exists()
    assert not (tmp / "presets").exists()
    mod.ensure_dirs()
    assert (tmp / "assets").exists()
    assert (tmp / "assets" / "qr").exists()
    assert (tmp / "assets" / "vouchers").exists()
    assert (tmp / "assets" / "pdfs").exists()
    assert (tmp / "uploads").exists()
    assert (tmp / "exports").exists()
    assert (tmp / "presets").exists()
    assert (tmp / "legacy").exists()


def test_ensure_dirs_is_idempotent(temp_data_dir):
    _, mod = temp_data_dir
    mod.ensure_dirs()
    mod.ensure_dirs()  # should not raise
    mod.ensure_dirs()


def test_static_paths_are_relative_strings(temp_data_dir):
    """STATIC_LOGO_PATH / STATIC_VOUCHER_TEMPLATE_PATH stay under static/
    (baked into the image, not on the volume)."""
    _, mod = temp_data_dir
    assert mod.STATIC_LOGO_PATH == "static/UniFleet Logo.png"
    assert mod.STATIC_VOUCHER_TEMPLATE_PATH == "static/BRANDED VOUCHER TEMPLATE - UNIFLEET.png"
