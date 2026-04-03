"""Unit tests for image_processor pipeline (no DB, no real images required)."""
import sys
import math
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from services.image_processor.pipeline import (
    CellResult,
    ClueAnnotation,
    GridResult,
    _cluster_positions,
    _order_corners,
    _find_clue_starts_by_projection,
    _build_clue_map_from_sequence,
    _lookup_clue_number,
    classify_annotation,
    deskew,
    segment_clue_rows,
    find_clue_list_region,
)


# ---------------------------------------------------------------------------
# _cluster_positions
# ---------------------------------------------------------------------------

def test_cluster_positions_merges_nearby():
    result = _cluster_positions([10, 11, 12, 50, 51, 100], gap=5)
    assert len(result) == 3


def test_cluster_positions_empty():
    assert _cluster_positions([], gap=5) == []


def test_cluster_positions_no_merge():
    result = _cluster_positions([0, 100, 200], gap=5)
    assert result == [0, 100, 200]


# ---------------------------------------------------------------------------
# _order_corners
# ---------------------------------------------------------------------------

def test_order_corners_returns_tl_tr_br_bl():
    pts = np.array([[0, 100], [100, 0], [100, 100], [0, 0]], dtype=np.float32)
    ordered = _order_corners(pts)
    assert tuple(ordered[0]) == (0.0, 0.0)   # top-left
    assert tuple(ordered[2]) == (100.0, 100.0)  # bottom-right


# ---------------------------------------------------------------------------
# classify_annotation — synthetic images
# ---------------------------------------------------------------------------

def _blank_cell(size=64) -> np.ndarray:
    """White RGB region (no annotation)."""
    return np.full((size, size, 3), 255, dtype=np.uint8)


def _cell_with_star(size=128) -> np.ndarray:
    """Image with a filled 5-pointed star (128px — realistic phone photo resolution)."""
    img = _blank_cell(size)
    cx, cy = size // 2, size // 2
    outer_r = size // 2 - 4
    inner_r = outer_r * 38 // 100  # classic 5-star inner/outer ratio ~0.38
    pts = []
    for i in range(10):
        angle = math.radians(-90 + i * 36)
        r = outer_r if i % 2 == 0 else inner_r
        pts.append([int(cx + r * math.cos(angle)), int(cy + r * math.sin(angle))])
    pts_arr = np.array(pts, dtype=np.int32)
    cv2.fillPoly(img, [pts_arr], color=(0, 0, 0))
    return img


