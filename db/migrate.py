"""
Run all SQL migrations in db/migrations/ in order.

Usage:
    python db/migrate.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.clients.postgres import get_conn

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run():
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        filename TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not migration_files:
            print("No migration files found.")
            return

        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT filename FROM schema_migrations")
                applied = {row[0] for row in cur.fetchall()}

        for path in migration_files:
            if path.name in applied:
                print(f"  skip  {path.name}")
                continue
            sql = path.read_text()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s)",
                        (path.name,),
                    )
            print(f"  apply {path.name}")

        print("Migrations complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
