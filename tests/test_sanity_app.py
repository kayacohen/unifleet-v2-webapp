"""Tests for sanity_app.py — the F1.1 platform-sanity Flask app.

These tests verify:
- the HTTP contract (GET and HEAD / return 200)
- the module shape (exposes a Flask app for gunicorn)
- the import isolation (does not pull in main.py or heavy native deps)
"""
import ast
from pathlib import Path

from flask import Flask

import sanity_app


# --- HTTP contract ---

def test_root_returns_200_on_get():
    client = sanity_app.app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert response.data, "Response body must be non-empty"


def test_root_returns_200_on_head():
    client = sanity_app.app.test_client()
    response = client.head("/")
    assert response.status_code == 200


# --- Module shape ---

def test_app_symbol_is_a_flask_app():
    assert isinstance(sanity_app.app, Flask), (
        "sanity_app must expose a Flask instance named `app` for gunicorn"
    )


# --- Import isolation ---

_FORBIDDEN_HEAVY_DEPS = frozenset({"PIL", "reportlab", "pandas", "qrcode", "psycopg"})


def _top_level_imports(source):
    """Return the set of top-level module names imported by `source`."""
    tree = ast.parse(source)
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module is not None:
                names.add(node.module.split(".")[0])
    return names


def _sanity_app_source():
    return Path(sanity_app.__file__).read_text(encoding="utf-8")


def test_does_not_import_main():
    imports = _top_level_imports(_sanity_app_source())
    assert "main" not in imports, (
        f"sanity_app must not import `main` (got: {sorted(imports)})"
    )


def test_does_not_import_heavy_native_deps():
    imports = _top_level_imports(_sanity_app_source())
    forbidden = imports & _FORBIDDEN_HEAVY_DEPS
    assert not forbidden, (
        f"sanity_app must not import heavy native deps (found: {sorted(forbidden)})"
    )
