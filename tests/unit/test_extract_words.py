import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from services.clue_indexer.extract_words import tokenise, extract_answer_words, extract_clue_words


def test_tokenise_basic():
    assert tokenise("hello world") == ["hello", "world"]


def test_tokenise_strips_numbers_and_punctuation():
    assert tokenise("it's a 4-letter word!") == ["it", "s", "a", "letter", "word"]


def test_tokenise_lowercases():
    assert tokenise("MYSTERY") == ["mystery"]


def test_tokenise_empty():
    assert tokenise("") == []


def test_extract_answer_words_single():
    clues = [{"answer": "MYSTERY"}]
    assert extract_answer_words(clues) == {"mystery"}


def test_extract_answer_words_multiword():
    clues = [{"answer": "FIRST STAGE"}]
    assert extract_answer_words(clues) == {"first", "stage"}


def test_extract_answer_words_skips_empty():
    clues = [{"answer": ""}, {"answer": "WORD"}]
    assert extract_answer_words(clues) == {"word"}


def test_extract_answer_words_deduplicates():
    clues = [{"answer": "STEM"}, {"answer": "STEM"}]
    assert extract_answer_words(clues) == {"stem"}


def test_extract_clue_words_basic():
    clues = [{"text": "Stop big name in NYC sport (4)"}]
    words = extract_clue_words(clues)
    assert "stop" in words
    assert "sport" in words
    # Numbers stripped
    assert "4" not in words


def test_extract_clue_words_skips_missing_text():
    clues = [{"text": ""}, {"text": "Simple clue"}]
    words = extract_clue_words(clues)
    assert "simple" in words
    assert "clue" in words
