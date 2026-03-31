"""
InstagramPublisher: publishes image posts to Instagram via the Graph API.

Auth env vars:
    INSTAGRAM_ACCESS_TOKEN   — long-lived page access token
    INSTAGRAM_ACCOUNT_ID     — numeric Instagram Business/Creator account ID

Dry-run mode (set INSTAGRAM_DRY_RUN=1):
    Logs the caption and media path instead of calling the API.

Instagram Graph API flow (two-step):
  1. POST /media        — create a media container, returns creation_id
  2. POST /media_publish — publish the container, returns the post ID
"""

import logging
import os

import requests

from ..models import PostRecord, RenderedContent, PublishResult

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com/v19.0"


class InstagramPublisher:
    def __init__(self) -> None:
        self._dry_run = os.getenv("INSTAGRAM_DRY_RUN", "").lower() in ("1", "true", "yes")
        self._access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
        self._account_id = os.getenv("INSTAGRAM_ACCOUNT_ID", "")

    def publish(self, post: PostRecord, content: RenderedContent) -> PublishResult:
        caption = content.text or ""

        if self._dry_run:
            media = content.media_paths[0] if content.media_paths else "<no media>"
            logger.info("[DRY RUN] Would post to Instagram: media=%s caption=%r", media, caption)
            return PublishResult(
                success=True,
                platform_post_id="dry_run_ig_000",
                platform_url="https://www.instagram.com/p/dry_run_ig_000/",
            )

        if not self._access_token or not self._account_id:
            msg = "INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID must be set"
            logger.error(msg)
            return PublishResult(success=False, error=msg)

        if not content.media_paths:
            msg = "Instagram posts require at least one media file"
            logger.error("Post %d: %s", post.id, msg)
            return PublishResult(success=False, error=msg)

        image_url = content.metadata.get("image_url")
        if not image_url:
            msg = "Instagram publisher requires content.metadata['image_url'] (publicly accessible URL)"
            logger.error("Post %d: %s", post.id, msg)
            return PublishResult(success=False, error=msg)

        try:
            container_id = self._create_container(image_url, caption)
            post_id = self._publish_container(container_id)
            return PublishResult(
                success=True,
                platform_post_id=post_id,
                platform_url=f"https://www.instagram.com/p/{post_id}/",
            )
        except requests.HTTPError as exc:
            logger.error("Instagram API error for post %d: %s", post.id, exc)
            return PublishResult(success=False, error=str(exc))

    def _create_container(self, image_url: str, caption: str) -> str:
        """Step 1: create a media container. Returns creation_id."""
        resp = requests.post(
            f"{_GRAPH_BASE}/{self._account_id}/media",
            params={
                "image_url": image_url,
                "caption": caption,
                "access_token": self._access_token,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["id"]

    def _publish_container(self, creation_id: str) -> str:
        """Step 2: publish the container. Returns the published post ID."""
        resp = requests.post(
            f"{_GRAPH_BASE}/{self._account_id}/media_publish",
            params={
                "creation_id": creation_id,
                "access_token": self._access_token,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["id"]
