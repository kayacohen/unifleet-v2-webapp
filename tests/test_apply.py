"""
Tests for db/apply.py — the generic SQL applier.

Each test invokes the script as a subprocess (matching the pattern in
`scripts/verify_build.py`) and asserts on the database state via a
direct `psycopg` connection from the `postgres_db` fixture.
"""

import os
import socket
import subprocess
import sys
import threading
import time

import psycopg
import pytest


def test_apply_runs_a_trivial_sql_file_and_returns_zero(tmp_path, postgres_db):
    """GIVEN a fixture SQL file containing `CREATE TABLE IF NOT EXISTS foo (id INT);`
    WHEN `apply.py path/to/fixture.sql` is invoked
    THEN exit code is 0 AND a `foo` table exists in the database."""
    sql_file = tmp_path / "trivial.sql"
    sql_file.write_text("CREATE TABLE IF NOT EXISTS foo (id INT);")

    result = subprocess.run(
        [sys.executable, "db/apply.py", str(sql_file), "--dsn", postgres_db],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"apply.py exited {result.returncode}\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )

    with psycopg.connect(postgres_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'foo'"
            )
            rows = cur.fetchall()

    assert rows == [("foo",)], f"Expected foo table to exist, got {rows}"


def test_apply_accepts_dsn_from_env_var(tmp_path, postgres_db):
    """GIVEN `DATABASE_URL` is set to the test DSN
    WHEN `apply.py path/to/fixture.sql` is invoked (no `--dsn`)
    THEN the script connects to the URL from the env var."""
    sql_file = tmp_path / "trivial.sql"
    sql_file.write_text("CREATE TABLE IF NOT EXISTS foo (id INT);")

    env = os.environ.copy()
    env["DATABASE_URL"] = postgres_db

    result = subprocess.run(
        [sys.executable, "db/apply.py", str(sql_file)],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"apply.py exited {result.returncode}\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )

    with psycopg.connect(postgres_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'foo'"
            )
            rows = cur.fetchall()

    assert rows == [("foo",)], f"Expected foo table to exist, got {rows}"


def test_apply_accepts_explicit_dsn_flag(tmp_path, postgres_db):
    """GIVEN a `--dsn` flag is passed (and `DATABASE_URL` is set to a different, bogus DSN)
    WHEN invoked
    THEN the script connects to the `--dsn` value, not the env var.

    The bogus env-var DSN points to a database that does not exist; if the
    script falls back to it, ``psycopg.connect`` will raise InvalidCatalogName
    and the apply will exit non-zero. Proving the apply exits 0 + the table
    exists in the --dsn DB proves the flag took precedence."""
    sql_file = tmp_path / "trivial.sql"
    sql_file.write_text("CREATE TABLE IF NOT EXISTS foo (id INT);")

    env = os.environ.copy()
    env["DATABASE_URL"] = (
        "postgresql://unifleet:unifleet_dev_pw@db:5432/this_db_should_not_exist"
    )

    result = subprocess.run(
        [sys.executable, "db/apply.py", str(sql_file), "--dsn", postgres_db],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"apply.py exited {result.returncode}\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )

    with psycopg.connect(postgres_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'foo'"
            )
            rows = cur.fetchall()

    assert rows == [("foo",)], f"Expected foo table to exist in --dsn DB, got {rows}"


def test_apply_returns_nonzero_on_connection_failure(tmp_path):
    """GIVEN `--dsn` points to a bogus port WHEN invoked
    THEN exit code is non-zero AND stderr mentions the connection failure.

    Uses a port the OS just released (via bind to port 0). The window
    where another process could grab it is microseconds, which is
    reliable for local dev and CI."""
    sql_file = tmp_path / "trivial.sql"
    sql_file.write_text("CREATE TABLE IF NOT EXISTS foo (id INT);")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        bogus_port = s.getsockname()[1]

    bogus_dsn = (
        f"postgresql://unifleet:unifleet_dev_pw@127.0.0.1:{bogus_port}/unifleet"
    )

    result = subprocess.run(
        [sys.executable, "db/apply.py", str(sql_file), "--dsn", bogus_dsn],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode != 0, (
        f"apply.py exited {result.returncode} (expected non-zero)\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    assert "connection" in result.stderr.lower(), (
        f"stderr should mention connection failure, got: {result.stderr!r}"
    )


def test_apply_is_idempotent_against_an_existing_schema(tmp_path, postgres_db):
    """GIVEN the fixture SQL has been applied once
    WHEN applied a second time
    THEN exit code is 0 AND the `foo` table still exists
    (the SQL uses `CREATE TABLE IF NOT EXISTS` so the second apply is a no-op)."""
    sql_file = tmp_path / "trivial.sql"
    sql_file.write_text("CREATE TABLE IF NOT EXISTS foo (id INT);")

    first = subprocess.run(
        [sys.executable, "db/apply.py", str(sql_file), "--dsn", postgres_db],
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, (
        f"first apply exited {first.returncode}\n"
        f"stdout: {first.stdout!r}\n"
        f"stderr: {first.stderr!r}"
    )

    second = subprocess.run(
        [sys.executable, "db/apply.py", str(sql_file), "--dsn", postgres_db],
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0, (
        f"second apply exited {second.returncode} (apply is not idempotent)\n"
        f"stdout: {second.stdout!r}\n"
        f"stderr: {second.stderr!r}"
    )

    with psycopg.connect(postgres_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'foo'"
            )
            rows = cur.fetchall()

    assert rows == [("foo",)], f"Expected foo table to exist after re-apply, got {rows}"


def test_apply_does_not_drop_existing_data(tmp_path, postgres_db):
    """GIVEN the schema is applied AND a row is inserted into `foo`
    WHEN the schema is applied again
    THEN the row still exists (no `DROP TABLE` in the apply)."""
    sql_file = tmp_path / "trivial.sql"
    sql_file.write_text("CREATE TABLE IF NOT EXISTS foo (id INT);")

    first = subprocess.run(
        [sys.executable, "db/apply.py", str(sql_file), "--dsn", postgres_db],
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, (
        f"first apply exited {first.returncode}\n"
        f"stderr: {first.stderr!r}"
    )

    with psycopg.connect(postgres_db) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO foo (id) VALUES (42)")
        conn.commit()

    second = subprocess.run(
        [sys.executable, "db/apply.py", str(sql_file), "--dsn", postgres_db],
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0, (
        f"second apply exited {second.returncode}\n"
        f"stderr: {second.stderr!r}"
    )

    with psycopg.connect(postgres_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM foo ORDER BY id")
            rows = cur.fetchall()

    assert rows == [(42,)], f"Expected row (42,) to survive re-apply, got {rows}"


def _start_silent_server():
    """Bind a TCP socket that accepts connections but never reads or writes.

    Yields (port, server_socket). Caller is responsible for closing the
    server socket. The accept loop runs in a daemon thread."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]

    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                server.settimeout(0.5)
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            # Hold the connection open but never send/receive.
            # The client will block on the Postgres protocol handshake.

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, server, stop


def test_apply_uses_five_second_timeout(tmp_path):
    """GIVEN the DSN points to a port that accepts but never responds
    WHEN invoked
    THEN the script exits with a connection timeout error within ~6 seconds
    (5s timeout + small overhead)."""
    sql_file = tmp_path / "trivial.sql"
    sql_file.write_text("CREATE TABLE IF NOT EXISTS foo (id INT);")

    port, server, stop = _start_silent_server()
    try:
        dsn = f"postgresql://unifleet:unifleet_dev_pw@127.0.0.1:{port}/unifleet"

        start = time.monotonic()
        result = subprocess.run(
            [sys.executable, "db/apply.py", str(sql_file), "--dsn", dsn],
            capture_output=True,
            text=True,
            timeout=15,
        )
        elapsed = time.monotonic() - start

        assert result.returncode != 0, (
            f"expected non-zero exit on silent server, got {result.returncode}\n"
            f"stderr: {result.stderr!r}"
        )
        # 5s timeout + small overhead; allow generous headroom for slow CI.
        assert elapsed < 10, (
            f"apply took {elapsed:.2f}s; expected ~6s with 5s timeout + overhead"
        )
    finally:
        stop.set()
        server.close()


def test_apply_uses_psycopg_3_not_psycopg2():
    """GIVEN the apply script imports its DB driver
    WHEN inspected THEN the import is `psycopg` (the locked driver from F1.1),
    not `psycopg2` or `pg8000`."""
    import db.apply

    module_globals = vars(db.apply)
    psycopg_module = module_globals.get("psycopg")
    assert psycopg_module is not None, "db.apply should import psycopg"
    assert psycopg_module.__version__.startswith("3."), (
        f"expected psycopg 3.x, got version {psycopg_module.__version__!r}"
    )
    assert "psycopg2" not in module_globals, "db.apply should not import psycopg2"
    assert "pg8000" not in module_globals, "db.apply should not import pg8000"


def test_apply_can_run_multiple_sql_files_in_order(tmp_path, postgres_db):
    """GIVEN two SQL files passed on the command line
    WHEN invoked
    THEN both are applied AND the second file can reference the first file's
    tables (FK works).

    The first file creates a parent table; the second file creates a child
    table with a FK to the parent. After applying both, inserting into the
    child with a valid parent id succeeds and with an invalid id fails —
    proving the FK was actually created and enforced.

    Uses unique table names (multi_parent, multi_child) to avoid colliding
    with the `foo` table created by earlier tests in the session-scoped DB."""
    parent_sql = tmp_path / "parent.sql"
    parent_sql.write_text(
        "CREATE TABLE IF NOT EXISTS multi_parent (id INT PRIMARY KEY);"
    )

    child_sql = tmp_path / "child.sql"
    child_sql.write_text(
        "CREATE TABLE IF NOT EXISTS multi_child ("
        "  id INT PRIMARY KEY, "
        "  parent_id INT REFERENCES multi_parent(id)"
        ");"
    )

    result = subprocess.run(
        [
            sys.executable,
            "db/apply.py",
            str(parent_sql),
            str(child_sql),
            "--dsn",
            postgres_db,
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"apply.py exited {result.returncode}\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )

    with psycopg.connect(postgres_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_name IN ('multi_parent', 'multi_child') "
                "ORDER BY table_name"
            )
            tables = cur.fetchall()

    assert tables == [("multi_child",), ("multi_parent",)], (
        f"Expected multi_parent and multi_child, got {tables}"
    )

    with psycopg.connect(postgres_db) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO multi_parent (id) VALUES (1)")
            cur.execute("INSERT INTO multi_child (id, parent_id) VALUES (10, 1)")
        conn.commit()

    with psycopg.connect(postgres_db, autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                cur.execute(
                    "INSERT INTO multi_child (id, parent_id) VALUES (20, 999)"
                )
