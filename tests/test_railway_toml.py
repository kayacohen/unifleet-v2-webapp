"""Tests for railway.toml — the F1.1 Railway build/start/nix config.

Railway reads this file to know how to build and start the service
and which nix packages to install. The four tests below lock the
config down to the contracts called out in PLAN-railway-provisioning.md.
"""
import tomllib
from pathlib import Path


RAILWAY_TOML = Path(__file__).resolve().parent.parent / "rw.txt"


def _load():
    with RAILWAY_TOML.open("rb") as f:
        return tomllib.load(f)


def test_railway_toml_exists_and_parses():
    assert RAILWAY_TOML.is_file(), f"railway.toml must exist at {RAILWAY_TOML}"
    data = _load()
    assert data, "railway.toml must not be empty"


def _build_command(data):
    """Railway accepts build.command or buildCommand depending on schema version."""
    build = data.get("build", {})
    return build.get("command") or build.get("buildCommand") or ""


def _start_command(data):
    """Railway accepts deploy.startCommand or startCommand depending on schema version."""
    deploy = data.get("deploy", {})
    return (
        deploy.get("startCommand")
        or deploy.get("start_command")
        or data.get("startCommand")
        or ""
    )


def _nix_packages(data):
    return data.get("nixPackages") or data.get("nix", {}).get("packages") or []


def test_railway_toml_declares_poetry_build():
    cmd = _build_command(_load())
    assert "poetry install" in cmd, (
        f"railway.toml build.command must include 'poetry install' (got: {cmd!r})"
    )


def test_railway_toml_declares_gunicorn_start_with_dynamic_port():
    cmd = _start_command(_load())
    assert "gunicorn" in cmd, (
        f"railway.toml start command must include 'gunicorn' (got: {cmd!r})"
    )
    assert "0.0.0.0:$PORT" in cmd, (
        f"railway.toml start command must bind to 0.0.0.0:$PORT (got: {cmd!r})"
    )
    assert "0.0.0.0:5000" not in cmd, (
        f"railway.toml start command must NOT hardcode port 5000 (got: {cmd!r})"
    )


def test_railway_toml_declares_required_nix_packages():
    packages = _nix_packages(_load())
    assert "freetype" in packages, (
        f"railway.toml must declare 'freetype' nix package (got: {packages!r})"
    )
    assert "glibcLocales" in packages, (
        f"railway.toml must declare 'glibcLocales' nix package (got: {packages!r})"
    )
