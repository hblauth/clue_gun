"""
Clue selector: picks which puzzle and clue to feature for a given date.

Selection strategy:
  1. Find the crossword whose puzzle_date matches the target date.
     If none, fall back to the puzzle with the highest puzzle_number
     scraped before or on that date (most recent available).
  2. From that puzzle's across clues, pick the one whose answer has the
     highest wordfreq Zipf score (most recognizable to a general audience).
     Falls back to the first across clue if wordfreq is unavailable.

Returns a dict with keys: puzzle_number, clue_ref, clue_text, answer, letter_count.
"""

import sys
from datetime import date

from shared.clients.postgres import transaction


def select_clue_for_date(target_date: date) -> dict | None:
    """
    Return the featured clue for target_date, or None if no puzzle is available.

    Returned dict:
        puzzle_number: int
        clue_ref: str       e.g. "across_3"
        clue_text: str      e.g. "Puzzling crime (7)"
        answer: str         e.g. "MYSTERY"
        letter_count: str   e.g. "7"
        puzzle_url: str
    """
    with transaction() as cur:
        # Try exact date match first, then fall back to most recent puzzle
        cur.execute(
            """
            SELECT puzzle_number, across, url
            FROM crosswords_raw
            WHERE puzzle_date = %s
            ORDER BY puzzle_number DESC
            LIMIT 1
            """,
            (target_date,),
        )
        row = cur.fetchone()

        if row is None:
            cur.execute(
                """
                SELECT puzzle_number, across, url
                FROM crosswords_raw
                WHERE puzzle_date <= %s
                  AND across != '[]'::jsonb
                ORDER BY puzzle_date DESC, puzzle_number DESC
                LIMIT 1
                """,
                (target_date,),
            )
            row = cur.fetchone()

        if row is None:
            return None

        clues = row["across"]
        if not clues:
            return None

        # Fetch Zipf scores for all answer words in one query.
        answers = [
            c.get("answer", "").lower() for c in clues if c.get("answer", "").strip()
        ]
        scores: dict[str, float] = {}
        if answers:
            cur.execute(
                "SELECT word, zipf_score FROM word_frequency WHERE word = ANY(%s)",
                (answers,),
            )
            scores = {r["word"]: r["zipf_score"] for r in cur.fetchall()}

    chosen_idx, chosen = _pick_best_clue(clues, scores)

    answer = chosen.get("answer", "")
    letter_count = _extract_letter_count(chosen.get("text", ""), answer)

    return {
        "puzzle_number": row["puzzle_number"],
        "clue_ref": f"across_{chosen_idx}",
        "clue_text": _strip_letter_count(chosen.get("text", "")),
        "answer": answer,
        "letter_count": letter_count,
        "puzzle_url": row["url"],
    }


def _pick_best_clue(
    clues: list[dict], scores: dict[str, float]
) -> tuple[int, dict]:
    """Return (index, clue) for the clue with the highest Zipf score from the DB.
    Skips clues with no answer. Falls back to the first clue with an answer."""
    best_idx = 0
    best_score = -1.0
    best_clue = clues[0]

    for i, clue in enumerate(clues):
        answer = clue.get("answer", "").strip()
        if not answer:
            continue
        if best_score == -1.0:
            # Ensure we always have a valid fallback.
            best_idx, best_clue = i, clue
        score = scores.get(answer.lower(), 0.0)
        if score > best_score:
            best_score = score
            best_idx = i
            best_clue = clue

    return best_idx, best_clue


def _extract_letter_count(clue_text: str, answer: str) -> str:
    """Extract letter count from clue text like 'Some clue (7)' → '7'.
    Falls back to len(answer) if no parenthetical found."""
    import re

    m = re.search(r"\(([0-9,\-]+)\)\s*$", clue_text)
    if m:
        return m.group(1)
    if answer:
        return str(len(answer))
    return ""


def _strip_letter_count(clue_text: str) -> str:
    """Remove trailing (7) or (3,4) from clue text."""
    import re

    return re.sub(r"\s*\([0-9,\-]+\)\s*$", "", clue_text).strip()


if __name__ == "__main__":
    target = date.today()
    result = select_clue_for_date(target)
    if result:
        print(f"Puzzle #{result['puzzle_number']} — {result['clue_ref']}")
        print(f"  Clue:   {result['clue_text']} ({result['letter_count']})")
        print(f"  Answer: {result['answer']}")
    else:
        print("No puzzle found for", target, file=sys.stderr)
        sys.exit(1)
