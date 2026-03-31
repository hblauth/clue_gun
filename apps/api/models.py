"""Pydantic response models for the crossword API."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class Clue(BaseModel):
    number: int
    text: str = ""
    letter_count: str = ""
    answer: str = ""
    explanation: str = ""


class Puzzle(BaseModel):
    id: int
    puzzle_number: int
    puzzle_date: date | None
    blogger: str | None
    url: str
    across: list[Clue]
    down: list[Clue]
    scraped_at: datetime | None
    loaded_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Puzzle":
        return cls(
            id=row["id"],
            puzzle_number=row["puzzle_number"],
            puzzle_date=row["puzzle_date"],
            blogger=row["blogger"],
            url=row["url"],
            across=[Clue(**c) for c in (row["across"] or [])],
            down=[Clue(**c) for c in (row["down"] or [])],
            scraped_at=row["scraped_at"],
            loaded_at=row["loaded_at"],
        )


class PuzzleSummary(BaseModel):
    """Lightweight puzzle listing — omits clue arrays."""

    puzzle_number: int
    puzzle_date: date | None
    blogger: str | None
    url: str
    across_count: int
    down_count: int

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PuzzleSummary":
        return cls(
            puzzle_number=row["puzzle_number"],
            puzzle_date=row["puzzle_date"],
            blogger=row["blogger"],
            url=row["url"],
            across_count=row["across_count"],
            down_count=row["down_count"],
        )


class SocialPost(BaseModel):
    id: int
    post_type: str
    platform: str
    status: str
    puzzle_number: int | None
    clue_ref: str | None
    parent_post_id: int | None
    scheduled_for: datetime
    attempt_count: int
    platform_post_id: str | None
    platform_url: str | None
    published_at: datetime | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "SocialPost":
        return cls(**row)
