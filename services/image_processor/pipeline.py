"""
Image processor pipeline.

Stages:
  1. Ingest    — load image (HEIC/JPEG/PNG), EXIF-rotate, convert to RGB numpy array
  2. Deskew    — detect grid angle via HoughLinesP, rotate to axis-aligned
  3. Grid      — find grid contour, perspective-warp to square
  4. Segment   — detect cell boundaries, extract per-cell sub-images
  5. OCR       — read clue numbers (printed) and filled letters (handwritten)
  6. Annotate  — classify symbol on clue-number region (circle/square/star/…)
  7. Output    — return structured GridResult

Usage:
    python services/image_processor/pipeline.py path/to/photo.jpg [--puzzle-number 28397]
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class CellResult:
    row: int
    col: int
    clue_number: Optional[int]
    letter: Optional[str]
    annotation: Optional[str]  # circle | square | star | strikethrough | cross | None
    confidence: float = 1.0


@dataclass
class GridResult:
    puzzle_number: Optional[int]
    image_path: str
    rows: int
    cols: int
    cells: list[CellResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 1 — Ingest
# ---------------------------------------------------------------------------

def load_image(path: str) -> np.ndarray:
    """Return an axis-aligned RGB numpy array, handling HEIC and EXIF rotation."""
    p = Path(path)
    if p.suffix.upper() in (".HEIC", ".HEIF"):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            raise RuntimeError("pillow-heif required for HEIC files: pip install pillow-heif")

    pil_img = Image.open(path)
    pil_img = ImageOps.exif_transpose(pil_img)
    pil_img = pil_img.convert("RGB")
    return np.array(pil_img)


# ---------------------------------------------------------------------------
# Stage 2 — Deskew
# ---------------------------------------------------------------------------

def deskew(img: np.ndarray) -> np.ndarray:
    """Rotate image so grid lines are axis-aligned."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100,
                             minLineLength=img.shape[1] // 4, maxLineGap=20)
    if lines is None:
        logger.debug("deskew: no lines found, skipping")
        return img

    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        # Only consider near-horizontal lines (within ±45°)
        if -45 < angle < 45:
            angles.append(angle)

    if not angles:
        return img

    median_angle = float(np.median(angles))
    if abs(median_angle) < 0.3:
        return img  # already straight enough

    logger.debug("deskew: rotating by %.2f°", -median_angle)
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), median_angle, 1.0)
    rotated = cv2.warpAffine(img, M, (w, h),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)
    return rotated


# ---------------------------------------------------------------------------
# Stage 3 — Grid detection + perspective warp
# ---------------------------------------------------------------------------

