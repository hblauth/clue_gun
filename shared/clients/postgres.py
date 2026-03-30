"""
Reusable PostgreSQL client.

Connection is configured via environment variables (or a .env file):
    POSTGRES_HOST     default: localhost
    POSTGRES_PORT     default: 5432
    POSTGRES_DB       default: crossword
    POSTGRES_USER     default: crossword
    POSTGRES_PASSWORD default: crossword
"""

import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


def _dsn() -> str:
    return (
        f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
        f"port={os.getenv('POSTGRES_PORT', '5432')} "
        f"dbname={os.getenv('POSTGRES_DB', 'crossword')} "
        f"user={os.getenv('POSTGRES_USER', 'crossword')} "
        f"password={os.getenv('POSTGRES_PASSWORD', 'crossword')}"
    )


def get_conn():
    """Return a new psycopg2 connection. Caller is responsible for closing it."""
    return psycopg2.connect(_dsn())


@contextmanager
def transaction():
    """Context manager yielding a cursor inside a committed transaction."""
    conn = get_conn()
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
    finally:
        conn.close()


def upsert_crossword(cur, puzzle: dict) -> None:
    """Insert or update a crossword row keyed on puzzle_number."""
    cur.execute(
        """
        INSERT INTO crosswords_raw
            (puzzle_number, puzzle_date, blogger, url, across, down)
        VALUES
            (%(puzzle_number)s, %(puzzle_date)s, %(blogger)s, %(url)s,
             %(across)s::jsonb, %(down)s::jsonb)
        ON CONFLICT (puzzle_number) DO UPDATE SET
            puzzle_date = EXCLUDED.puzzle_date,
            blogger     = EXCLUDED.blogger,
            url         = EXCLUDED.url,
            across      = EXCLUDED.across,
            down        = EXCLUDED.down
        """,
        {
            "puzzle_number": puzzle["puzzle_number"],
            "puzzle_date": puzzle.get("date") or None,
            "blogger": puzzle.get("blogger") or None,
            "url": puzzle["url"],
            "across": psycopg2.extras.Json(puzzle.get("across", [])),
            "down": psycopg2.extras.Json(puzzle.get("down", [])),
        },
    )
