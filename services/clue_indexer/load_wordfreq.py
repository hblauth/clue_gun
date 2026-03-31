"""
Load wordfreq scores into the word_frequency Postgres table.

Reads data/words/wordfreq.csv (produced by enrich_wordfreq.py) and
batch-upserts every row into the word_frequency table.

Usage:
    python services/clue_indexer/load_wordfreq.py [--csv PATH]

Run enrich_wordfreq.py first if the CSV does not exist.
"""

import argparse
import csv
import sys
from pathlib import Path

import psycopg2.extras

# Allow running from repo root or from the service directory.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from shared.clients.postgres import transaction  # noqa: E402

DEFAULT_CSV = Path(__file__).parent.parent.parent / "data" / "words" / "wordfreq.csv"
BATCH_SIZE = 1000


def load(csv_path: Path) -> None:
    if not csv_path.exists():
        print(f"Not found: {csv_path}", file=sys.stderr)
        print("Run enrich_wordfreq.py first.", file=sys.stderr)
        sys.exit(1)

    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("CSV is empty — nothing to load.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {len(rows):,} words into word_frequency table...")

    upserted = 0
    with transaction() as cur:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO word_frequency (word, zipf_score, frequency, loaded_at)
                VALUES %s
                ON CONFLICT (word) DO UPDATE SET
                    zipf_score = EXCLUDED.zipf_score,
                    frequency  = EXCLUDED.frequency,
                    loaded_at  = NOW()
                """,
                [
                    (r["word"], float(r["zipf_score"]), float(r["frequency"]))
                    for r in batch
                ],
                template="(%s, %s, %s, NOW())",
            )
            upserted += len(batch)
            print(f"  {upserted:,} / {len(rows):,}", end="\r")

    print(f"\nDone. {upserted:,} rows upserted.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Path to wordfreq CSV (default: {DEFAULT_CSV})",
    )
    args = parser.parse_args()
    load(args.csv)


if __name__ == "__main__":
    main()
