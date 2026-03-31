-- Word frequency table populated by services/clue_indexer/load_wordfreq.py.
-- Stores wordfreq Zipf scores for all words seen in scraped crossword answers
-- and clue text.  Used by social_bot/selector.py to rank clue interestingness.

CREATE TABLE IF NOT EXISTS word_frequency (
    word        TEXT        PRIMARY KEY,
    zipf_score  REAL        NOT NULL,
    frequency   DOUBLE PRECISION NOT NULL,
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE word_frequency IS
    'Zipf-scale word frequency scores derived from crossword answer/clue corpus.';
COMMENT ON COLUMN word_frequency.zipf_score IS
    'wordfreq Zipf score (0–7). Higher = more common. 0 = not in corpus.';
COMMENT ON COLUMN word_frequency.frequency IS
    'Raw wordfreq frequency (fraction of words in corpus).';
