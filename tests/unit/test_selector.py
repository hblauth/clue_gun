import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from services.social_bot.selector import _extract_letter_count, _strip_letter_count, _pick_best_clue


def test_strip_letter_count_basic():
    assert _strip_letter_count("Some clue (7)") == "Some clue"


def test_strip_letter_count_hyphenated():
    assert _strip_letter_count("Some clue (3-4)") == "Some clue"


def test_strip_letter_count_multiword():
    assert _strip_letter_count("Some clue (9,4)") == "Some clue"


def test_strip_letter_count_no_count():
    assert _strip_letter_count("Some clue") == "Some clue"


def test_extract_letter_count_from_text():
    assert _extract_letter_count("Some clue (7)", "MYSTERY") == "7"


def test_extract_letter_count_multiword():
    assert _extract_letter_count("Some clue (9,4)", "SOMETHING") == "9,4"


def test_extract_letter_count_falls_back_to_answer_length():
    assert _extract_letter_count("Some clue", "STEM") == "4"


def test_extract_letter_count_no_answer():
    assert _extract_letter_count("Some clue", "") == ""


def test_pick_best_clue_uses_scores():
    clues = [
        {"answer": "RARE"},    # zipf 3.0
        {"answer": "THE"},     # zipf 7.0  ← should win
        {"answer": "BIZARRE"}, # zipf 2.0
    ]
    scores = {"rare": 3.0, "the": 7.0, "bizarre": 2.0}
    idx, clue = _pick_best_clue(clues, scores)
    assert idx == 1
    assert clue["answer"] == "THE"


def test_pick_best_clue_skips_empty_answers():
    clues = [
        {"answer": ""},
        {"answer": "WORD"},
    ]
    scores = {"word": 4.0}
    idx, clue = _pick_best_clue(clues, scores)
    assert clue["answer"] == "WORD"


def test_pick_best_clue_falls_back_without_scores():
    clues = [{"answer": "FIRST"}, {"answer": "SECOND"}]
    idx, clue = _pick_best_clue(clues, {})
    # No scores → falls back to first clue with an answer
    assert clue["answer"] == "FIRST"


def test_pick_best_clue_single_clue():
    clues = [{"answer": "ONLY"}]
    idx, clue = _pick_best_clue(clues, {"only": 3.5})
    assert idx == 0
    assert clue["answer"] == "ONLY"
