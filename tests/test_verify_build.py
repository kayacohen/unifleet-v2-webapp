"""Tests for scripts/verify_build.py — the F1.1 one-time build probe.

The probe imports 7 heavy dependencies (Flask, Pillow, reportlab, qrcode,
pandas, pytz, psycopg) and runs SELECT 1 against $DATABASE_URL.

We test the probe as a black box by calling its `main()` function and
capturing its stdout, with `psycopg.connect` and the underlying imports
mocked via unittest.mock (no real Postgres required).
"""
import io
import os
import sys
from contextlib import contextmanager
from unittest import mock

import pytest

import scripts.verify_build as probe


# --- Helpers: stand-in for the real connect-and-execute flow ---

class _FakeCursor:
    def __init__(self, fetch_value):
        self._fetch_value = fetch_value
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)

    def fetchone(self):
        return (self._fetch_value,)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@contextmanager
def _fake_psycopg_connect_ok(url, **kwargs):
    yield _FakeConnection(_FakeCursor(fetch_value=1))


@contextmanager
def _fake_psycopg_connect_fail(url, **kwargs):
    raise RuntimeError("simulated connection failure")
    yield  # unreachable, satisfies type checker


def _run_probe(capsys, monkeypatch, *, env, connect):
    """Invoke probe.main() with a controlled env and a controlled connect."""
    for k in ("DATABASE_URL",):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with mock.patch.object(probe.psycopg, "connect", side_effect=connect):
        rc = probe.main()
    out = capsys.readouterr().out
    return rc, out


# --- Happy path ---

def test_passes_when_all_deps_and_db_are_present(capsys, monkeypatch):
    rc, out = _run_probe(
        capsys, monkeypatch,
        env={"DATABASE_URL": "postgres://u:p@h:5432/d"},
        connect=_fake_psycopg_connect_ok,
    )
    assert rc == 0
    assert "RESULT: PASS" in out


def test_enumerates_all_required_deps(capsys, monkeypatch):
    _, out = _run_probe(
        capsys, monkeypatch,
        env={"DATABASE_URL": "postgres://u:p@h:5432/d"},
        connect=_fake_psycopg_connect_ok,
    )
    for name in ("Flask", "Pillow", "reportlab", "qrcode", "pandas", "pytz", "psycopg"):
        assert name in out, f"probe output must mention dep {name!r} (got: {out!r})"


# --- Failure paths ---

def test_fails_when_a_dep_is_missing(capsys, monkeypatch):
    with mock.patch.dict(sys.modules, {"PIL": None}):
        rc, out = _run_probe(
            capsys, monkeypatch,
            env={"DATABASE_URL": "postgres://u:p@h:5432/d"},
            connect=_fake_psycopg_connect_ok,
        )
    assert rc != 0
    assert "FAIL: Pillow" in out
    assert "RESULT: PASS" not in out


def test_fails_when_db_connection_fails(capsys, monkeypatch):
    rc, out = _run_probe(
        capsys, monkeypatch,
        env={"DATABASE_URL": "postgres://u:p@h:5432/d"},
        connect=_fake_psycopg_connect_fail,
    )
    assert rc != 0
    assert "FAIL: db" in out


# --- Defensive paths ---

def test_skips_db_when_database_url_unset(capsys, monkeypatch):
    rc, out = _run_probe(
        capsys, monkeypatch,
        env={},
        connect=_fake_psycopg_connect_ok,  # should never be called
    )
    assert rc == 0
    assert "SKIP: db" in out


def test_db_connection_uses_five_second_timeout(capsys, monkeypatch):
    captured_kwargs = {}

    @contextmanager
    def capturing_connect(url, **kwargs):
        captured_kwargs.update(kwargs)
        yield _FakeConnection(_FakeCursor(fetch_value=1))

    with mock.patch.object(probe.psycopg, "connect", side_effect=capturing_connect):
        _run_probe(
            capsys, monkeypatch,
            env={"DATABASE_URL": "postgres://u:p@h:5432/d"},
            connect=capturing_connect,
        )
    assert captured_kwargs.get("connect_timeout") == 5
