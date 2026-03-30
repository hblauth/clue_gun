"""
Load scraped crossword JSONs into the crosswords_raw PostgreSQL table.

Usage:
    python db/load_puzzles.py

Idempotent: uses ON CONFLICT DO UPDATE so safe to re-run.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.clients.postgres import transaction, upsert_crossword

PUZZLES_DIR = Path(__file__).parent.parent / "data" / "puzzles"

BATCH_SIZE = 100


def run():
    puzzle_files = sorted(PUZZLES_DIR.glob("*.json"))
    if not puzzle_files:
        print(f"No puzzle files found in {PUZZLES_DIR}", file=sys.stderr)
        sys.exit(1)

    total = len(puzzle_files)
    loaded = skipped = failed = 0

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

        batch.append(puzzle)

        if len(batch) >= BATCH_SIZE:
            loaded += _flush(batch)
            batch = []

    if batch:
        loaded += _flush(batch)

    print(f"\nDone. loaded={loaded}  skipped={skipped}  failed={failed}  total={total}")


def _flush(batch: list[dict]) -> int:
    with transaction() as cur:
        for puzzle in batch:
            upsert_crossword(cur, puzzle)
    return len(batch)


if __name__ == "__main__":
    run()
