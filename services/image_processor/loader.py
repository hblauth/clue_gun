"""
Write GridResult cells to the puzzle_annotations table.

Usage:
    from services.image_processor.loader import save_result
    save_result(result)
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from services.image_processor.pipeline import GridResult
from shared.clients.postgres import transaction

logger = logging.getLogger(__name__)


def save_result(result: GridResult) -> int:
    """
    Upsert all cells from a GridResult into puzzle_annotations.
    Returns the number of rows written.
    """
    if result.puzzle_number is None:
        raise ValueError("puzzle_number required to save result")

    rows = [
        (
            result.puzzle_number,
            cell.row,
            cell.col,
            cell.clue_number,
            cell.letter,
            cell.annotation,
            cell.confidence,
            result.image_path,
        )
        for cell in result.cells
    ]

    if not rows:
        return 0

    with transaction() as cur:
        from psycopg2.extras import execute_values
        execute_values(
            cur,
            """
            INSERT INTO puzzle_annotations
                (puzzle_number, row, col, clue_number, letter, annotation, confidence, image_path)
            VALUES %s
            ON CONFLICT (puzzle_number, row, col) DO UPDATE SET
                clue_number  = EXCLUDED.clue_number,
                letter       = EXCLUDED.letter,
                annotation   = EXCLUDED.annotation,
                confidence   = EXCLUDED.confidence,
                image_path   = EXCLUDED.image_path,
                processed_at = NOW()
            """,
            rows,
        )

    logger.info("Saved %d cells for puzzle #%d", len(rows), result.puzzle_number)
    return len(rows)
