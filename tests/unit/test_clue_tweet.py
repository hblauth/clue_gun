import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from services.social_bot.generators.clue_tweet import ClueTweetGenerator, MAX_TWEET_LENGTH


def _make_generator():
    return ClueTweetGenerator()


def test_format_basic():
    gen = _make_generator()
    text = gen._format(29067, {"text": "Some clue (7)", "answer": "MYSTERY"})
    assert "Times Cryptic #29067" in text
    assert "Some clue" in text
    assert "(7)" in text
    assert "Can you solve it?" in text


def test_format_within_length():
    gen = _make_generator()
    text = gen._format(28461, {"text": "Short clue (4)", "answer": "STEM"})
    assert len(text) <= MAX_TWEET_LENGTH


def test_format_truncates_long_clue():
    gen = _make_generator()
    long_clue = "A" * 300 + " (4)"
    text = gen._format(28461, {"text": long_clue, "answer": "STEM"})
    assert len(text) <= MAX_TWEET_LENGTH
    assert "…" in text


def test_format_no_letter_count():
    gen = _make_generator()
    text = gen._format(29067, {"text": "Clue with no count", "answer": "WORD"})
    assert "Times Cryptic #29067" in text
    assert "Can you solve it?" in text


def test_format_multiword_letter_count():
    gen = _make_generator()
    text = gen._format(28000, {"text": "Some long clue (9,4)", "answer": "SOMETHING ELSE"})
    assert "(9,4)" in text


def test_fetch_clue_calls_db():
    gen = _make_generator()
    mock_row = {
        "across": [{"text": "Test clue (5)", "answer": "WORDS", "number": 1}],
        "down": [],
    }
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = mock_row

    post = MagicMock()
    post.clue_ref = "across_0"
    post.puzzle_number = 29000

    with patch("services.social_bot.generators.clue_tweet.transaction") as mock_tx:
        mock_tx.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_tx.return_value.__exit__ = MagicMock(return_value=False)
        clue = gen._fetch_clue(post)

    assert clue["answer"] == "WORDS"
