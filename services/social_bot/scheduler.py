"""
Social bot scheduler.

Responsibilities:
  1. Create today's scheduled posts (once per day, idempotent).
  2. Dispatch ready posts: claim scheduled rows and push IDs to Redis.
  3. Recover stale dispatched posts (crash-recovery).

Run continuously:
    python services/social_bot/scheduler.py

Dry-run mode (no DB writes, no Redis pushes):
    SOCIAL_BOT_DRY_RUN=1 python services/social_bot/scheduler.py
"""

import logging
import os
import signal
import sys
import time
from datetime import date, datetime, timedelta, timezone

# Allow running from repo root: python services/social_bot/scheduler.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.clients.postgres import get_conn, transaction
from shared.clients.redis import get_client as get_redis
from services.social_bot import queue as q
from services.social_bot.selector import select_clue_for_date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [scheduler] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("SOCIAL_BOT_POLL_INTERVAL", "60"))  # seconds
POST_HOUR_UTC = int(os.getenv("SOCIAL_BOT_POST_HOUR", "9"))       # UTC hour for daily posts
STALE_DISPATCH_MINUTES = 10
DRY_RUN = os.getenv("SOCIAL_BOT_DRY_RUN", "").lower() in ("1", "true", "yes")

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    logger.info("SIGTERM received, shutting down after current cycle")
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_sigterm)


def run() -> None:
    logger.info("Scheduler started (dry_run=%s, post_hour=%d UTC)", DRY_RUN, POST_HOUR_UTC)
    redis = get_redis()

    while not _shutdown:
        try:
            _create_daily_posts_if_needed()
            dispatched = _dispatch_ready_posts(redis)
            recovered = _recover_stale_dispatched()
            if dispatched or recovered:
                logger.info("Cycle: dispatched=%d recovered=%d", dispatched, recovered)
        except Exception:
            logger.exception("Error in scheduler cycle")

        # Sleep in short increments to respond to SIGTERM quickly
        for _ in range(POLL_INTERVAL):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("Scheduler stopped")


def _create_daily_posts_if_needed() -> None:
    """Create clue_tweet + reveal_tweet for today if they don't already exist."""
    today = date.today()
    tomorrow = today + timedelta(days=1)

    clue_key = f"clue_tweet:{today.isoformat()}"
    reveal_key = f"reveal_tweet:{tomorrow.isoformat()}"

    ig_key = f"image_card_tweet:{today.isoformat()}"

    with transaction() as cur:
        cur.execute(
            "SELECT idempotency_key FROM social_posts WHERE idempotency_key IN (%s, %s, %s)",
            (clue_key, reveal_key, ig_key),
        )
        existing_keys = {row["idempotency_key"] for row in cur.fetchall()}

    # All three already exist
    if len(existing_keys) >= 3:
        return

    clue_data = select_clue_for_date(today)
    if clue_data is None:
        logger.warning("No puzzle available for %s, skipping daily post creation", today)
        return

    puzzle_num = clue_data["puzzle_number"]
    clue_key = f"clue_tweet:{puzzle_num}:{today.isoformat()}"
    reveal_key = f"reveal_tweet:{puzzle_num}:{tomorrow.isoformat()}"
    ig_key = f"image_card_tweet:{puzzle_num}:{today.isoformat()}"

    scheduled_today = datetime(
        today.year, today.month, today.day, POST_HOUR_UTC, 0, 0, tzinfo=timezone.utc
    )
    scheduled_tomorrow = datetime(
        tomorrow.year, tomorrow.month, tomorrow.day, POST_HOUR_UTC, 0, 0, tzinfo=timezone.utc
    )

    if DRY_RUN:
        logger.info("[DRY RUN] Would schedule: clue_tweet puzzle=%d clue=%s at %s",
                    puzzle_num, clue_data["clue_ref"], scheduled_today)
        logger.info("[DRY RUN] Would schedule: image_card_tweet puzzle=%d at %s",
                    puzzle_num, scheduled_today)
        logger.info("[DRY RUN] Would schedule: reveal_tweet puzzle=%d at %s",
                    puzzle_num, scheduled_tomorrow)
        return

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            # clue_tweet (Twitter)
            cur.execute(
                """
                INSERT INTO social_posts
                    (post_type, platform, puzzle_number, clue_ref,
                     scheduled_for, idempotency_key)
                VALUES ('clue_tweet', 'twitter', %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """,
                (puzzle_num, clue_data["clue_ref"], scheduled_today, clue_key),
            )
            row = cur.fetchone()
            clue_post_id = row[0] if row else None
            if clue_post_id is None:
                cur.execute("SELECT id FROM social_posts WHERE idempotency_key = %s", (clue_key,))
                clue_post_id = cur.fetchone()[0]

            # image_card_tweet (Instagram) — same time as clue tweet
            cur.execute(
                """
                INSERT INTO social_posts
                    (post_type, platform, puzzle_number, clue_ref,
                     scheduled_for, idempotency_key)
                VALUES ('image_card_tweet', 'instagram', %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (puzzle_num, clue_data["clue_ref"], scheduled_today, ig_key),
            )

            # reveal_tweet (Twitter) — next day, threaded reply to clue_tweet
            cur.execute(
                """
                INSERT INTO social_posts
                    (post_type, platform, puzzle_number, clue_ref,
                     parent_post_id, scheduled_for, idempotency_key)
                VALUES ('reveal_tweet', 'twitter', %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (puzzle_num, clue_data["clue_ref"], clue_post_id, scheduled_tomorrow, reveal_key),
            )
    finally:
        conn.close()

    logger.info(
        "Scheduled puzzle #%d: clue_tweet+image_card at %s, reveal_tweet at %s",
        puzzle_num, scheduled_today, scheduled_tomorrow,
    )


def _dispatch_ready_posts(redis_client) -> int:
    """Claim scheduled posts and push their IDs to Redis. Returns count dispatched."""
    now = datetime.now(tz=timezone.utc)
    dispatched = 0

    with transaction() as cur:
        cur.execute(
            """
            SELECT id FROM social_posts
            WHERE status = 'scheduled'
              AND scheduled_for <= %s
              AND next_attempt_at <= %s
            ORDER BY scheduled_for
            LIMIT 50
            """,
            (now, now),
        )
        candidates = [row["id"] for row in cur.fetchall()]

    for post_id in candidates:
        conn = get_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE social_posts
                    SET status = 'dispatched', dispatched_at = NOW()
                    WHERE id = %s AND status = 'scheduled'
                    RETURNING id
                    """,
                    (post_id,),
                )
                claimed = cur.fetchone()
        finally:
            conn.close()

        if claimed:
            if not DRY_RUN:
                q.enqueue(redis_client, post_id)
            logger.info("Dispatched post id=%d", post_id)
            dispatched += 1

    return dispatched


def _recover_stale_dispatched() -> int:
    """Reset dispatched posts that haven't been published in STALE_DISPATCH_MINUTES."""
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE social_posts
                SET status = 'scheduled', dispatched_at = NULL
                WHERE status = 'dispatched'
                  AND dispatched_at < NOW() - INTERVAL '%s minutes'
                RETURNING id
                """,
                (STALE_DISPATCH_MINUTES,),
            )
            recovered = cur.fetchall()
    finally:
        conn.close()

    if recovered:
        ids = [r[0] for r in recovered]
        logger.warning("Recovered %d stale dispatched posts: %s", len(ids), ids)
    return len(recovered)


if __name__ == "__main__":
    run()
