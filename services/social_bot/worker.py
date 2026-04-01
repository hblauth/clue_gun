"""
Social bot worker.

Drains the Redis dispatch queue, calls the appropriate content generator
and platform publisher for each post, then writes the result back to DB.

Run continuously:
    python services/social_bot/worker.py

Dry-run mode (generators run, but publisher logs instead of calling API):
    TWITTER_DRY_RUN=1 python services/social_bot/worker.py
"""

import logging
import os
import signal
import sys

# Allow running from repo root: python services/social_bot/worker.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.clients.postgres import get_conn, transaction
from shared.clients.redis import get_client as get_redis
from services.social_bot import queue as q
from services.social_bot.generators import register as register_generator, get_generator
from services.social_bot.generators.clue_tweet import ClueTweetGenerator
from services.social_bot.generators.reveal_tweet import RevealTweetGenerator
from services.social_bot.generators.image_card import ImageCardGenerator
from services.social_bot.publishers import register as register_publisher, get_publisher
from services.social_bot.publishers.twitter_web import TwitterWebPublisher
from services.social_bot.publishers.instagram import InstagramPublisher
from services.social_bot.models import PostRecord, PostStatus, PostType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

BLPOP_TIMEOUT = 30  # seconds — controls shutdown responsiveness

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    logger.info("SIGTERM received, will stop after current job")
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_sigterm)


def _setup_registries() -> None:
    register_generator(PostType.CLUE_TWEET, ClueTweetGenerator())
    register_generator(PostType.REVEAL_TWEET, RevealTweetGenerator())
    register_generator(PostType.IMAGE_CARD_TWEET, ImageCardGenerator())
    register_publisher("twitter", TwitterWebPublisher())
    register_publisher("instagram", InstagramPublisher())


def _fetch_post(post_id: int) -> PostRecord | None:
    with transaction() as cur:
        cur.execute(
            """
            SELECT id, post_type, platform, status, puzzle_number, clue_ref,
                   scheduled_for, parent_post_id, attempt_count, max_attempts,
                   idempotency_key, platform_post_id, last_error
            FROM social_posts
            WHERE id = %s
            """,
            (post_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return PostRecord(
        id=row["id"],
        post_type=PostType(row["post_type"]),
        platform=row["platform"],
        status=PostStatus(row["status"]),
        puzzle_number=row["puzzle_number"],
        clue_ref=row["clue_ref"],
        scheduled_for=row["scheduled_for"],
        parent_post_id=row["parent_post_id"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        idempotency_key=row["idempotency_key"],
        platform_post_id=row["platform_post_id"],
        last_error=row["last_error"],
    )


def _mark_published(post_id: int, platform_post_id: str, platform_url: str) -> None:
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE social_posts
                SET status = 'published',
                    platform_post_id = %s,
                    platform_url = %s,
                    published_at = NOW()
                WHERE id = %s
                """,
                (platform_post_id, platform_url, post_id),
            )
    finally:
        conn.close()


def _mark_failed_or_retry(post: PostRecord, error: str) -> None:
    new_attempt = post.attempt_count + 1
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            if new_attempt >= post.max_attempts:
                cur.execute(
                    """
                    UPDATE social_posts
                    SET status = 'failed',
                        attempt_count = %s,
                        last_error = %s
                    WHERE id = %s
                    """,
                    (new_attempt, error, post.id),
                )
                logger.error(
                    "Post id=%d permanently failed after %d attempts: %s",
                    post.id, new_attempt, error,
                )
            else:
                # Exponential backoff: 10min, 20min, 40min
                backoff_minutes = 10 * (2 ** (new_attempt - 1))
                cur.execute(
                    """
                    UPDATE social_posts
                    SET status = 'scheduled',
                        attempt_count = %s,
                        next_attempt_at = NOW() + (%s || ' minutes')::interval,
                        last_error = %s
                    WHERE id = %s
                    """,
                    (new_attempt, str(backoff_minutes), error, post.id),
                )
                logger.warning(
                    "Post id=%d attempt %d/%d failed, retry in %dm: %s",
                    post.id, new_attempt, post.max_attempts, backoff_minutes, error,
                )
    finally:
        conn.close()


def _process(post_id: int) -> None:
    post = _fetch_post(post_id)
    if post is None:
        logger.warning("Post id=%d not found in DB, skipping", post_id)
        return

    if post.status != PostStatus.DISPATCHED:
        logger.info(
            "Post id=%d has status=%s (expected dispatched), skipping stale message",
            post_id, post.status,
        )
        return

    logger.info(
        "Processing post id=%d type=%s platform=%s puzzle=%s",
        post.id, post.post_type, post.platform, post.puzzle_number,
    )

    try:
        generator = get_generator(post.post_type)
        content = generator.generate(post)
    except Exception as exc:
        _mark_failed_or_retry(post, f"generator error: {exc}")
        return

    try:
        publisher = get_publisher(post.platform)
        result = publisher.publish(post, content)
    except Exception as exc:
        _mark_failed_or_retry(post, f"publisher error: {exc}")
        return

    if result.success:
        _mark_published(post.id, result.platform_post_id or "", result.platform_url or "")
        logger.info(
            "Published post id=%d → %s",
            post.id, result.platform_url or result.platform_post_id,
        )
    else:
        _mark_failed_or_retry(post, result.error or "unknown error")


def run() -> None:
    _setup_registries()
    redis = get_redis()
    logger.info("Worker started")

    while not _shutdown:
        try:
            post_id = q.dequeue(redis, timeout=BLPOP_TIMEOUT)
            if post_id is None:
                continue  # timeout — loop to check _shutdown
            _process(post_id)
        except Exception:
            logger.exception("Unhandled error in worker loop")

    logger.info("Worker stopped")


if __name__ == "__main__":
    run()
