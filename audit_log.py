"""
audit_log.py — Postgres-backed ops audit log.

F2.4 of the UniFleet v2 → Railway + Postgres migration. Replaces
the CSV-on-disk implementation in main.py (data/ops_audit_log.csv)
with an append-only insert into the F2.1 schema's `audit_log`
table. The public function signature is preserved so call sites
in main.py do not change.

Public API (unchanged from main.py:134):
  append_audit(action, voucher_id, from_status="", to_status="", note="")
    Inserts one row into the audit_log table. Reads route, actor_ip,
    user_agent from flask.request (must be called inside a request
    context). The DB column "timestamp" is set to NOW() at INSERT
    time; the route is truncated to 200 chars (VARCHAR(200) limit)
    and actor_ip to 50 chars (VARCHAR(50) limit) to avoid overruns.

Type handling (CSV-world -> Postgres):
  - "" (empty string) for from_status / to_status / note -> NULL
  - "" or None voucher_id -> NULL (the FK column is nullable)
  - Decimal / datetime / etc. are not applicable here; all values
    are strings or None.

Failure policy: matches the legacy CSV impl. An audit write failure
is logged to stderr and swallowed so it never breaks the request
flow. The DB has a NOT NULL constraint on `action` only; if a
caller passes action="" the INSERT will fail and be swallowed.
"""

import sys
from typing import Optional

from flask import request

from db.pool import get_pool


def append_audit(
    action: str,
    voucher_id: Optional[str],
    from_status: str = "",
    to_status: str = "",
    note: str = "",
) -> None:
    """Append one audit row to the audit_log table.

    Must be called from within a Flask request context (the only
    place main.py calls it). Captures route / actor_ip / user_agent
    from the current request. Failures are caught and logged to
    stderr so an audit write never breaks the request flow.
    """
    try:
        # Capture the request context FIRST so it works even if the
        # pool/DB is slow to open. Truncate to the schema's VARCHAR
        # limits to avoid overrun errors.
        route = (request.path or "")[:200]
        actor_ip = (
            request.headers.get("X-Forwarded-For", request.remote_addr or "")
            or ""
        )[:50]
        user_agent = (request.headers.get("User-Agent", "") or "")
        vid = (voucher_id or "").strip() or None  # NULL if blank

        pool = get_pool()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_log
                        (timestamp, action, voucher_id, from_status, to_status,
                         route, actor_ip, user_agent, note)
                    VALUES
                        (NOW(), %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        action,
                        vid,
                        from_status or None,
                        to_status or None,
                        route,
                        actor_ip,
                        user_agent,
                        note or None,
                    ),
                )
            conn.commit()
    except Exception as e:
        # Audit write failure must never break the request flow.
        # (The legacy CSV impl logged to stdout; we log to stderr.)
        print(f"[audit_log] write failed: {e}", file=sys.stderr)
