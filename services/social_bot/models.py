"""
Core data models for the social bot.

PostRecord mirrors a social_posts DB row (immutable once fetched).
RenderedContent is the output of a ContentGenerator (platform-agnostic).
PublishResult is the output of a Publisher.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class PostStatus(str, Enum):
    SCHEDULED = "scheduled"
    DISPATCHED = "dispatched"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PostType(str, Enum):
    CLUE_TWEET = "clue_tweet"
    REVEAL_TWEET = "reveal_tweet"
    IMAGE_CARD_TWEET = "image_card_tweet"
    TIKTOK_VIDEO = "tiktok_video"


@dataclass(frozen=True)
class PostRecord:
    """Immutable mirror of a social_posts row. Worker must not mutate this."""

    id: int
    post_type: PostType
    platform: str
    status: PostStatus
    puzzle_number: int | None
    clue_ref: str | None
    scheduled_for: datetime
    parent_post_id: int | None
    attempt_count: int
    max_attempts: int
    idempotency_key: str
    platform_post_id: str | None = None
    last_error: str | None = None


@dataclass
class RenderedContent:
    """Output of a ContentGenerator. Platform-agnostic.

    media_paths: local file paths for images/videos to upload. Empty for text-only posts.
    metadata: arbitrary extra data a publisher might need (alt-text, aspect ratio, etc.).
    """

    text: str | None = None
    media_paths: list[Path] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class PublishResult:
    """Output of a Publisher.publish() call."""

    success: bool
    platform_post_id: str | None = None
    platform_url: str | None = None
    error: str | None = None
