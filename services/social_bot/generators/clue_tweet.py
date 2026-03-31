"""
ClueTweetGenerator: formats a daily cryptic clue challenge tweet.

Tweet format:
    Times Cryptic #28461 — Some clue text (7). Can you solve it? 🔐

The post record's clue_ref ("across_14") and puzzle_number tell us which
clue to render. We re-fetch from DB rather than storing the text in social_posts
so there's a single source of truth for clue data.
"""

import json

from shared.clients.postgres import transaction
from ..models import PostRecord, RenderedContent
from ..selector import _extract_letter_count, _strip_letter_count

MAX_TWEET_LENGTH = 280


class ClueTweetGenerator:
    def generate(self, post: PostRecord) -> RenderedContent:
        clue = self._fetch_clue(post)
        text = self._format(post.puzzle_number, clue)
        return RenderedContent(text=text)

    def _fetch_clue(self, post: PostRecord) -> dict:
        direction, raw_idx = post.clue_ref.split("_", 1)
        idx = int(raw_idx)

        with transaction() as cur:
            cur.execute(
                "SELECT across, down FROM crosswords_raw WHERE puzzle_number = %s",
                (post.puzzle_number,),
            )
            row = cur.fetchone()

        if row is None:
            raise ValueError(f"Puzzle {post.puzzle_number} not found in DB")

        clues = row[direction]
        if isinstance(clues, str):
            clues = json.loads(clues)

        if idx >= len(clues):
            raise ValueError(
                f"clue_ref {post.clue_ref!r} out of range "
                f"(puzzle {post.puzzle_number} has {len(clues)} {direction} clues)"
            )
        return clues[idx]

    def _format(self, puzzle_number: int, clue: dict) -> str:
        raw_text = clue.get("text", "")
        clue_text = _strip_letter_count(raw_text)
        answer = clue.get("answer", "")
        letter_count = _extract_letter_count(raw_text, answer)

        count_str = f" ({letter_count})" if letter_count else ""
        tweet = f"Times Cryptic #{puzzle_number} — {clue_text}{count_str}. Can you solve it? 🔐"

        if len(tweet) > MAX_TWEET_LENGTH:
            # Truncate clue text to fit, preserving the suffix
            suffix = f"{count_str}. Can you solve it? 🔐"
            prefix = f"Times Cryptic #{puzzle_number} — "
            max_clue = MAX_TWEET_LENGTH - len(prefix) - len(suffix) - 1
            tweet = prefix + clue_text[:max_clue] + "…" + suffix

        return tweet
