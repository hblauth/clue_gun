"""
Enrich word lists with wordfreq frequency scores.

Reads data/words/all_words.txt and writes data/words/wordfreq.csv with:
    word, zipf_score, frequency

Zipf scale reference:
    6+    extremely common  (the, of, and)
    5     common            (house, road, make)
    4     moderately common (cryptic, solver)
    3     uncommon          (escarpment, bivalve)
    2     rare
    1     very rare / not in corpus

Usage:
    python services/clue_indexer/enrich_wordfreq.py
"""

import csv
import sys
from pathlib import Path

from wordfreq import word_frequency, zipf_frequency

WORDS_DIR = Path(__file__).parent.parent.parent / "data" / "words"
OUT_PATH = WORDS_DIR / "wordfreq.csv"


def run():
    words_file = WORDS_DIR / "all_words.txt"
    if not words_file.exists():
        print(f"Not found: {words_file}", file=sys.stderr)
        print("Run extract_words.py first.", file=sys.stderr)
        sys.exit(1)

    words = [w.strip() for w in words_file.read_text().splitlines() if w.strip()]
    print(f"Scoring {len(words):,} words...")

    rows = []
    for word in words:
        rows.append(
            {
                "word": word,
                "zipf_score": zipf_frequency(word, "en"),
                "frequency": word_frequency(word, "en"),
            }
        )

    rows.sort(key=lambda r: r["zipf_score"], reverse=True)

    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["word", "zipf_score", "frequency"])
        writer.writeheader()
        writer.writerows(rows)

    in_corpus = sum(1 for r in rows if r["zipf_score"] > 0)
    print(f"Done. {in_corpus:,} of {len(rows):,} words found in wordfreq corpus.")
    print(f"Output: {OUT_PATH}")


if __name__ == "__main__":
    run()