def detect_and_warp_grid(img: np.ndarray, target_size: int = 1080) -> np.ndarray:
    """
    Find the crossword grid (largest near-square contour) and warp to target_size × target_size.
    Falls back to the whole image if no grid is found.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(blurred, 255,
                                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY_INV, 11, 2)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        logger.warning("detect_grid: no contours found, using full image")
        return _resize_square(img, target_size)

    # Largest contour by area that approximates a quadrilateral
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    grid_contour = None
    for c in contours[:10]:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4:
            grid_contour = approx
            break

    if grid_contour is None:
        logger.warning("detect_grid: no quadrilateral found, using full image")
        return _resize_square(img, target_size)

    src_pts = _order_corners(grid_contour.reshape(4, 2).astype(np.float32))
    dst_pts = np.array([[0, 0], [target_size - 1, 0],
                         [target_size - 1, target_size - 1], [0, target_size - 1]],
                        dtype=np.float32)
    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    warped = cv2.warpPerspective(img, M, (target_size, target_size))
    return warped


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points: top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]   # top-left
    rect[2] = pts[np.argmax(s)]   # bottom-right
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # top-right
    rect[3] = pts[np.argmax(diff)]  # bottom-left
    return rect


def _resize_square(img: np.ndarray, size: int) -> np.ndarray:
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------------------
# Stage 4 — Cell segmentation
# ---------------------------------------------------------------------------

def segment_cells(grid_img: np.ndarray, expected_size: int = 15) -> list[list[np.ndarray]]:
    """
    Split the warped grid into cells.

    Tries to detect grid lines for precise boundaries; falls back to uniform division.
    Returns a 2D list: cells[row][col] = numpy array (RGB).
    """
    lines = _detect_grid_lines(grid_img)
    if lines:
        xs, ys = lines
    else:
        h, w = grid_img.shape[:2]
        xs = list(range(0, w, w // expected_size)) + [w]
        ys = list(range(0, h, h // expected_size)) + [h]

    cells: list[list[np.ndarray]] = []
    for r in range(len(ys) - 1):
        row_cells = []
        for c in range(len(xs) - 1):
            cell = grid_img[ys[r]:ys[r + 1], xs[c]:xs[c + 1]]
            row_cells.append(cell)
        cells.append(row_cells)
    return cells


def _detect_grid_lines(img: np.ndarray) -> tuple[list[int], list[int]] | None:
    """Return sorted x and y pixel positions of grid lines, or None if detection fails."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 30, 100)

    h, w = img.shape[:2]
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80,
                              minLineLength=w // 3, maxLineGap=10)
    if lines is None:
        return None

    h_positions, v_positions = [], []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if abs(y2 - y1) < 5:   # horizontal
            h_positions.append((y1 + y2) // 2)
        elif abs(x2 - x1) < 5:  # vertical
            v_positions.append((x1 + x2) // 2)

    xs = _cluster_positions(sorted(set(v_positions)), gap=w // 30)
    ys = _cluster_positions(sorted(set(h_positions)), gap=h // 30)

    if len(xs) < 4 or len(ys) < 4:
        return None

    return xs, ys


def _cluster_positions(positions: list[int], gap: int) -> list[int]:
    """Merge nearby positions into cluster centres."""
    if not positions:
        return []
    clusters: list[list[int]] = [[positions[0]]]
    for p in positions[1:]:
        if p - clusters[-1][-1] <= gap:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [int(np.mean(c)) for c in clusters]


# ---------------------------------------------------------------------------
# Stage 5 — OCR: clue numbers + filled letters
# ---------------------------------------------------------------------------

def _number_region(cell: np.ndarray) -> np.ndarray:
    """Top-left ~28% of cell — contains the printed clue number."""
    h, w = cell.shape[:2]
    return cell[:max(1, h * 28 // 100), :max(1, w * 28 // 100)]


def _letter_region(cell: np.ndarray) -> np.ndarray:
    """Centre ~60% of cell — contains the handwritten letter."""
    h, w = cell.shape[:2]
    margin_y, margin_x = h * 20 // 100, w * 20 // 100
    return cell[margin_y:h - margin_y, margin_x:w - margin_x]


def ocr_clue_number(cell: np.ndarray) -> Optional[int]:
    """Read the printed clue number from the top-left sub-region."""
    try:
        import pytesseract
    except ImportError:
        return None

    region = _number_region(cell)
    if region.size == 0:
        return None

    gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)
    # Upscale for Tesseract
    gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    config = "--psm 10 -c tessedit_char_whitelist=0123456789"
    text = pytesseract.image_to_string(thresh, config=config).strip()
    try:
        return int(text) if text else None
    except ValueError:
        return None


def ocr_letter(cell: np.ndarray) -> Optional[str]:
    """Read the handwritten letter from the centre region."""
    try:
        import pytesseract
    except ImportError:
        return None

    region = _letter_region(cell)
    if region.size == 0:
        return None

    gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)

    # Sauvola-style adaptive threshold via OpenCV
    thresh = cv2.adaptiveThreshold(gray, 255,
                                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY, 21, 8)

    # Check if cell is mostly dark (black square) — skip
    if np.mean(thresh) < 30:
        return None

    config = "--psm 10 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    text = pytesseract.image_to_string(thresh, config=config).strip().upper()
    return text[0] if len(text) == 1 else None


# ---------------------------------------------------------------------------
# Stage 6 — Annotation classification
# ---------------------------------------------------------------------------

def classify_annotation(cell: np.ndarray) -> tuple[Optional[str], float]:
    """
    Classify the symbol (if any) drawn on the clue-number sub-region.

    Returns (annotation_type, confidence) where annotation_type is one of:
        'circle', 'square', 'star', 'strikethrough', 'cross', or None.

    Uses classical CV contour/line analysis. Swap in a CNN by replacing this function.
    Decision order: circle → square → cross → strikethrough → star → none.
    """
    region = _number_region(cell)
    if region.size == 0:
        return None, 1.0

    gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)
    # Upscale for analysis
    gray = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Check pixel density — if nearly blank, no annotation
    density = np.sum(thresh > 0) / thresh.size
    if density < 0.05:
        return None, 0.95

    # --- Circle: check with HoughCircles first (most distinctive shape) ---
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1, minDist=20,
        param1=50, param2=15, minRadius=8, maxRadius=30,
    )
    if circles is not None:
        return "circle", 0.80

    # --- Line analysis: cross vs strikethrough (before contour to avoid thin-rect confusion) ---
    lines = cv2.HoughLinesP(thresh, 1, np.pi / 180, threshold=12,
                              minLineLength=20, maxLineGap=5)
    if lines is not None and len(lines) >= 1:
        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            angles.append(math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180)

        # Determine angular spread accounting for the 0°/180° wraparound:
        # If any angles are near 180° and others near 0°, they represent the same
        # direction. Fold those near-180° values down by subtracting 180 so that
        # spread is computed correctly for both "same direction" and "perpendicular" cases.
        angles_arr = np.array(angles)
        if np.any(angles_arr > 150) and np.any(angles_arr < 30):
            angles_arr[angles_arr > 150] -= 180  # map e.g. 179° → -1°
        angle_spread = float(np.max(angles_arr) - np.min(angles_arr))
        if angle_spread > 30 and len(lines) >= 2:
            return "cross", 0.75
        return "strikethrough", 0.70

    # --- Contour analysis: square, circle, star ---
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [c for c in contours if cv2.contourArea(c) > 10]

    if contours:
        c = max(contours, key=cv2.contourArea)
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.04 * peri, True)
        area = cv2.contourArea(c)
        circularity = (4 * math.pi * area / (peri ** 2)) if peri > 0 else 0
        n_corners = len(approx)

        # Square: 4 corners with near-1:1 bounding box aspect ratio
        if n_corners == 4:
            x, y, w, h = cv2.boundingRect(c)
            aspect = min(w, h) / max(w, h) if max(w, h) > 0 else 0
            if aspect > 0.5:
                return "square", 0.70

        if circularity > 0.65:
            return "circle", 0.70

        if n_corners >= 8:
            return "star", 0.60

    return None, 0.80


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_image(image_path: str, puzzle_number: Optional[int] = None,
                  grid_size: int = 15) -> GridResult:
    logger.info("Processing %s", image_path)

    img = load_image(image_path)
    logger.info("Loaded: %dx%d", img.shape[1], img.shape[0])

    img = deskew(img)
    grid_img = detect_and_warp_grid(img)
    cells_2d = segment_cells(grid_img, expected_size=grid_size)

    rows = len(cells_2d)
    cols = len(cells_2d[0]) if cells_2d else 0
    logger.info("Grid: %d×%d cells", rows, cols)

    result = GridResult(
        puzzle_number=puzzle_number,
        image_path=str(Path(image_path).name),
        rows=rows,
        cols=cols,
    )

    for r, row_cells in enumerate(cells_2d):
        for c, cell in enumerate(row_cells):
            clue_num = ocr_clue_number(cell)
            letter = ocr_letter(cell)
            annotation, confidence = classify_annotation(cell)
            result.cells.append(CellResult(
                row=r, col=c,
                clue_number=clue_num,
                letter=letter,
                annotation=annotation,
                confidence=confidence,
            ))

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(description="Process a crossword photo")
    parser.add_argument("image", help="Path to photo (JPEG/PNG/HEIC)")
    parser.add_argument("--puzzle-number", type=int, default=None)
    parser.add_argument("--grid-size", type=int, default=15,
                        help="Expected grid dimension (default 15)")
    parser.add_argument("--out", default=None, help="Write JSON result to file")
    args = parser.parse_args()

    result = process_image(args.image, args.puzzle_number, args.grid_size)
    output = json.dumps(asdict(result), indent=2)

    if args.out:
        Path(args.out).write_text(output)
        logger.info("Wrote result to %s", args.out)
    else:
        print(output)
