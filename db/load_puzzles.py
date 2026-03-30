"""
Load scraped crossword JSONs into the crosswords_raw PostgreSQL table.

Usage:
    python db/load_puzzles.py

Idempotent: uses ON CONFLICT DO UPDATE so safe to re-run.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.clients.postgres import transaction, upsert_crossword

PUZZLES_DIR = Path(__file__).parent.parent / "data" / "puzzles"

BATCH_SIZE = 100

# Date formats seen in the wild from timesforthetimes.co.uk:
#   "May 13, 2024"          — normal post, scraped from <time> text
#   "2023-06-17T00:00:00"   — Saturday post, from WP REST API
_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",   # ISO 8601 (Saturday REST API)
    "%B %d, %Y",            # "May 13, 2024"
    "%d %B %Y",             # "13 May 2024"
    "%Y-%m-%d",             # plain ISO date
]


def parse_date(raw: str | None):
    """Return a date object parsed from raw, or None if unparseable."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    print(f"  WARN: unrecognised date format {raw!r}", file=sys.stderr)
    return None


def run():
    puzzle_files = sorted(PUZZLES_DIR.glob("*.json"))
    if not puzzle_files:
        print(f"No puzzle files found in {PUZZLES_DIR}", file=sys.stderr)
        sys.exit(1)

    total = len(puzzle_files)
    loaded = skipped = 0

    batch = []
    for path in puzzle_files:
        try:
            puzzle = json.loads(path.read_text())
        except Exception as e:
            print(f"SKIP {path.name}: {e}", file=sys.stderr)
            skipped += 1
            continue

        if not puzzle.get("puzzle_number"):
            skipped += 1
            continue

        puzzle["_puzzle_date"] = parse_date(puzzle.get("date"))
        puzzle["_scraped_at"] = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        )
        batch.append(puzzle)

        if len(batch) >= BATCH_SIZE:
            loaded += _flush(batch)
            batch = []

    if batch:
        loaded += _flush(batch)

    print(f"\nDone. loaded={loaded}  skipped={skipped}  total={total}")


def _flush(batch: list[dict]) -> int:
    with transaction() as cur:
        for puzzle in batch:
            upsert_crossword(
                cur,
                puzzle,
                puzzle_date=puzzle["_puzzle_date"],
                scraped_at=puzzle["_scraped_at"],
            )
    return len(batch)


if __name__ == "__main__":
    run()