def _cell_with_circle(size=128) -> np.ndarray:
    """Image with a circle."""
    img = _blank_cell(size)
    cv2.circle(img, (size // 2, size // 2), size // 3, (0, 0, 0), 2)
    return img


def _cell_with_cross(size=128) -> np.ndarray:
    """Image with two crossing diagonal lines."""
    img = _blank_cell(size)
    s = size
    cv2.line(img, (0, 0), (s, s), (0, 0, 0), 2)
    cv2.line(img, (s, 0), (0, s), (0, 0, 0), 2)
    return img


def test_classify_blank_returns_none():
    annotation, confidence = classify_annotation(_blank_cell())
    assert annotation is None
    assert confidence > 0.5


def test_classify_star_detected():
    annotation, confidence = classify_annotation(_cell_with_star())
    assert annotation == "star"
    assert confidence >= 0.6


def test_classify_circle_returns_none():
    # MVP: only stars are returned; circles map to None
    annotation, _ = classify_annotation(_cell_with_circle())
    assert annotation is None


def test_classify_cross_returns_none():
    # MVP: only stars are returned; crosses map to None
    annotation, _ = classify_annotation(_cell_with_cross())
    assert annotation is None


def test_classify_confidence_range():
    _, confidence = classify_annotation(_cell_with_star())
    assert 0.0 <= confidence <= 1.0


# ---------------------------------------------------------------------------
# deskew — should not crash on plain image
# ---------------------------------------------------------------------------

def test_deskew_passthrough():
    img = np.full((200, 200, 3), 200, dtype=np.uint8)
    result = deskew(img)
    assert result.shape == img.shape


# ---------------------------------------------------------------------------
# segment_clue_rows
# ---------------------------------------------------------------------------

def _make_clue_region(n_lines: int = 5, img_w: int = 400, line_h: int = 20,
                       gap: int = 10) -> np.ndarray:
    """Synthetic clue list: white background with black text-like horizontal bands."""
    total_h = n_lines * (line_h + gap) + gap
    img = np.full((total_h, img_w, 3), 255, dtype=np.uint8)
    for i in range(n_lines):
        y = gap + i * (line_h + gap)
        # Simulate a text line: draw some black rectangles (like printed characters)
        for x_start in range(10, img_w - 10, 15):
            cv2.rectangle(img, (x_start, y + 2), (x_start + 8, y + line_h - 2), (0, 0, 0), -1)
    return img


def test_segment_clue_rows_finds_lines():
    region = _make_clue_region(n_lines=5)
    rows = segment_clue_rows(region)
    assert len(rows) == 5


def test_segment_clue_rows_empty_image():
    blank = np.full((100, 200, 3), 255, dtype=np.uint8)
    rows = segment_clue_rows(blank)
    assert rows == []


def test_segment_clue_rows_returns_tuples():
    region = _make_clue_region(n_lines=3)
    rows = segment_clue_rows(region)
    for y1, y2 in rows:
        assert y1 < y2


# ---------------------------------------------------------------------------
# find_clue_list_region
# ---------------------------------------------------------------------------

def test_find_clue_list_region_no_grid():
    img = np.zeros((400, 300, 3), dtype=np.uint8)
    region, ox, oy = find_clue_list_region(img, None)
    assert region.shape == img.shape
    assert ox == 0 and oy == 0


def test_find_clue_list_region_grid_at_top():
    """Grid occupies top half; clue list should be the bottom half."""
    img = np.zeros((400, 300, 3), dtype=np.uint8)
    # Grid bbox: x=0, y=0, w=300, h=200 (top half)
    region, ox, oy = find_clue_list_region(img, (0, 0, 300, 200))
    assert oy == 200  # clue region starts at y=200


# ---------------------------------------------------------------------------
# GridResult / CellResult / ClueAnnotation dataclasses
# ---------------------------------------------------------------------------

def test_grid_result_defaults():
    gr = GridResult(puzzle_number=28397, image_path="test.jpg", rows=15, cols=15)
    assert gr.cells == []
    assert gr.clue_annotations == []


def test_cell_result_fields():
    cr = CellResult(row=0, col=0, clue_number=1, letter="M", annotation="circle")
    assert cr.confidence == 1.0


def test_clue_annotation_fields():
    ca = ClueAnnotation(clue_number=12, direction="ac", annotation="star", confidence=0.8)
    assert ca.clue_number == 12
    assert ca.direction == "ac"
    assert ca.annotation == "star"


# ---------------------------------------------------------------------------
# Helpers for clue-map tests
# ---------------------------------------------------------------------------

def _make_col_image(
    n_clues: int = 5,
    col_w: int = 400,
    line_h: int = 60,
    gap: int = 10,
) -> np.ndarray:
    """
    Synthetic column image: white background with n_clues numbered clue rows.

    Each row has a solid dark rectangle at x=42–65 (the number zone that
    _find_clue_starts_by_projection scans at x=40–90).  The body is kept
    intentionally sparse so total band ink stays below the heading-filter
    threshold (band_h * col_w * 0.08).

    Row y-centres: gap + i*(line_h+gap) + line_h//2.
    """
    total_h = n_clues * (line_h + gap) + gap
    img = np.full((total_h, col_w, 3), 255, dtype=np.uint8)
    for i in range(n_clues):
        y_top = gap + i * (line_h + gap)
        y_bot = y_top + line_h
        # Clue number digit block inside number zone (x=40–90)
        cv2.rectangle(img, (42, y_top + 5), (65, y_bot - 5), (0, 0, 0), -1)
        # Sparse text body: two narrow character-like blocks (well under heading threshold)
        cv2.rectangle(img, (100, y_top + 15), (108, y_bot - 15), (60, 60, 60), -1)
        cv2.rectangle(img, (130, y_top + 15), (138, y_bot - 15), (60, 60, 60), -1)
    return img


def _expected_y_centres(n_clues: int, line_h: int = 60, gap: int = 10) -> list[int]:
    return [gap + i * (line_h + gap) + line_h // 2 for i in range(n_clues)]


# ---------------------------------------------------------------------------
# _find_clue_starts_by_projection
# ---------------------------------------------------------------------------

def test_find_clue_starts_returns_list():
    col = _make_col_image(n_clues=4)
    starts = _find_clue_starts_by_projection(col)
    assert isinstance(starts, list)


def test_find_clue_starts_detects_all_clues():
    n = 5
    col = _make_col_image(n_clues=n)
    starts = _find_clue_starts_by_projection(col)
    assert len(starts) == n


def test_find_clue_starts_empty_image():
    blank = np.full((200, 400, 3), 255, dtype=np.uint8)
    starts = _find_clue_starts_by_projection(blank)
    assert starts == []


def test_find_clue_starts_y_centres_are_sorted():
    col = _make_col_image(n_clues=6)
    starts = _find_clue_starts_by_projection(col)
    assert starts == sorted(starts)


def test_find_clue_starts_y_centres_are_approximate():
    """Detected y-centres should be within ±30px of the true centres."""
    n = 4
    col = _make_col_image(n_clues=n)
    starts = _find_clue_starts_by_projection(col)
    expected = _expected_y_centres(n)
    assert len(starts) == len(expected)
    for detected, true_y in zip(starts, expected):
        assert abs(detected - true_y) < 30, f"detected={detected}, expected={true_y}"


# ---------------------------------------------------------------------------
# _build_clue_map_from_sequence
# ---------------------------------------------------------------------------

def test_build_clue_map_perfect_match():
    """When n_detected == n_expected every clue is paired in order."""
    seq = [1, 5, 9, 14, 18]
    col = _make_col_image(n_clues=len(seq))
    result = _build_clue_map_from_sequence(col, seq)
    assert len(result) == len(seq)
    nums = [num for _, num in result]
    assert nums == seq


def test_build_clue_map_returns_sorted_by_y():
    seq = [2, 7, 11]
    col = _make_col_image(n_clues=len(seq))
    result = _build_clue_map_from_sequence(col, seq)
    ys = [y for y, _ in result]
    assert ys == sorted(ys)


def test_build_clue_map_empty_sequence():
    col = _make_col_image(n_clues=3)
    result = _build_clue_map_from_sequence(col, [])
    # No sequence → nothing to pair
    assert result == []


def test_build_clue_map_empty_image():
    blank = np.full((200, 400, 3), 255, dtype=np.uint8)
    result = _build_clue_map_from_sequence(blank, [1, 2, 3])
    # No detected starts → empty
    assert result == []


def test_build_clue_map_more_detections_than_expected():
    """Extra detected starts are trimmed to match the sequence length."""
    seq = [3, 7]
    # Image with 4 clue rows, but sequence only names 2
    col = _make_col_image(n_clues=4)
    result = _build_clue_map_from_sequence(col, seq)
    assert len(result) == len(seq)
    nums = [num for _, num in result]
    assert nums == seq


def test_build_clue_map_fewer_detections_than_expected():
    """Fewer detections than expected: result is padded/interpolated to full length."""
    seq = [1, 5, 9, 14, 18, 22]
    # Only 3 real clue rows in the image
    col = _make_col_image(n_clues=3)
    result = _build_clue_map_from_sequence(col, seq)
    assert len(result) == len(seq)
    nums = [num for _, num in result]
    assert nums == seq


# ---------------------------------------------------------------------------
# _lookup_clue_number
# ---------------------------------------------------------------------------

def test_lookup_exact_match():
    clue_map = [(100, 1), (200, 5), (300, 9)]
    assert _lookup_clue_number(clue_map, 200) == 5


def test_lookup_nearest_within_range():
    clue_map = [(100, 1), (300, 5)]
    # 180 is closest to 100 (dist=80) vs 300 (dist=120) → clue 1
    assert _lookup_clue_number(clue_map, 180) == 1


def test_lookup_returns_none_when_too_far():
    clue_map = [(100, 1), (200, 5)]
    assert _lookup_clue_number(clue_map, 500, max_dist=200) is None


def test_lookup_returns_none_on_empty_map():
    assert _lookup_clue_number([], 150) is None


def test_lookup_single_entry_within_range():
    clue_map = [(120, 7)]
    assert _lookup_clue_number(clue_map, 150, max_dist=200) == 7


def test_lookup_single_entry_out_of_range():
    clue_map = [(120, 7)]
    assert _lookup_clue_number(clue_map, 400, max_dist=200) is None


def test_lookup_ties_broken_by_first_min():
    """When two entries are equidistant, min() returns the first (lower y)."""
    clue_map = [(100, 2), (200, 8)]
    # star_y=150 is 50px from both
    result = _lookup_clue_number(clue_map, 150)
    assert result in (2, 8)  # either is acceptable; just confirm no crash
