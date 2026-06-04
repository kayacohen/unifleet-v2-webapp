"""
Shared pytest fixtures for tests that need a real Postgres database.

These tests run inside the `web` Docker container via `make test-db`,
where `db:5432` resolves to the unifleet-db container started by
`docker-compose.yml`.

The `postgres_db` fixture creates a unique `unifleet_test_<uuid>` database
once per test session, yields a DSN pointing to it, and drops the database
on teardown. Tests must use this fixture (or the `postgres_db`-derived DSN)
to get a clean schema per session.

The `schema_db` fixture additionally applies `db/schema.sql` to that
fresh database once per session, so the full F2.1 schema is in place
for the T2/T3 tests.

Override the admin DSN with the `TEST_DATABASE_ADMIN_DSN` env var if needed
(e.g., to point at a different maintenance DB). The admin DSN must point
to a database that already exists (typically `postgres` or `unifleet`),
not the test database itself, because we use it to issue CREATE/DROP DATABASE.
"""

import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

ADMIN_DSN = "postgresql://unifleet:unifleet_dev_pw@db:5432/postgres"

_TERMINATE_CONNECTIONS = (
    "SELECT pg_terminate_backend(pid) "
    "FROM pg_stat_activity "
    "WHERE datname = %s AND pid <> pg_backend_pid()"
)


@pytest.fixture(scope="session")
def postgres_db():
    """Yield a DSN to a fresh `unifleet_test_<uuid>` database; drop on teardown."""
    db_name = f"unifleet_test_{uuid.uuid4().hex[:8]}"
    test_dsn = ADMIN_DSN.rsplit("/", 1)[0] + f"/{db_name}"

    with psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=5) as admin:
        admin.execute(f'CREATE DATABASE "{db_name}"')

    try:
        yield test_dsn
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=5) as admin:
            admin.execute(_TERMINATE_CONNECTIONS, (db_name,))
            admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')


@pytest.fixture(scope="session")
def schema_db(postgres_db):
    """Apply db/schema.sql to the postgres_db database; yield the same DSN."""
    schema_path = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
    result = subprocess.run(
        [sys.executable, "db/apply.py", str(schema_path), "--dsn", postgres_db],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"db/apply.py failed for db/schema.sql\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    yield postgres_db


@pytest.fixture(scope="session")
def seeded_db(schema_db):
    """Apply the seeds on top of the schema; yield the same DSN."""
    db_dir = Path(__file__).resolve().parent.parent / "db"
    seed_files = [db_dir / "seed_stations.sql", db_dir / "seed_prices.sql"]
    result = subprocess.run(
        [sys.executable, "db/apply.py", *[str(p) for p in seed_files],
         "--dsn", schema_db],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"db/apply.py failed for seed files\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    yield schema_db

