"""
TwitterPublisher: publishes posts to Twitter/X via the v2 API (tweepy).

Auth env vars (OAuth 1.0a User Context — required for posting tweets):
    TWITTER_API_KEY
    TWITTER_API_SECRET
    TWITTER_ACCESS_TOKEN
    TWITTER_ACCESS_SECRET

Dry-run mode (set TWITTER_DRY_RUN=1):
    Logs the tweet text instead of calling the API. Returns a fake post ID.
"""

import logging
import os

import tweepy

from ..models import PostRecord, PostType, RenderedContent, PublishResult

logger = logging.getLogger(__name__)


class TwitterPublisher:
    def __init__(self) -> None:
        self._dry_run = os.getenv("TWITTER_DRY_RUN", "").lower() in ("1", "true", "yes")
        self._client: tweepy.Client | None = None
        # v1.1 API is only needed for media uploads (image card, video)
        self._api_v1: tweepy.API | None = None

    def _get_client(self) -> tweepy.Client:
        if self._client is None:
            self._client = tweepy.Client(
                consumer_key=os.environ["TWITTER_API_KEY"],
                consumer_secret=os.environ["TWITTER_API_SECRET"],
                access_token=os.environ["TWITTER_ACCESS_TOKEN"],
                access_token_secret=os.environ["TWITTER_ACCESS_SECRET"],
            )
        return self._client

    def _get_api_v1(self) -> tweepy.API:
        """v1.1 API for media uploads — only initialised when needed."""
        if self._api_v1 is None:
            auth = tweepy.OAuth1UserHandler(
                os.environ["TWITTER_API_KEY"],
                os.environ["TWITTER_API_SECRET"],
                os.environ["TWITTER_ACCESS_TOKEN"],
                os.environ["TWITTER_ACCESS_SECRET"],
            )
            self._api_v1 = tweepy.API(auth)
        return self._api_v1

    def publish(self, post: PostRecord, content: RenderedContent) -> PublishResult:
        text = content.text or ""

        if self._dry_run:
            logger.info("[DRY RUN] Would tweet: %s", text)
            return PublishResult(
                success=True,
                platform_post_id="dry_run_000",
                platform_url="https://x.com/i/web/status/dry_run_000",
            )

        try:
            media_ids = self._upload_media(content)
            reply_params = self._reply_params(post, content)

            kwargs: dict = {"text": text}
            if media_ids:
                kwargs["media_ids"] = media_ids
            if reply_params:
                kwargs["reply"] = reply_params

            response = self._get_client().create_tweet(**kwargs)
            tweet_id = str(response.data["id"])
            return PublishResult(
                success=True,
                platform_post_id=tweet_id,
                platform_url=f"https://x.com/i/web/status/{tweet_id}",
            )
        except tweepy.TweepyException as exc:
            logger.error("Twitter API error for post %d: %s", post.id, exc)
            return PublishResult(success=False, error=str(exc))

    def _upload_media(self, content: RenderedContent) -> list[str]:
        if not content.media_paths:
            return []
        api = self._get_api_v1()
        media_ids = []
        for path in content.media_paths:
            media = api.media_upload(str(path))
            media_ids.append(str(media.media_id))
        return media_ids

    def _reply_params(self, post: PostRecord, content: RenderedContent) -> dict | None:
        # RevealTweetGenerator stores the parent tweet ID in metadata
        parent_tweet_id = content.metadata.get("in_reply_to_tweet_id")
        if parent_tweet_id:
            return {"in_reply_to_tweet_id": parent_tweet_id}
        return None
