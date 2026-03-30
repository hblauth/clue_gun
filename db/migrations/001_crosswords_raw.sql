-- Migration 001: crosswords_raw table
-- Stores raw scraped crossword data as JSON alongside parsed metadata.

CREATE TABLE IF NOT EXISTS crosswords_raw (
    id              SERIAL PRIMARY KEY,
    puzzle_number   INTEGER NOT NULL UNIQUE,
    puzzle_date     DATE,
    blogger         TEXT,
    url             TEXT NOT NULL,
    across          JSONB NOT NULL DEFAULT '[]',
    down            JSONB NOT NULL DEFAULT '[]',
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crosswords_raw_puzzle_number ON crosswords_raw (puzzle_number);
CREATE INDEX IF NOT EXISTS idx_crosswords_raw_puzzle_date ON crosswords_raw (puzzle_date);
