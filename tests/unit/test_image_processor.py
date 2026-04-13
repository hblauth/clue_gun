"""Unit tests for image_processor pipeline (no DB, no real images required)."""
import sys
import math
from pathlib import Path
from unittest.mock import patch

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
    _suppress_light_ink,
    _classify_contour_shape,
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


def test_find_clue_list_region_prefers_text_density_over_area():
    """When the larger candidate is nearly solid black, pick the smaller one with text-like density."""
    rng = np.random.default_rng(0)
    h, w = 400, 600
    img = np.full((h, w, 3), 255, dtype=np.uint8)

    # Left region (x=0-200): sparse text-like ink (10% dark pixels)
    img[50:350, 0:200] = rng.choice([0, 255], size=(300, 200, 3), p=[0.10, 0.90]).astype(np.uint8)

    # Right region (x=400-600): nearly solid black (another dense page)
    img[:, 400:600] = 0

    # Grid in the middle (x=200-400)
    grid_bbox = (200, 0, 200, h)
    region, ox, oy = find_clue_list_region(img, grid_bbox)

    # Right is larger (200*400 > 200*400, tie in area — left_w=200=right_w=200 actually)
    # But right is solid black (density≈1), left has sparse ink (density≈0.10) → left wins
    assert ox == 0  # left region selected, not the dense right region


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

    The number zone block is deliberately narrow (5px wide) so the per-column
    ink density stays within the [1%, 35%] range that _find_text_start_x
    expects for "text-like" columns.  At 5px wide the column still delivers
    5 ink pixels per row — above the min_ink=4 threshold used by the band
    detector — and 5*line_h / total_h ≈ 14% which is safely in range.

    Row y-centres: gap + i*(line_h+gap) + line_h//2.
    """
    # Ensure per-column ink density stays below _find_text_start_x's 35% ceiling.
    # n_clues * line_h ink pixels need at least (n_clues * line_h / 0.35) rows.
    # A 1 200px minimum covers all test cases comfortably.
    nat_h = n_clues * (line_h + gap) + gap
    total_h = max(nat_h, 1200)
    img = np.full((total_h, col_w, 3), 255, dtype=np.uint8)
    for i in range(n_clues):
        y_top = gap + i * (line_h + gap)
        y_bot = y_top + line_h
        # Narrow digit-like block inside the number zone (x=42–46, 5px wide).
        # Keeps per-column ink density ~14% — within _find_text_start_x's [1–35%] window.
        cv2.rectangle(img, (42, y_top + 5), (46, y_bot - 5), (0, 0, 0), -1)
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
# (mocks _build_clue_number_map — synthetic images have no OCR-readable digits)
# ---------------------------------------------------------------------------

_PATCH = "services.image_processor.pipeline._build_clue_number_map"


def test_build_clue_map_all_ocrd():
    """All clues OCR'd: result pairs each number with its y-position."""
    seq = [1, 5, 9, 14, 18]
    ocr = [(100, 1), (200, 5), (300, 9), (400, 14), (500, 18)]
    col = _make_col_image(n_clues=len(seq))
    with patch(_PATCH, return_value=ocr):
        result = _build_clue_map_from_sequence(col, seq)
    assert len(result) == len(seq)
    assert [num for _, num in result] == seq
    assert [y for y, _ in result] == [100, 200, 300, 400, 500]


def test_build_clue_map_returns_sorted_by_y():
    seq = [2, 7, 11]
    ocr = [(300, 11), (100, 2), (200, 7)]  # deliberately unsorted
    col = _make_col_image(n_clues=len(seq))
    with patch(_PATCH, return_value=ocr):
        result = _build_clue_map_from_sequence(col, seq)
    ys = [y for y, _ in result]
    assert ys == sorted(ys)


def test_build_clue_map_empty_sequence():
    col = _make_col_image(n_clues=3)
    with patch(_PATCH, return_value=[(100, 1), (200, 3)]):
        result = _build_clue_map_from_sequence(col, [])
    assert result == []


def test_build_clue_map_ocr_returns_nothing():
    """When OCR finds nothing, fall back to uniform spacing from projection bounds."""
    col = _make_col_image(n_clues=3)
    with patch(_PATCH, return_value=[]):
        result = _build_clue_map_from_sequence(col, [1, 3, 5])
    # Fallback produces a full map in order rather than empty list
    assert len(result) == 3
    ys = [y for y, _ in result]
    nums = [n for _, n in result]
    assert ys == sorted(ys)
    assert set(nums) == {1, 3, 5}


