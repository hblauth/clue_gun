"""
Redis-backed dispatch queue for the social bot.

The queue holds post IDs (integers as strings). The scheduler pushes IDs;
the worker pops them. Redis provides low-latency dispatch; PostgreSQL holds
the authoritative post state.
"""

import redis

QUEUE_KEY = "social_bot:dispatch"


def enqueue(client: redis.Redis, post_id: int) -> None:
    """Push a post ID onto the right end of the dispatch queue."""
    client.rpush(QUEUE_KEY, post_id)


def dequeue(client: redis.Redis, timeout: int = 30) -> int | None:
    """
    Block until a post ID is available, then return it.

    Returns None after `timeout` seconds with no message (allows the
    caller to loop and check for shutdown signals).
    """
    result = client.blpop(QUEUE_KEY, timeout=timeout)
    if result is None:
        return None
    _key, value = result
    return int(value)


def queue_length(client: redis.Redis) -> int:
    return client.llen(QUEUE_KEY)
