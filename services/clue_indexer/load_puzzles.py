"""
Load scraped puzzle JSONs into the crosswords_raw Postgres table.

Reads every JSON from data/puzzles/, parses the date, and upserts using
the shared postgres client. Safe to re-run — existing rows are updated.

Usage:
    python services/clue_indexer/load_puzzles.py [--puzzles-dir PATH]
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from shared.clients.postgres import transaction, upsert_crossword  # noqa: E402

DEFAULT_DIR = Path(__file__).parent.parent.parent / "data" / "puzzles"
BATCH_SIZE = 100

# "15th May 2013 at 9:14 AM" → date(2013, 5, 15)
_DATE_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\s+(\w+)\s+(\d{4})")
_MONTHS = {m: i for i, m in enumerate(
    ["January","February","March","April","May","June",
     "July","August","September","October","November","December"], 1
)}


def parse_date(raw: str) -> date | None:
    if not raw:
        return None
    m = _DATE_RE.search(raw)
    if not m:
        return None
    day, month_str, year = int(m.group(1)), m.group(2), int(m.group(3))
    month = _MONTHS.get(month_str)
    if not month:
        return None
    return date(year, month, day)


def load(puzzles_dir: Path) -> None:
    files = sorted(puzzles_dir.glob("*.json"))
    if not files:
        print(f"No puzzle JSONs found in {puzzles_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {len(files):,} puzzles into crosswords_raw...")
    loaded = skipped = 0

    with transaction() as cur:
        for i, path in enumerate(files):
            try:
                puzzle = json.loads(path.read_text())
            except Exception as e:
                print(f"  SKIP {path.name}: {e}", file=sys.stderr)
                skipped += 1
                continue

            puzzle_date = parse_date(puzzle.get("date", ""))
            scraped_at = None  # file mtime as fallback
            try:
                import os
                from datetime import datetime, timezone
                mtime = os.path.getmtime(path)
                scraped_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
            except Exception:
                pass

            try:
                upsert_crossword(cur, puzzle, puzzle_date=puzzle_date, scraped_at=scraped_at)
                loaded += 1
            except Exception as e:
                print(f"  SKIP {path.name}: {e}", file=sys.stderr)
                skipped += 1

            if (i + 1) % BATCH_SIZE == 0:
                print(f"  {i + 1:,} / {len(files):,}", end="\r")

    print(f"\nDone. {loaded:,} upserted, {skipped:,} skipped.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--puzzles-dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    load(args.puzzles_dir)


if __name__ == "__main__":
    main()