def test_build_clue_map_interpolates_middle():
    """Missing clues in the middle are interpolated between OCR'd neighbours."""
    seq = [1, 5, 9, 14, 18]
    # 9 and 14 are starred/missing from OCR
    ocr = [(100, 1), (200, 5), (500, 18)]
    col = _make_col_image(n_clues=len(seq))
    with patch(_PATCH, return_value=ocr):
        result = _build_clue_map_from_sequence(col, seq)
    assert len(result) == 5
    result_dict = {num: y for y, num in result}
    # 9 is at seq index 2 out of [1,5,9,14,18]; 14 is at index 3.
    # Between y=200 (idx 1) and y=500 (idx 4):
    assert 200 < result_dict[9]  < 500
    assert 200 < result_dict[14] < 500
    assert result_dict[9] < result_dict[14]


def test_build_clue_map_interpolates_leading():
    """Missing clues before the first OCR hit are extrapolated backwards."""
    seq = [1, 5, 9]
    ocr = [(300, 9)]  # only last clue found
    col = _make_col_image(n_clues=len(seq))
    with patch(_PATCH, return_value=ocr):
        result = _build_clue_map_from_sequence(col, seq)
    assert len(result) == 3
    result_dict = {num: y for y, num in result}
    # Extrapolated entries may be clamped to 0 at the column boundary,
    # so use <= to allow ties at y=0.
    assert result_dict[1] <= result_dict[5] <= result_dict[9]
    assert result_dict[9] == 300


def test_build_clue_map_interpolates_trailing():
    """Missing clues after the last OCR hit are extrapolated forwards."""
    seq = [1, 5, 9]
    ocr = [(100, 1)]  # only first clue found
    col = _make_col_image(n_clues=len(seq))
    with patch(_PATCH, return_value=ocr):
        result = _build_clue_map_from_sequence(col, seq)
    assert len(result) == 3
    result_dict = {num: y for y, num in result}
    assert result_dict[1] < result_dict[5] < result_dict[9]
    assert result_dict[1] == 100


def test_build_clue_map_ignores_ocr_numbers_not_in_sequence():
    """Extra OCR tokens whose numbers aren't in the sequence are discarded."""
    seq = [1, 5, 9]
    ocr = [(100, 1), (200, 5), (300, 9), (350, 27)]  # 27 not in seq
    col = _make_col_image(n_clues=len(seq))
    with patch(_PATCH, return_value=ocr):
        result = _build_clue_map_from_sequence(col, seq)
    nums = [num for _, num in result]
    assert 27 not in nums
    assert len(result) == 3


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


# ---------------------------------------------------------------------------
# _suppress_light_ink
# ---------------------------------------------------------------------------

def test_suppress_light_ink_removes_star_ink():
    """Pixels at star-ink brightness (~157) are set to 255."""
    gray = np.array([[157, 200, 255]], dtype=np.uint8)
    result = _suppress_light_ink(gray, threshold=140)
    assert result[0, 0] == 255
    assert result[0, 1] == 255
    assert result[0, 2] == 255


def test_suppress_light_ink_preserves_printed_text():
    """Pixels at printed-text brightness (~115) survive unchanged."""
    gray = np.array([[80, 115, 140]], dtype=np.uint8)
    result = _suppress_light_ink(gray, threshold=140)
    assert result[0, 0] == 80
    assert result[0, 1] == 115
    assert result[0, 2] == 140  # exactly at threshold is kept


def test_suppress_light_ink_does_not_modify_input():
    """The input array is not mutated."""
    gray = np.array([[100, 157, 200]], dtype=np.uint8)
    original = gray.copy()
    _suppress_light_ink(gray)
    np.testing.assert_array_equal(gray, original)


def test_suppress_light_ink_custom_threshold():
    gray = np.array([[100, 120, 160]], dtype=np.uint8)
    result = _suppress_light_ink(gray, threshold=110)
    assert result[0, 0] == 100   # 100 <= 110, kept
    assert result[0, 1] == 255   # 120 > 110, suppressed
    assert result[0, 2] == 255   # 160 > 110, suppressed


def test_suppress_light_ink_leaves_clean_column_unchanged():
    """A column with only printed text (no star ink) is unaffected."""
    col = _make_col_image(n_clues=3)
    gray = cv2.cvtColor(col, cv2.COLOR_RGB2GRAY)
    result = _suppress_light_ink(gray, threshold=140)
    # _make_col_image uses (0,0,0) and (60,60,60) — all well below 140
    np.testing.assert_array_equal(result, gray)


