from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from cadence.config import settings

MIGRATIONS = Path(__file__).parent / "migrations"


# Fail rather than block. A worker starting before Postgres is ready, or reaching a server
# that is restarting, should get an exception the retry logic can act on -- psycopg's
# default is to wait indefinitely, which turns a transient outage into a stuck process.
CONNECT_TIMEOUT_SECONDS = 10


@contextmanager
def connect(url: str | None = None, *, connect_timeout: int = CONNECT_TIMEOUT_SECONDS):
    with psycopg.connect(
        url or settings.database_url,
        row_factory=dict_row,
        connect_timeout=connect_timeout,
    ) as conn:
        yield conn


def apply_migrations(url: str | None = None) -> list[str]:
    """Apply every .sql file in migrations/ in name order, once each.

    Deliberately minimal -- a schema_migrations table and sorted filenames. Alembic earns
    its keep when there are branching migration histories and multiple deployed versions;
    neither exists yet, and one less dependency is one less thing to operate.
    """
    applied: list[str] = []
    with connect(url) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            " name text PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r["name"] for r in conn.execute("SELECT name FROM schema_migration")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migration (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
        conn.commit()
    return applied
