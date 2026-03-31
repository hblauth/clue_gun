"""
Reusable Redis client.

Connection is configured via environment variables (or a .env file):
    REDIS_HOST     default: localhost
    REDIS_PORT     default: 6379
    REDIS_DB       default: 0
"""

import os

import redis
from dotenv import load_dotenv

load_dotenv()

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    """Return the shared Redis client (lazy singleton)."""
    global _client
    if _client is None:
        _client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            decode_responses=True,
        )
    return _client