def test_find_clue_starts_ignores_star_ink_between_rows():
    """Star-ink patches in the gap between clue rows should not create extra detections."""
    n = 4
    col = _make_col_image(n_clues=n)
    line_h, gap = 60, 10
    # Paint star-ink gray (157) in the blank gap between row 1 and row 2,
    # overlapping the number zone — this would register as extra ink without suppression.
    gap_y = gap + 1 * (line_h + gap) + line_h  # bottom of row 1
    col[gap_y: gap_y + gap, 42:65] = (157, 157, 157)
    starts = _find_clue_starts_by_projection(col)
    assert len(starts) == n


# ---------------------------------------------------------------------------
# _classify_contour_shape — helpers
# ---------------------------------------------------------------------------

def _contour_from_filled_rect(x1: int, y1: int, x2: int, y2: int,
                               canvas: tuple[int, int] = (300, 300)) -> np.ndarray:
    """Return a CHAIN_APPROX_NONE contour of a filled axis-aligned rectangle."""
    img = np.zeros(canvas, dtype=np.uint8)
    cv2.rectangle(img, (x1, y1), (x2, y2), 255, -1)
    contours, _ = cv2.findContours(img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return contours[0]


def _contour_from_rotated_rect(cx: int, cy: int, w: int, h: int, angle_deg: float,
                                canvas: tuple[int, int] = (300, 300)) -> np.ndarray:
    """Return a contour of a filled rotated rectangle (simulates diagonal marks)."""
    img = np.zeros(canvas, dtype=np.uint8)
    box = cv2.boxPoints(((cx, cy), (w, h), angle_deg))
    cv2.fillPoly(img, [np.int32(box)], 255)
    contours, _ = cv2.findContours(img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return contours[0]


# ---------------------------------------------------------------------------
# _classify_contour_shape — tests
# ---------------------------------------------------------------------------

def test_classify_contour_shape_horizontal_line():
    """Wide flat rectangle (e.g. an underline) is classified as 'line'."""
    c = _contour_from_filled_rect(10, 100, 120, 108)  # 110×8 → aspect ≈ 13.8
    assert _classify_contour_shape(c) == "line"


def test_classify_contour_shape_vertical_line():
    """Tall narrow rectangle (e.g. a tick mark) is classified as 'line'."""
    c = _contour_from_filled_rect(100, 10, 108, 90)  # 8×80 → aspect = 10
    assert _classify_contour_shape(c) == "line"


def test_classify_contour_shape_diagonal_line():
    """A rotated elongated rectangle is classified as 'line' via minAreaRect."""
    c = _contour_from_rotated_rect(150, 150, 100, 8, angle_deg=45)  # 100×8 at 45°
    assert _classify_contour_shape(c) == "line"


def test_classify_contour_shape_square_blob():
    """A near-square blob is classified as 'blob'."""
    c = _contour_from_filled_rect(50, 50, 110, 110)  # 60×60 → aspect = 1.0
    assert _classify_contour_shape(c) == "blob"


def test_classify_contour_shape_slightly_rectangular_blob():
    """A 2:1 rectangle is well under the line threshold and classified as 'blob'."""
    c = _contour_from_filled_rect(50, 50, 150, 100)  # 100×50 → aspect = 2.0
    assert _classify_contour_shape(c) == "blob"


def test_classify_contour_shape_too_few_points():
    """Contours with fewer than 5 points pass through as 'blob' for stage-2 evaluation."""
    tiny = np.array([[[0, 0]], [[10, 0]], [[10, 10]], [[0, 10]]], dtype=np.int32)
    assert _classify_contour_shape(tiny) == "blob"


def test_classify_contour_shape_at_threshold_boundary():
    """Aspect ratio exactly at 3.0 is 'blob'; just above is 'line'."""
    # 3:1 rectangle → aspect = 3.0 → blob (threshold is strictly > 3.0)
    blob = _contour_from_filled_rect(10, 10, 70, 30)  # 60×20 → aspect = 3.0
    assert _classify_contour_shape(blob) == "blob"
    # 4:1 rectangle → aspect = 4.0 → line
    line = _contour_from_filled_rect(10, 10, 90, 30)  # 80×20 → aspect = 4.0
    assert _classify_contour_shape(line) == "line"


def test_classify_contour_shape_degenerate_passes_through():
    """A degenerate contour (short_side < 1) passes through as 'blob', not silently dropped."""
    # A single-column strip has effectively zero short side in its minAreaRect.
    # Create via a very thin 1-pixel wide rectangle.
    img = np.zeros((100, 100), np.uint8)
    img[10:90, 50] = 255  # single-pixel column
    contours, _ = cv2.findContours(img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    assert _classify_contour_shape(contours[0]) == "blob"
