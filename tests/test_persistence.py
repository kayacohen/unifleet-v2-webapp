"""
Tests for persistence.get_repo() — the F2.2 backend dispatcher.

F2.2 wires 'pg' and 'postgres' to the new PostgresRepo. The legacy
'csv' (default) and 'db' (incomplete SQLite) branches must still work.
"""

from persistence import CSVRepo, DBRepo, get_repo
from db.postgres_repo import PostgresRepo


def test_get_repo_csv_returns_csvrepo():
    """Default 'csv' backend still returns a CSVRepo."""
    repo = get_repo("csv")
    assert isinstance(repo, CSVRepo)


def test_get_repo_empty_string_returns_csvrepo():
    """An empty / None backend falls back to CSVRepo."""
    repo = get_repo("")
    assert isinstance(repo, CSVRepo)


def test_get_repo_db_returns_dbrepo(tmp_path, monkeypatch):
    """The legacy 'db' backend still returns a DBRepo (SQLite).

    We monkeypatch the cwd so the SQLite file is created in tmp_path
    rather than /app/data/ (where the test container would otherwise
    leave an unifleet.db file behind).
    """
    monkeypatch.chdir(tmp_path)
    repo = get_repo("db")
    try:
        assert isinstance(repo, DBRepo)
    finally:
        # Close the connection so the file handle is released on Windows too.
        if hasattr(repo, "conn") and repo.conn is not None:
            try:
                repo.conn.close()
            except Exception:
                pass


def test_get_repo_pg_returns_postgres_repo(monkeypatch, schema_db):
    """The new 'pg' backend returns a PostgresRepo pointed at the test DB."""
    monkeypatch.setenv("UNIFLEET_DB_DSN", schema_db)
    repo = get_repo("pg")
    try:
        assert isinstance(repo, PostgresRepo)
    finally:
        repo.close()


def test_get_repo_postgres_alias_returns_postgres_repo(monkeypatch, schema_db):
    """The 'postgres' (full word) alias also returns a PostgresRepo."""
    monkeypatch.setenv("UNIFLEET_DB_DSN", schema_db)
    repo = get_repo("postgres")
    try:
        assert isinstance(repo, PostgresRepo)
    finally:
        repo.close()


def test_get_repo_unknown_backend_falls_back_to_csv():
    """An unknown backend falls back to CSVRepo (defensive default)."""
    repo = get_repo("nonexistent_backend")
    assert isinstance(repo, CSVRepo)
