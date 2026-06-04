"""db.apply — apply one or more SQL files to a Postgres database.

Usage: python db/apply.py [--dsn DSN] FILE [FILE ...]

Used by F2.1 T2 to apply db/schema.sql and F2.1 T3 to apply the seed
SQL files. Idempotency depends on the SQL itself (e.g. ``CREATE TABLE
IF NOT EXISTS``); the applier does not drop tables or run destructive
operations.
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply one or more SQL files to a Postgres database."
    )
    parser.add_argument(
        "sql_files",
        nargs="+",
        type=Path,
        help="One or more SQL files to apply, in order.",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres DSN (e.g. postgresql://user:pw@host:port/db). "
             "Falls back to $DATABASE_URL.",
    )
    args = parser.parse_args()

    sql_text = "\n".join(path.read_text() for path in args.sql_files)

    with psycopg.connect(args.dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(sql_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
