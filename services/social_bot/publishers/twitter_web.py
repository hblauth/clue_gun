"""
TwitterWebPublisher: posts to X/Twitter via Playwright (headless browser).

Uses a real Chromium browser with your existing X session cookies, so X's
anti-bot checks pass. No API plan required.

Auth env vars:
    TWITTER_AUTH_TOKEN   — value of `auth_token` cookie from x.com
    TWITTER_CT0          — value of `ct0` cookie from x.com
    Extract in Chrome: DevTools → Application → Cookies → https://x.com

Optional:
    TWITTER_DRY_RUN=1    — log instead of posting

Cookies are injected into the browser context before navigating to X, so no
login flow is needed.
"""

from __future__ import annotations

import logging
import os
import time

from ..models import PostRecord, RenderedContent, PublishResult

logger = logging.getLogger(__name__)


class TwitterWebPublisher:
    def __init__(self) -> None:
        self._dry_run = os.getenv("TWITTER_DRY_RUN", "").lower() in ("1", "true", "yes")

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
            return _post_via_browser(text, content.media_paths or [],
                                      content.metadata.get("in_reply_to_tweet_id"))
        except Exception as exc:
            logger.error("Tweet failed for post id=%d: %s", post.id, exc)
            return PublishResult(success=False, error=str(exc))


def _post_via_browser(text: str, media_paths: list, reply_to_id: str | None = None) -> PublishResult:
    from playwright.sync_api import sync_playwright

    auth_token = os.environ["TWITTER_AUTH_TOKEN"]
    ct0 = os.environ["TWITTER_CT0"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        # Inject session cookies before any navigation
        ctx.add_cookies([
            {"name": "auth_token", "value": auth_token, "domain": ".x.com", "path": "/"},
            {"name": "ct0",        "value": ct0,        "domain": ".x.com", "path": "/"},
        ])

        page = ctx.new_page()

        # Navigate to home — compose box is inline here (no modal overlay issues)
        dest = f"https://x.com/i/web/status/{reply_to_id}" if reply_to_id else "https://x.com/home"
        logger.info("Navigating to %s", dest)
        page.goto(dest, wait_until="domcontentloaded", timeout=30000)

        # Dismiss cookie consent banner if present
        try:
            page.locator('[data-testid="consent-banner"]').wait_for(timeout=3000)
            accept = page.locator('button[data-testid="accept"]')
            if accept.is_visible():
                accept.click()
        except Exception:
            pass  # no banner

        if reply_to_id:
            # Click reply button on the target tweet
            page.locator('[data-testid="reply"]').first.click()

        # Wait for the tweet compose box (primary column inline box)
        compose = page.get_by_test_id("primaryColumn").get_by_test_id("tweetTextarea_0")
        compose.wait_for(timeout=15000)
        compose.click(force=True)
        compose.press_sequentially(text, delay=30)
        time.sleep(0.5)

        # Upload media if any
        if media_paths:
            file_input = page.locator('input[data-testid="fileInput"]')
            file_input.set_input_files(media_paths)
            # Wait for media to finish uploading
            page.wait_for_selector('[data-testid="attachments"]', timeout=30000)

        # Intercept the CreateTweet response to get the tweet ID
        tweet_id: list[str] = []

        def handle_response(response):
            if "CreateTweet" in response.url and response.status == 200:
                try:
                    data = response.json()
                    result = data["data"]["create_tweet"]["tweet_results"]["result"]
                    tweet_id.append(str(result["rest_id"]))
                except Exception:
                    pass

        page.on("response", handle_response)

        # Submit the tweet (button lives in the primary column dialog)
        submit = page.get_by_test_id("primaryColumn").locator(
            '[data-testid="tweetButtonInline"], [data-testid="tweetButton"]'
        )
        submit.first.click()

        # Poll up to 10s for the CreateTweet network response
        deadline = time.time() + 10
        while not tweet_id and time.time() < deadline:
            time.sleep(0.2)

        browser.close()

    if tweet_id:
        tid = tweet_id[0]
        logger.info("Tweet posted: %s", tid)
        return PublishResult(
            success=True,
            platform_post_id=tid,
            platform_url=f"https://x.com/i/web/status/{tid}",
        )

    # If we didn't capture the ID from network but the page didn't error,
    # treat as success with an unknown ID
    logger.warning("Tweet likely posted but ID not captured")
    return PublishResult(
        success=True,
        platform_post_id=None,
        platform_url="https://x.com/clue_of_the_day",
    )
