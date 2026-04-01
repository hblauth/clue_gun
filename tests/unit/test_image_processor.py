"""Unit tests for image_processor pipeline (no DB, no real images required)."""
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from services.image_processor.pipeline import (
    CellResult,
    GridResult,
    _cluster_positions,
    _order_corners,
    classify_annotation,
    deskew,
)


# ---------------------------------------------------------------------------
# _cluster_positions
# ---------------------------------------------------------------------------

def test_cluster_positions_merges_nearby():
    from services.image_processor.pipeline import _cluster_positions
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
# classify_annotation — synthetic cell images
# ---------------------------------------------------------------------------

def _blank_cell(size=64) -> np.ndarray:
    """White RGB cell (no annotation)."""
    return np.full((size, size, 3), 255, dtype=np.uint8)


def _cell_with_circle(size=64) -> np.ndarray:
    """Cell with a circle drawn in the top-left number region."""
    img = _blank_cell(size)
    region_size = size * 28 // 100
    cx, cy = region_size // 2, region_size // 2
    r = region_size // 3
    cv2.circle(img, (cx, cy), r, (0, 0, 0), 2)
    return img


def _cell_with_cross(size=64) -> np.ndarray:
    """Cell with two crossing lines in the top-left number region."""
    img = _blank_cell(size)
    s = size * 28 // 100
    cv2.line(img, (0, 0), (s, s), (0, 0, 0), 2)
    cv2.line(img, (s, 0), (0, s), (0, 0, 0), 2)
    return img


def _cell_with_strikethrough(size=64) -> np.ndarray:
    """Cell with a single horizontal line in the top-left number region."""
    img = _blank_cell(size)
    s = size * 28 // 100
    mid = s // 2
    cv2.line(img, (0, mid), (s, mid), (0, 0, 0), 2)
    return img


def test_classify_blank_returns_none():
    annotation, confidence = classify_annotation(_blank_cell())
    assert annotation is None
    assert confidence > 0.5


def test_classify_circle_detected():
    annotation, _ = classify_annotation(_cell_with_circle())
    assert annotation == "circle"


def test_classify_cross_detected():
    annotation, _ = classify_annotation(_cell_with_cross())
    assert annotation == "cross"


def test_classify_strikethrough_detected():
    annotation, _ = classify_annotation(_cell_with_strikethrough())
    assert annotation == "strikethrough"


def test_classify_confidence_range():
    _, confidence = classify_annotation(_cell_with_circle())
    assert 0.0 <= confidence <= 1.0


# ---------------------------------------------------------------------------
# deskew — should not crash on plain image
# ---------------------------------------------------------------------------

def test_deskew_passthrough():
    img = np.full((200, 200, 3), 200, dtype=np.uint8)
    result = deskew(img)
    assert result.shape == img.shape


# ---------------------------------------------------------------------------
# GridResult / CellResult dataclasses
# ---------------------------------------------------------------------------

def test_grid_result_defaults():
    gr = GridResult(puzzle_number=28397, image_path="test.jpg", rows=15, cols=15)
    assert gr.cells == []


def test_cell_result_fields():
    cr = CellResult(row=0, col=0, clue_number=1, letter="M", annotation="circle")
    assert cr.confidence == 1.0
