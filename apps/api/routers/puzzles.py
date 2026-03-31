from datetime import date

from fastapi import APIRouter, HTTPException, Query

from shared.clients.postgres import transaction
from apps.api.models import Puzzle, PuzzleSummary

router = APIRouter(prefix="/puzzles", tags=["puzzles"])


@router.get("", response_model=list[PuzzleSummary])
def list_puzzles(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List puzzles ordered by puzzle number descending."""
    with transaction() as cur:
        cur.execute(
            """
            SELECT
                puzzle_number, puzzle_date, blogger, url,
                jsonb_array_length(across) AS across_count,
                jsonb_array_length(down)   AS down_count
            FROM crosswords_raw
            ORDER BY puzzle_number DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        rows = cur.fetchall()
    return [PuzzleSummary.from_row(dict(r)) for r in rows]


@router.get("/date/{puzzle_date}", response_model=Puzzle)
def get_puzzle_by_date(puzzle_date: date):
    """Get puzzle by publication date."""
    with transaction() as cur:
        cur.execute(
            "SELECT * FROM crosswords_raw WHERE puzzle_date = %s LIMIT 1",
            (puzzle_date,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No puzzle found for that date")
    return Puzzle.from_row(dict(row))


@router.get("/{puzzle_number}", response_model=Puzzle)
def get_puzzle(puzzle_number: int):
    """Get a single puzzle by number."""
    with transaction() as cur:
        cur.execute(
            "SELECT * FROM crosswords_raw WHERE puzzle_number = %s",
            (puzzle_number,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Puzzle not found")
    return Puzzle.from_row(dict(row))
