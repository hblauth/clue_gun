-- Migration 004: puzzle annotations
-- Stores per-cell data extracted from solver photos:
--   letter  — the handwritten letter in the cell (NULL = blank/black square)
--   annotation — symbol drawn on the clue number (circle, square, star, etc.)

CREATE TYPE annotation_type AS ENUM (
    'circle',
    'square',
    'star',
    'strikethrough',
    'cross'
);

CREATE TABLE IF NOT EXISTS puzzle_annotations (
    id             SERIAL PRIMARY KEY,
    puzzle_number  INTEGER NOT NULL REFERENCES crosswords_raw(puzzle_number),
    row            INTEGER NOT NULL,
    col            INTEGER NOT NULL,
    clue_number    INTEGER,                      -- printed clue number in cell (NULL if none)
    letter         CHAR(1),                     -- handwritten letter (NULL = blank/black)
    annotation     annotation_type,             -- symbol on clue number (NULL = none)
    confidence     REAL,                        -- model confidence [0,1]
    image_path     TEXT,                        -- source photo path/filename
    processed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (puzzle_number, row, col)
);

CREATE INDEX idx_puzzle_annotations_puzzle
    ON puzzle_annotations (puzzle_number);
