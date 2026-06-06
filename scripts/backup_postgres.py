#!/usr/bin/env python3
"""
Postgres backup for UniFleet v2.

Runs `pg_dump --format=custom` against $DATABASE_URL, writes the
output to $UNIFLEET_BACKUP_DIR (default /backups), optionally
uploads to S3 if $UNIFLEET_BACKUP_S3_BUCKET is set, then rotates
files older than $UNIFLEET_BACKUP_RETAIN_DAYS (default 14).

Designed for the Railway Cron Schedule service (image
postgres:16-alpine has pg_dump built in), and for local
testing via `make backup` (which streams pg_dump output from
the unifleet-db container to the host).

Idempotent: filename is timestamped to the second, so re-runs
in the same minute produce different files; rotation is
deterministic on mtime.

Exit codes:
  0 — backup succeeded (rotation always succeeds; S3 upload is
      best-effort and logged but not fatal)
  1 — DATABASE_URL not set, or pg_dump failed
  2 — backup file missing or zero bytes after pg_dump returned 0
      (indicates pg_dump wrote somewhere unexpected)
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def log(msg: str, log_path: Path | None = None) -> None:
    """Print to stdout AND append to log file (if given)."""
    line = f"[{dt.datetime.now(dt.timezone.utc).isoformat()}] {msg}"
    print(line, flush=True)
    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            # Don't fail the backup if we can't write the log;
            # the operator will see the stdout line in cron.
            print(f"[warn] could not write {log_path}: {e}", file=sys.stderr, flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the pg_dump command and exit (no writes).",
    )
    return p.parse_args()


def build_pg_dump_cmd(dsn: str, out_file: Path) -> list[str]:
    """Build the pg_dump invocation. Out-file is a path INSIDE
    the container running this script (for local dev, the
    compose exec pipes the bytes; for production, the file is
    on the mounted volume)."""
    return [
        "pg_dump",
        "--format=custom",  # binary, compressed, parallel-restore-capable
        "--no-owner",       # portable across environments
        "--no-privileges",  # portable across environments
        "--file", str(out_file),
        dsn,
    ]


def run_pg_dump(dsn: str, out_file: Path) -> tuple[int, str, str]:
    """Run pg_dump as a subprocess. Returns (returncode, stdout, stderr)."""
    cmd = build_pg_dump_cmd(dsn, out_file)
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def upload_to_s3(local_file: Path, log_path: Path | None) -> bool:
    """Optional S3 upload. Returns True on success or skip (no
    config); False on failure (the caller logs it but does not
    fail the backup)."""
    bucket = os.environ.get("UNIFLEET_BACKUP_S3_BUCKET", "").strip()
    if not bucket:
        log("[s3] skipped (UNIFLEET_BACKUP_S3_BUCKET not set)", log_path)
        return True

    try:
        import boto3  # type: ignore
    except ImportError:
        log(f"[s3] skipped (boto3 not installed; file stays local at {local_file})", log_path)
        return True

    prefix = os.environ.get("UNIFLEET_BACKUP_S3_PREFIX", "unifleet/").strip("/")
    key = f"{prefix}/{local_file.name}" if prefix else local_file.name
    storage_class = os.environ.get("UNIFLEET_BACKUP_S3_STORAGE_CLASS", "STANDARD_IA")

    try:
        client = boto3.client("s3")
        client.upload_file(
            str(local_file),
            bucket,
            key,
            ExtraArgs={"StorageClass": storage_class},
        )
        log(f"[s3] uploaded s3://{bucket}/{key} ({storage_class})", log_path)
        return True
    except Exception as e:
        # Don't fail the backup over an S3 hiccup; the local copy
        # is the primary, S3 is the off-platform insurance.
        log(f"[s3] upload FAILED (local copy still safe at {local_file}): {e}", log_path)
        return False


def rotate_old(backup_dir: Path, retain_days: int, log_path: Path | None) -> int:
    """Delete files in backup_dir matching `unifleet-*.pgdump` with
    mtime older than retain_days. Returns count removed."""
    if retain_days <= 0:
        log(f"[rotate] disabled (retain_days={retain_days})", log_path)
        return 0
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=retain_days)
    removed = 0
    for f in backup_dir.glob("unifleet-*.pgdump"):
        mtime = dt.datetime.fromtimestamp(f.stat().st_mtime, tz=dt.timezone.utc)
        if mtime < cutoff:
            f.unlink()
            removed += 1
    log(f"[rotate] removed {removed} file(s) older than {retain_days} days", log_path)
    return removed


def main() -> int:
    args = parse_args()

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        return 1

    backup_dir = Path(os.environ.get("UNIFLEET_BACKUP_DIR", "/backups")).resolve()
    retain_days = int(os.environ.get("UNIFLEET_BACKUP_RETAIN_DAYS", "14"))
    log_path = backup_dir / "backup.log"

    # Sanity: pg_dump must be on PATH. On the postgres:16-alpine
    # image it is, and on the local db container too. If not,
    # we fail fast with a clear message rather than a confusing
    # FileNotFoundError.
    if not shutil.which("pg_dump"):
        log(f"ERROR: pg_dump not on PATH (looked for {os.environ.get('PATH', '')})", log_path)
        return 1

    if args.dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        out_file = backup_dir / f"unifleet-{timestamp}.pgdump"
        log(f"[dry-run] DATABASE_URL={_redact_dsn(dsn)} out={out_file} retain={retain_days}d", None)
        log(f"[dry-run] would run: {' '.join(shlex.quote(c) for c in build_pg_dump_cmd(dsn, out_file))}", None)
        return 0

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_file = backup_dir / f"unifleet-{timestamp}.pgdump"

    log(f"[start] DATABASE_URL={_redact_dsn(dsn)} out={out_file} retain={retain_days}d", log_path)

    rc, stdout, stderr = run_pg_dump(dsn, out_file)
    if rc != 0:
        # pg_dump creates the --file before writing, so a failed
        # dump leaves a 0-byte placeholder. Clean it up so a
        # retry doesn't accumulate junk (rotation would only
        # remove it in 14+ days otherwise).
        if out_file.exists() and out_file.stat().st_size == 0:
            try:
                out_file.unlink()
            except OSError:
                pass
        log(f"pg_dump FAILED (rc={rc}): {stderr.strip() or stdout.strip() or '(no output)'}", log_path)
        return 1

    if not out_file.exists() or out_file.stat().st_size == 0:
        log(f"pg_dump returned 0 but {out_file} is missing or empty", log_path)
        return 2

    size = out_file.stat().st_size
    log(f"[ok] wrote {out_file} ({size:,} bytes)", log_path)

    upload_to_s3(out_file, log_path)
    rotate_old(backup_dir, retain_days, log_path)
    log("[done]", log_path)
    return 0


def _redact_dsn(dsn: str) -> str:
    """Show the host/port/db but redact the password (if any)."""
    if "@" not in dsn:
        return dsn
    head, tail = dsn.rsplit("@", 1)
    if ":" in head.split("//", 1)[-1]:
        scheme_user, _ = head.rsplit(":", 1)
        return f"{scheme_user}:***@{tail}"
    return dsn


if __name__ == "__main__":
    sys.exit(main())
