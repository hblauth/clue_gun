-- Migration 002: social posting queue
-- Tracks every post scheduled or published by the social bot.
-- Status lifecycle: scheduled → dispatched → published | failed | cancelled

CREATE TYPE social_post_status AS ENUM (
    'scheduled',
    'dispatched',
    'published',
    'failed',
    'cancelled'
);

CREATE TYPE social_post_type AS ENUM (
    'clue_tweet',
    'reveal_tweet',
    'image_card_tweet',
    'tiktok_video'
);

CREATE TABLE IF NOT EXISTS social_posts (
    id               SERIAL PRIMARY KEY,
    post_type        social_post_type   NOT NULL,
    platform         TEXT               NOT NULL,           -- 'twitter', 'instagram', etc.
    status           social_post_status NOT NULL DEFAULT 'scheduled',

    -- Content source
    puzzle_number    INTEGER REFERENCES crosswords_raw(puzzle_number),
    clue_ref         TEXT,                                  -- e.g. "across_14"

    -- Threading: reveal_tweet references its parent clue_tweet
    parent_post_id   INTEGER REFERENCES social_posts(id),

    -- Scheduling
    scheduled_for    TIMESTAMPTZ        NOT NULL,
    next_attempt_at  TIMESTAMPTZ        NOT NULL DEFAULT NOW(),
    attempt_count    INTEGER            NOT NULL DEFAULT 0,
    max_attempts     INTEGER            NOT NULL DEFAULT 3,
    last_error       TEXT,

    -- Idempotency key prevents double-posting if worker restarts or Redis replays a message.
    -- Format: "{post_type}:{puzzle_number}:{date}" e.g. "clue_tweet:28461:2026-03-31"
    idempotency_key  TEXT               NOT NULL UNIQUE,

    -- Result from platform API
    platform_post_id TEXT,
    platform_url     TEXT,

    -- Audit timestamps
    created_at       TIMESTAMPTZ        NOT NULL DEFAULT NOW(),
    dispatched_at    TIMESTAMPTZ,
    published_at     TIMESTAMPTZ
);

-- Fast lookup of ready-to-dispatch posts
CREATE INDEX idx_social_posts_status_scheduled
    ON social_posts (status, scheduled_for)
    WHERE status = 'scheduled';

-- Fast lookup for retry backoff
CREATE INDEX idx_social_posts_next_attempt
    ON social_posts (next_attempt_at)
    WHERE status IN ('scheduled', 'dispatched');
