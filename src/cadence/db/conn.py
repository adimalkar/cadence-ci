from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from cadence.config import settings

MIGRATIONS = Path(__file__).parent / "migrations"


@contextmanager
def connect(url: str | None = None):
    with psycopg.connect(url or settings.database_url, row_factory=dict_row) as conn:
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
