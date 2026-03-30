"""
Extract unique words from scraped crossword puzzle JSON files.

Produces two output files:
  - answer_words.txt   : unique single words appearing as crossword answers
  - clue_words.txt     : unique words appearing in clue text
  - all_words.txt      : union of the above
"""

import json
import os
import re
import sys
from pathlib import Path

PUZZLES_DIR = Path(__file__).parent.parent / "times_scraper" / "data" / "puzzles"
OUT_DIR = Path(__file__).parent / "data"


def tokenise(text: str) -> list[str]:
    """Split text into lowercase alphabetic tokens, dropping numbers and punctuation."""
    return [w.lower() for w in re.findall(r"[a-zA-Z]+", text)]


def extract_answer_words(clues: list[dict]) -> set[str]:
    words = set()
    for clue in clues:
        answer = clue.get("answer", "")
        if answer:
            # Multi-word answers (e.g. "UPPER CASE") → split into individual words
            for w in tokenise(answer):
                if w:
                    words.add(w)
    return words


def extract_clue_words(clues: list[dict]) -> set[str]:
    words = set()
    for clue in clues:
        for w in tokenise(clue.get("text", "")):
            if w:
                words.add(w)
    return words


def main():
    puzzle_files = sorted(PUZZLES_DIR.glob("*.json"))
    if not puzzle_files:
        print(f"No puzzle files found in {PUZZLES_DIR}", file=sys.stderr)
        sys.exit(1)

    answer_words: set[str] = set()
    clue_words: set[str] = set()
    parsed = skipped = 0

    for path in puzzle_files:
        try:
            with open(path) as f:
                puzzle = json.load(f)
        except Exception as e:
            print(f"SKIP {path.name}: {e}", file=sys.stderr)
            skipped += 1
            continue

        clues = (puzzle.get("across") or []) + (puzzle.get("down") or [])
        if not clues:
            skipped += 1
            continue

        answer_words |= extract_answer_words(clues)
        clue_words |= extract_clue_words(clues)
        parsed += 1

    OUT_DIR.mkdir(exist_ok=True)

    def write_wordlist(path: Path, words: set[str]):
        with open(path, "w") as f:
            f.write("\n".join(sorted(words)) + "\n")
        print(f"  {path.name}: {len(words):,} words")

    print(f"\nParsed {parsed} puzzles, skipped {skipped}")
    print("Writing word lists:")
    write_wordlist(OUT_DIR / "answer_words.txt", answer_words)
    write_wordlist(OUT_DIR / "clue_words.txt", clue_words)
    write_wordlist(OUT_DIR / "all_words.txt", answer_words | clue_words)


if __name__ == "__main__":
    main()
