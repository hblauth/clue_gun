"""
RevealTweetGenerator: posts the answer to yesterday's clue challenge as a reply.

The reveal is posted as a reply to the original clue_tweet so it forms a
thread. We look up the parent post's platform_post_id from social_posts to
get the tweet ID to reply to.

Tweet format:
    Times Cryptic #28461 answer: MYSTERY 🔓

    See the full blog post: https://timesforthetimes.co.uk/...
"""

import json

from shared.clients.postgres import transaction
from ..models import PostRecord, RenderedContent


class RevealTweetGenerator:
    def generate(self, post: PostRecord) -> RenderedContent:
        answer, puzzle_url = self._fetch_answer(post)
        parent_tweet_id = self._fetch_parent_tweet_id(post)

        text = f"Times Cryptic #{post.puzzle_number} answer: {answer} 🔓"
        if puzzle_url:
            text += f"\n\nSee the full blog post: {puzzle_url}"

        return RenderedContent(
            text=text,
            metadata={"in_reply_to_tweet_id": parent_tweet_id},
        )

    def _fetch_answer(self, post: PostRecord) -> tuple[str, str]:
        direction, raw_idx = post.clue_ref.split("_", 1)
        idx = int(raw_idx)

        with transaction() as cur:
            cur.execute(
                "SELECT across, down, url FROM crosswords_raw WHERE puzzle_number = %s",
                (post.puzzle_number,),
            )
            row = cur.fetchone()

        if row is None:
            raise ValueError(f"Puzzle {post.puzzle_number} not found in DB")

        clues = row[direction]
        if isinstance(clues, str):
            clues = json.loads(clues)

        answer = clues[idx].get("answer", "?") if idx < len(clues) else "?"
        return answer, row.get("url", "")

    def _fetch_parent_tweet_id(self, post: PostRecord) -> str | None:
        if post.parent_post_id is None:
            return None
        with transaction() as cur:
            cur.execute(
                "SELECT platform_post_id FROM social_posts WHERE id = %s",
                (post.parent_post_id,),
            )
            row = cur.fetchone()
        return row["platform_post_id"] if row else None
