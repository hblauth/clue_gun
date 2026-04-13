"""
Image processor pipeline.

Stages:
  1. Ingest    — load image (HEIC/JPEG/PNG), EXIF-rotate, convert to RGB numpy array
  2. Deskew    — detect grid angle via HoughLinesP, rotate to axis-aligned
  3. Grid      — find grid bounding box + perspective-warp copy for letter OCR
  4. Segment   — detect cell boundaries, extract per-cell sub-images
  5. OCR       — read filled letters (handwritten) from grid cells
  6. Annotate  — detect star marks on clue numbers in the printed CLUE LIST
  7. Output    — return structured GridResult

Usage:
    python services/image_processor/pipeline.py path/to/photo.jpg [--puzzle-number 28397]
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
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
    annotation: Optional[str]  # legacy — kept for grid-cell results
    confidence: float = 1.0


@dataclass
class ClueAnnotation:
    """A hand-drawn annotation found on a clue number in the printed clue list."""
    clue_number: int
    direction: str        # "ac" | "d" | "" if unknown
    annotation: str       # "star" for MVP
    confidence: float = 1.0


@dataclass
class GridResult:
    puzzle_number: Optional[int]
    image_path: str
    rows: int
    cols: int
    cells: list[CellResult] = field(default_factory=list)
    clue_annotations: list[ClueAnnotation] = field(default_factory=list)


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
        if -45 < angle < 45:
            angles.append(angle)

    if not angles:
        return img

    median_angle = float(np.median(angles))
    if abs(median_angle) < 0.3:
        return img

    logger.debug("deskew: rotating by %.2f°", -median_angle)
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), median_angle, 1.0)
    rotated = cv2.warpAffine(img, M, (w, h),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)
    return rotated


# ---------------------------------------------------------------------------
# Stage 3 — Grid detection
# ---------------------------------------------------------------------------

def _find_grid_contour(img: np.ndarray):
    """Return the 4-point approxPolyDP contour for the crossword grid, or None."""
    h, w = img.shape[:2]
    img_area = h * w

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(blurred, 255,
                                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY_INV, 11, 2)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < img_area * 0.05:
            continue
        x, y, cw, ch = cv2.boundingRect(c)
        aspect = cw / ch if ch > 0 else 0
        if 0.5 <= aspect <= 2.0:
            candidates.append(c)

    candidates = sorted(candidates, key=cv2.contourArea, reverse=True)

    for c in candidates[:10]:
        peri = cv2.arcLength(c, True)
        for eps_factor in (0.02, 0.04, 0.06, 0.08, 0.10, 0.12):
            approx = cv2.approxPolyDP(c, eps_factor * peri, True)
            if len(approx) == 4:
                return approx

    return None


def detect_grid_bbox(img: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    """Return (x, y, w, h) bounding box of the crossword grid, or None."""
    approx = _find_grid_contour(img)
    if approx is None:
        return None
    x, y, w, h = cv2.boundingRect(approx)
    return (x, y, w, h)


def detect_and_warp_grid(img: np.ndarray, target_size: int = 1080) -> np.ndarray:
    """
    Find the crossword grid and warp to target_size × target_size.
    Falls back to the whole image if no grid is found.
    """
    approx = _find_grid_contour(img)
    if approx is None:
        logger.warning("detect_grid: no quadrilateral found, using full image")
        return _resize_square(img, target_size)

    src_pts = _order_corners(approx.reshape(4, 2).astype(np.float32))
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
# Stage 4 — Cell segmentation (for letter OCR)
# ---------------------------------------------------------------------------

def segment_cells(grid_img: np.ndarray, expected_size: int = 15) -> list[list[np.ndarray]]:
    """
    Split the warped grid into cells.
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
        if abs(y2 - y1) < 5:
            h_positions.append((y1 + y2) // 2)
        elif abs(x2 - x1) < 5:
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
# Stage 5 — OCR: filled letters from grid cells
# ---------------------------------------------------------------------------

def _letter_region(cell: np.ndarray) -> np.ndarray:
    """Centre ~60% of cell — contains the handwritten letter."""
    h, w = cell.shape[:2]
    margin_y, margin_x = h * 20 // 100, w * 20 // 100
    return cell[margin_y:h - margin_y, margin_x:w - margin_x]


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
    thresh = cv2.adaptiveThreshold(gray, 255,
                                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY, 21, 8)
    if np.mean(thresh) < 30:
        return None

    config = "--psm 10 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    text = pytesseract.image_to_string(thresh, config=config).strip().upper()
    return text[0] if len(text) == 1 else None


# Kept for backward compat / unit tests
def ocr_clue_number(cell: np.ndarray) -> Optional[int]:
    """Read the printed clue number from the top-left sub-region of a grid cell."""
    try:
        import pytesseract
    except ImportError:
        return None

    h, w = cell.shape[:2]
    region = cell[:max(1, h * 28 // 100), :max(1, w * 28 // 100)]
    if region.size == 0:
        return None

    gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    config = "--psm 10 -c tessedit_char_whitelist=0123456789"
    text = pytesseract.image_to_string(thresh, config=config).strip()
    try:
        return int(text) if text else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Stage 6 — Annotation detection in the printed clue list
# ---------------------------------------------------------------------------

def find_clue_list_region(
    img: np.ndarray,
    grid_bbox: Optional[tuple[int, int, int, int]],
) -> tuple[np.ndarray, int, int]:
    """
    Return the sub-image that contains the printed clue list (outside the grid),
    plus the (offset_x, offset_y) of that sub-image in the original image.

    Typical newspaper layout has clues:
      - Below the grid   (portrait phone shot)
      - To the right     (landscape or tabletop shot)
    We pick whichever non-grid area is larger.
    """
    h, w = img.shape[:2]

    if grid_bbox is None:
        return img, 0, 0

    gx, gy, gw, gh = grid_bbox
    grid_right = gx + gw
    grid_bottom = gy + gh

    below_h = max(0, h - grid_bottom)
    right_w = max(0, w - grid_right)

    # Also consider the area to the left of the grid
    left_w = max(0, gx)

    below_area = below_h * w
    right_area = right_w * h
    left_area = left_w * h

    if below_area >= right_area and below_area >= left_area and below_h > 30:
        region = img[grid_bottom:h, 0:w]
        return region, 0, grid_bottom
    elif right_area >= left_area and right_w > 30:
        region = img[0:h, grid_right:w]
        return region, grid_right, 0
    elif left_w > 30:
        region = img[0:h, 0:gx]
        return region, 0, 0
    else:
        # Fallback: lower half of image
        mid_y = h // 2
        return img[mid_y:h, 0:w], 0, mid_y


def segment_clue_rows(
    region: np.ndarray,
    min_line_height: int = 8,
    merge_gap: int = 6,
) -> list[tuple[int, int]]:
    """
    Find text rows in the clue list using connected-component analysis.

    Strategy:
      1. Binarise with adaptive threshold (handles uneven lighting in phone photos).
      2. Dilate horizontally with a large kernel so all characters in one line
         merge into a single blob.  The gap *between* lines is preserved.
      3. Find connected components; each blob is one text line.

    Returns list of (y_start, y_end) in region coordinates, sorted top-to-bottom.
    """
    if region.size == 0:
        return []

    rh, rw = region.shape[:2]
    gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)

    # Adaptive threshold — copes with uneven lighting in photos of newspapers
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 8
    )

    # Large horizontal dilation: merges all chars on the same line into one blob.
    # A kernel of rw//6 ~500px for a 3024px-wide crop works well for newspaper text.
    dil_w = max(40, rw // 6)
    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (dil_w, 1))
    dilated = cv2.dilate(binary, kernel_h)

    # Connected components
    n_labels, _, stats, _ = cv2.connectedComponentsWithStats(dilated, connectivity=8)
    # stats columns: x, y, w, h, area (label 0 = background)
    raw_rows: list[tuple[int, int]] = []
    for lbl in range(1, n_labels):
        _x, y, _w, h, area = stats[lbl]
        if h < min_line_height:
            continue
        if area < rw * 0.05:  # blob must span at least 5% of image width
            continue
        raw_rows.append((int(y), int(y + h)))

    raw_rows.sort(key=lambda r: r[0])

    # Merge overlapping / very-close rows (handles multi-line clue entries)
    merged: list[list[int]] = []
    for y1, y2 in raw_rows:
        if merged and y1 - merged[-1][1] <= merge_gap:
            merged[-1][1] = max(merged[-1][1], y2)
        else:
            merged.append([y1, y2])

    return [(y1, y2) for y1, y2 in merged]


def _ocr_puzzle_number(full_img: np.ndarray, rotation_code: Optional[int]) -> Optional[int]:
    """
    Read the puzzle number from the header of the correctly-oriented full image.

    The Times crossword header reads "Times Crossword XXXXX".  We rotate the
    full image by the same code used for the clue region, then OCR the top
    third looking for that pattern.
    """
    try:
        import pytesseract
    except ImportError:
        return None

    if rotation_code is not None:
        oriented = cv2.rotate(full_img, rotation_code)
    else:
        oriented = full_img

    h, w = oriented.shape[:2]
    # The header is in the top third of the image; use full width for accuracy
    sample = oriented[:h // 3, :]
    gray = cv2.cvtColor(sample, cv2.COLOR_RGB2GRAY)
    # Don't shrink too aggressively — puzzle number is small text
    scale_w = min(w, 2400)
    scale_h = int(sample.shape[0] * scale_w / w)
    small = cv2.resize(gray, (scale_w, scale_h), interpolation=cv2.INTER_CUBIC)
    try:
        text = pytesseract.image_to_string(small, config="--psm 3 --oem 1")
    except Exception:
        return None

    # "Times Crossword 28683" — OCR may mangle "Crossword" or add comma punctuation
    # e.g. "28,683" is common for 5-digit numbers in newspaper formatting.
    # Pattern: Times + Cros... + optional-space + digits with optional comma separator
    m = re.search(
        r'Times\s+Cros\w+\s+(\d{2,3}[,.]?\d{3}|\d{4,6})',
        text, re.IGNORECASE,
    )
    if m:
        raw = m.group(1).replace(",", "").replace(".", "")
        num = int(raw)
        logger.info("Puzzle number from image: %d", num)
        return num
    logger.debug("_ocr_puzzle_number: puzzle number not found in header OCR")
    return None


def _load_clue_sequences(puzzle_number: int) -> Optional[dict]:
    """
    Return {'ac': [1, 5, 9, ...], 'd': [1, 2, 3, ...]} for the given puzzle.

    Looks up data/puzzles/{puzzle_number}.json.  Returns None if not found.
    """
    p = Path(__file__).parent.parent.parent / "data" / "puzzles" / f"{puzzle_number}.json"
    if not p.exists():
        logger.debug("_load_clue_sequences: no JSON for puzzle %d", puzzle_number)
        return None
    try:
        data = json.loads(p.read_text())
        ac = sorted(int(c["number"]) for c in data.get("across", []))
        dn = sorted(int(c["number"]) for c in data.get("down", []))
        if ac or dn:
            logger.info("Loaded clue sequences for puzzle %d: %d ac, %d d", puzzle_number, len(ac), len(dn))
            return {"ac": ac, "d": dn}
    except Exception as exc:
        logger.debug("_load_clue_sequences: failed to parse JSON: %s", exc)
    return None


def _suppress_light_ink(gray: np.ndarray, threshold: int = 140) -> np.ndarray:
    """
    Return a copy of `gray` with pixels brighter than `threshold` set to 255.

    Printed text is typically gray ~115; hand-drawn pen/star ink is ~157.
    Suppressing pixels above 140 removes light pen marks while keeping dark
    printed characters intact, producing a cleaner image for projection and OCR.
    """
    out = gray.copy()
    out[out > threshold] = 255
    return out


def _find_clue_starts_by_projection(col_img: np.ndarray) -> list[int]:
    """
    Return a sorted list of y-centre positions where new numbered clue lines begin.

    Strategy: scan the ROW PROJECTION of the number zone only (the narrow
    left strip where clue digits are printed).  Each clue-start line has a
    digit there; continuation lines have near-zero ink.  This produces a
    1-D signal with clear peaks at each clue number position regardless of
    how close together the surrounding text lines are.

    1. Locate the clue body via _find_clue_text_bounds (skip the heading).
    2. Binarise with adaptive threshold (same params as _scan_column_for_stars).
    3. Row-project the number zone (text_x to text_x+80) inside the body.
    4. Smooth, then find local peaks above a noise threshold.
    5. Return the y-centres of those peaks.
    """
    ch, cw = col_img.shape[:2]

    # Step 1: body bounds — skip the ACROSS/DOWN heading
    y_first, y_last = _find_clue_text_bounds(col_img)
    if y_last <= y_first or y_last - y_first < 20:
        y_first, y_last = 0, ch

    # Step 2: binarize
    text_x = _find_text_start_x(col_img)
    nz_x2 = min(cw, text_x + 80)
    if nz_x2 <= text_x:
        return []

    gray = cv2.cvtColor(col_img, cv2.COLOR_RGB2GRAY)
    gray = _suppress_light_ink(gray)  # remove pencil / light star marks from gap rows
    nz_gray = gray[y_first:y_last, text_x:nz_x2]
    if nz_gray.size == 0:
        return []
    nz_bin = cv2.adaptiveThreshold(
        nz_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 10
    )

    # Step 3: row projection of the number zone
    nz_w = nz_x2 - text_x
    row_proj = np.sum(nz_bin > 0, axis=1).astype(float)

    # Step 4: smooth with a window ≈ expected digit height (scale with col height)
    body_h = y_last - y_first
    # Typical digit height: body_h / (N * 3) where N≈15 clues, 3 lines each.
    # Clamp between 8 and 60 px so extreme images stay sensible.
    smooth_w = int(np.clip(body_h / 45, 8, 60))
    kernel = np.ones(smooth_w) / smooth_w
    smoothed = np.convolve(row_proj, kernel, mode="same")

    # Noise floor: at least 10% of the number-zone width must be inked on a
    # clue-start row (single digit ≈ 20–40% of 80 px wide zone at any scale).
    noise_floor = max(1.0, nz_w * 0.10)

    # Step 5: find continuous runs above noise_floor in the smoothed signal.
    # Each run = one clue number (digit appears in number zone for several
    # consecutive rows, then disappears for the continuation / gap rows).
    # Use the run centre as the clue-start y-position.
    runs: list[tuple[int, int]] = []
    in_run = False
    run_start = 0
    for i, val in enumerate(smoothed):
        if not in_run and val >= noise_floor:
            in_run = True
            run_start = i
        elif in_run and val < noise_floor:
            in_run = False
            runs.append((run_start, i))
    if in_run:
        runs.append((run_start, len(smoothed)))

    # Convert relative indices back to full column coordinates
    result = [y_first + (r0 + r1) // 2 for r0, r1 in runs]

    logger.debug(
        "_find_clue_starts_by_projection: y_first=%d y_last=%d smooth_w=%d "
        "runs=%d clue_starts=%d",
        y_first, y_last, smooth_w, len(runs), len(result),
    )
    return result


def _find_clue_text_bounds(col_img: np.ndarray) -> tuple[int, int]:
    """
    Return (y_first_clue, y_last_clue) — the vertical extent of the clue body
    within `col_img`.

    Strategy: project the full-width binary image onto the y-axis, find all
    text bands, then identify:
      • the HEADING band  — the short band just before the main body
      • the BODY band     — the large continuous band containing all clues

    The heading band bottom + small gap gives y_first.
    The body band bottom gives y_last.

    Falls back to (0, col_height) if detection fails.
    """
    ch, cw = col_img.shape[:2]
    gray = cv2.cvtColor(col_img, cv2.COLOR_RGB2GRAY)
    gray = _suppress_light_ink(gray)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    binary = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 61, 15
    )
    text_x = _find_text_start_x(col_img)
    text_area = binary[:, text_x:]
    row_proj = np.sum(text_area > 0, axis=1).astype(int)
    min_ink = max(4, text_area.shape[1] // 150)

    bands: list[tuple[int, int]] = []
    in_band, start = False, 0
    for y in range(ch):
        if not in_band and row_proj[y] > min_ink:
            in_band, start = True, y
        elif in_band and row_proj[y] <= 1:
            in_band = False
            if y - start > 4:
                bands.append((start, y))
    if in_band:
        bands.append((start, ch))

    merged: list[list[int]] = []
    for y1, y2 in bands:
        if merged and y1 - merged[-1][1] <= 8:
            merged[-1][1] = max(merged[-1][1], y2)
        else:
            merged.append([y1, y2])

    # Largest band (>300px) is the continuous clue body; there may be large
    # non-text bands (dark objects, photos) so pick by maximum height.
    body_candidates = [(y1, y2) for y1, y2 in merged if (y2 - y1) > 300]
    if body_candidates:
        body_y1, body_y2 = max(body_candidates, key=lambda b: b[1] - b[0])
    elif merged:
        # No single large band (e.g. many small clue rows with wide gaps).
        # Treat the full extent of all text bands as the body.
        body_y1, body_y2 = merged[0][0], merged[-1][1]
    else:
        body_y1, body_y2 = -1, -1

    # Heading band: last short band (20–150px) that ends before the body starts.
    heading_end: int = -1
    for y1, y2 in merged:
        band_h = y2 - y1
        if 20 <= band_h <= 150 and (body_y1 < 0 or y2 <= body_y1 + 20):
            heading_end = y2

    if heading_end > 0:
        y_first = heading_end + 10
    elif body_y1 > 0:
        y_first = body_y1
    else:
        y_first = max(0, ch // 10)

    y_last = body_y2 if body_y2 > 0 else int(ch * 0.92)

    logger.debug(
        "_find_clue_text_bounds: heading_end=%d body=[%d,%d] → y_first=%d y_last=%d",
        heading_end, body_y1, body_y2, y_first, y_last,
    )
    return y_first, y_last


def _align_projection_to_sequence(
    proj_ys: list[int],
    clue_sequence: list[int],
    col_height: int,
) -> list[tuple[int, int]]:
    """
    Map M detected y-positions (from row-projection) to N clue numbers in
    sequence order.

    - M == N: direct 1-to-1 assignment.
    - M > N: sub-sample evenly (false positives are scattered; real clue
             starts are roughly evenly spaced).
    - M < N: treat proj_ys as anchors spread uniformly over the sequence,
             then interpolate/extrapolate the remaining entries exactly as
             the OCR path does.
    """
    N = len(clue_sequence)
    M = len(proj_ys)
    if M == 0 or N == 0:
        return []

    proj_ys = sorted(proj_ys)

    if M >= N:
        if M == N:
            selected = proj_ys
        else:
            # Pick N evenly spaced samples from the M detections.
            indices = [int(round(i * (M - 1) / (N - 1))) for i in range(N)]
            selected = [proj_ys[idx] for idx in indices]
        return list(zip(selected, clue_sequence))

    # M < N — distribute M detections evenly over the N-entry sequence as anchors.
    step = (N - 1) / (M - 1) if M > 1 else 0.0
    anchors: list[tuple[int, int, int]] = []  # (seq_idx, y, clue_num)
    for i, y in enumerate(proj_ys):
        idx = min(N - 1, int(round(i * step))) if M > 1 else 0
        anchors.append((idx, y, clue_sequence[idx]))

    m_idxs = [a[0] for a in anchors]
    m_ys   = [a[1] for a in anchors]
    avg_spacing = (m_ys[-1] - m_ys[0]) / max(len(m_ys) - 1, 1) if len(m_ys) >= 2 else col_height / max(N, 1)

    result: list[tuple[int, int]] = [(y, num) for _, y, num in anchors]
    anchored = set(m_idxs)
    for i in range(N):
        if i in anchored:
            continue
        before = [(j, y) for j, y in zip(m_idxs, m_ys) if j < i]
        after  = [(j, y) for j, y in zip(m_idxs, m_ys) if j > i]
        if before and after:
            j, y_j = max(before, key=lambda t: t[0])
            k, y_k = min(after,  key=lambda t: t[0])
            y_interp = int(y_j + (i - j) / (k - j) * (y_k - y_j))
        elif after:
            k, y_k = min(after, key=lambda t: t[0])
            y_interp = int(y_k - (k - i) * avg_spacing)
        else:
            j, y_j = max(before, key=lambda t: t[0])
            y_interp = int(y_j + (i - j) * avg_spacing)
        result.append((max(0, y_interp), clue_sequence[i]))

    result.sort(key=lambda t: t[0])
    return result


def _build_clue_map_from_sequence(
    col_img: np.ndarray,
    clue_sequence: list[int],
) -> list[tuple[int, int]]:
    """
    Build a (y_centre, clue_number) map for the clues in `clue_sequence`.

    Strategy (in priority order):
      1. Row-projection (primary): detect actual clue-start y-positions from
         ink density. Works when M detected positions ≥ N/2 expected clues.
         OCR exact positions override projection where available.
      2. OCR-only with interpolation: when projection is sparse (M < N/2),
         fall back to the original OCR value-matching + linear interpolation.
      3. Uniform spacing: last resort when both projection and OCR fail.
    """
    if not clue_sequence:
        return []

    N = len(clue_sequence)

    # --- OCR anchors (exact clue-number → y mappings) ---
    ocr_map = _build_clue_number_map(col_img)
    seq_set = set(clue_sequence)
    ocr_by_num: dict[int, int] = {}
    for y, num in sorted(ocr_map, key=lambda t: t[0]):
        if num in seq_set and num not in ocr_by_num:
            ocr_by_num[num] = y

    matched: list[tuple[int, int, int]] = []   # (seq_idx, y, clue_num)
    missing_idxs: list[int] = []
    for i, num in enumerate(clue_sequence):
        if num in ocr_by_num:
            matched.append((i, ocr_by_num[num], num))
        else:
            missing_idxs.append(i)

    # --- Primary: row-projection clue starts (when OCR coverage is sparse) ---
    # OCR-primary takes over when it covers ≥ 1/3 of the sequence (dense
    # enough that linear interpolation between close anchors is accurate).
    # When OCR covers < 1/3, projection-detected positions are more reliable
    # than linear extrapolation from just a handful of anchors.
    ocr_is_dense = len(matched) * 3 >= N

    if not ocr_is_dense:
        proj_ys = _find_clue_starts_by_projection(col_img)
        proj_ratio = len(proj_ys) / N if N else 0.0

        if proj_ys and 0.4 <= proj_ratio <= 2.5:
            base = _align_projection_to_sequence(proj_ys, clue_sequence, col_img.shape[0])
            # Override with exact OCR positions where available.
            if ocr_by_num:
                base_dict = {num: y for y, num in base}   # num → y
                base_dict.update(ocr_by_num)               # OCR wins on conflict
                result = sorted(
                    ((y, num) for num, y in base_dict.items()), key=lambda t: t[0]
                )
            else:
                result = base
            logger.debug(
                "_build_clue_map_from_sequence (projection+OCR): M=%d N=%d ocr=%d",
                len(proj_ys), N, len(matched),
            )
            return result

    # --- OCR-primary with interpolation ---
    logger.debug(
        "_build_clue_map_from_sequence: OCR-primary (%d/%d anchors, dense=%s)",
        len(matched), N, ocr_is_dense,
    )

    if len(matched) >= 1:
        result = [(y, num) for _, y, num in matched]
        m_idxs = [m[0] for m in matched]
        m_ys   = [m[1] for m in matched]
        if len(m_ys) >= 2:
            avg_spacing = (m_ys[-1] - m_ys[0]) / (len(m_ys) - 1)
        else:
            avg_spacing = col_img.shape[0] / max(N, 1)

        for i in missing_idxs:
            before = [(j, y) for j, y in zip(m_idxs, m_ys) if j < i]
            after  = [(j, y) for j, y in zip(m_idxs, m_ys) if j > i]
            if before and after:
                j, y_j = max(before, key=lambda t: t[0])
                k, y_k = min(after,  key=lambda t: t[0])
                y_interp = int(y_j + (i - j) / (k - j) * (y_k - y_j))
            elif after:
                k, y_k = min(after, key=lambda t: t[0])
                y_interp = int(y_k - (k - i) * avg_spacing)
            else:
                j, y_j = max(before, key=lambda t: t[0])
                y_interp = int(y_j + (i - j) * avg_spacing)
            result.append((max(0, y_interp), clue_sequence[i]))

        result.sort(key=lambda t: t[0])
        logger.debug("_build_clue_map_from_sequence (OCR-interp): %d entries", len(result))
        return result

    # --- Uniform spacing last resort ---
    logger.warning(
        "_build_clue_map_from_sequence: OCR gave 0 anchors and projection "
        "either skipped or sparse — falling back to uniform spacing",
    )
    y_first, y_last = _find_clue_text_bounds(col_img)
    if N == 1:
        result = [(y_first, clue_sequence[0])]
    else:
        spacing = (y_last - y_first) / (N - 1)
        result = [
            (int(y_first + i * spacing), num)
            for i, num in enumerate(clue_sequence)
        ]
    logger.debug(
        "_build_clue_map_from_sequence (uniform): y_first=%d y_last=%d spacing=%.1f N=%d",
        y_first, y_last, (y_last - y_first) / max(N - 1, 1), N,
    )
    return result


def _build_clue_number_map(
    col_img: np.ndarray,
    scale: float = 3.0,
    num_zone_width: int = 80,
) -> list[tuple[int, int]]:
    """
    OCR the number zone of a clue column and return a sorted list of
    (y_center_in_original_coords, clue_number) pairs.

    Clue numbers are printed bold at the very left edge of each column,
    aligned with the ACROSS/DOWN heading.  Rather than OCR-ing the entire
    column (where body text confuses Tesseract), we crop to the narrow
    number zone (text_start_x .. text_start_x + num_zone_width) and use
    sparse-text mode so isolated digit groups are recognised reliably.
    Stars appear on only ~6 of ~30 clues, so the remaining entries provide
    a solid y → clue_number map even when some digits are obscured.
    """
    try:
        import pytesseract
    except ImportError:
        return []

    if col_img.size == 0:
        return []

    ch, cw = col_img.shape[:2]
    text_start = _find_text_start_x(col_img)
    nz_end = min(cw, text_start + num_zone_width)
    num_zone = col_img[:, text_start:nz_end]

    gray = cv2.cvtColor(num_zone, cv2.COLOR_RGB2GRAY)
    # Do NOT suppress light ink here — anti-aliased edges of bold digit strokes
    # sit in the 118–140 gray range and suppressing them degrades OCR quality.
    # Star pen ink (~157 gray) won't produce valid digit tokens anyway.
    upscaled = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    try:
        data = pytesseract.image_to_data(
            thresh,
            config="--psm 11",   # sparse text — best for isolated numbers
            output_type=pytesseract.Output.DICT,
        )
    except Exception as exc:
        logger.debug("_build_clue_number_map: OCR failed: %s", exc)
        return []

    result: list[tuple[int, int]] = []
    seen_y: set[int] = set()
    n = len(data["text"])
    for i in range(n):
        token = data["text"][i].strip()
        if not token.isdigit():
            continue
        conf = int(data["conf"][i])
        if conf < 30:
            continue
        bh = data["height"][i]
        by = data["top"][i]
        # Map y back to original (pre-scale) coordinates
        y_orig = int((by + bh / 2) / scale)
        # Deduplicate: keep only one entry per 25px band
        key = y_orig // 25
        if key in seen_y:
            continue
        seen_y.add(key)
        try:
            num = int(token)
        except ValueError:
            continue
        if 1 <= num <= 60:  # Times crossword clue numbers are typically 1–50
            result.append((y_orig, num))

    result.sort(key=lambda t: t[0])
    logger.debug("_build_clue_number_map: found %d clue number entries", len(result))
    return result


def _lookup_clue_number(
    clue_map: list[tuple[int, int]],
    star_y: int,
    max_dist: int = 200,
) -> Optional[int]:
    """
    Return the clue number from `clue_map` whose y-center is closest to `star_y`.
    Returns None if the map is empty or the nearest entry is farther than `max_dist`.
    """
    if not clue_map:
        return None
    best_y, best_num = min(clue_map, key=lambda t: abs(t[0] - star_y))
    if abs(best_y - star_y) > max_dist:
        logger.debug("_lookup_clue_number: nearest map entry y=%d is >%dpx from star y=%d",
                     best_y, max_dist, star_y)
        return None
    return best_num


def _classify_contour_shape(contour: np.ndarray) -> str:
    """
    First-stage shape classifier: distinguishes elongated line-like strokes from
    compact blobs before the more expensive radial-peaks star test is applied.

    Returns:
        'line' — elongated mark (underline, tick, strikethrough, diagonal stroke)
        'blob' — compact shape; pass to second-stage star classifier

    Uses the minimum-area enclosing rectangle so diagonal lines are caught as
    reliably as axis-aligned ones.  Contours with too few points to compute a
    reliable rectangle are passed through as 'blob' so the radial-peaks test
    gets a chance to evaluate them rather than silently discarding them.
    """
    if len(contour) < 5:
        return "blob"

    _, (rw, rh), _ = cv2.minAreaRect(contour)
    long_side = max(rw, rh)
    short_side = min(rw, rh)

    if short_side < 1:
        return "blob"  # degenerate — can't determine shape; let stage 2 decide

    if long_side / short_side > 3.0:
        return "line"

    return "blob"


def classify_annotation(cell: np.ndarray) -> tuple[Optional[str], float]:
    """
    Detect whether an image region contains a hand-drawn star.

    MVP scope: only 'star' is returned; all other marks return None.

    Works on both grid-cell sub-images and clue-list row crops.
    Uses radial-peak counting on the largest contour.
    """
    if cell.size == 0:
        return None, 1.0

    gray = cv2.cvtColor(cell, cv2.COLOR_RGB2GRAY)
    # Upscale to at least 96px on the short side for reliable contour detection
    h, w = gray.shape[:2]
    scale = max(1.0, 96.0 / min(h, w))
    if scale > 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    density = float(np.sum(thresh > 0) / thresh.size)
    if density < 0.04 or density > 0.70:
        return None, 0.90

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contours = [c for c in contours if cv2.contourArea(c) > 30]
    if not contours:
        return None, 0.90

    c = max(contours, key=cv2.contourArea)
    peri = cv2.arcLength(c, True)
    if peri < 20:
        return None, 0.90

    M = cv2.moments(c)
    if M["m00"] == 0:
        return None, 0.90
    cx_c = M["m10"] / M["m00"]
    cy_c = M["m01"] / M["m00"]

    pts = c[:, 0, :]
    dx = pts[:, 0] - cx_c
    dy = pts[:, 1] - cy_c
    radii = np.sqrt(dx**2 + dy**2)
    angles_rad = np.arctan2(dy, dx)

    n_bins = 72  # 5° per bin
    bins = np.full(n_bins, 0.0)
    angle_indices = ((angles_rad + np.pi) / (2 * np.pi) * n_bins).astype(int) % n_bins
    for i, r in zip(angle_indices, radii):
        bins[i] = max(bins[i], r)

    smooth = np.convolve(bins, np.ones(3) / 3, mode="same")
    mean_r = smooth.mean()
    peak_bins: list[int] = []
    for i in range(n_bins):
        v = smooth[i]
        if v > smooth[(i - 1) % n_bins] and v > smooth[(i + 1) % n_bins] and v > mean_r * 1.15:
            peak_bins.append(i)

    n_peaks = len(peak_bins)
    if not (4 <= n_peaks <= 7):
        return None, 0.85

    # Spacing-uniformity check: a real star has evenly-spaced radial peaks
    # (e.g. 5-pointed ★ → peaks ~72° = ~14 bins apart). Bold digit pairs
    # also produce 4-7 peaks but at irregular angular intervals.
    # Reject if the coefficient of variation of inter-peak gaps exceeds 0.45.
    sorted_bins = sorted(peak_bins)
    gaps = [
        (sorted_bins[(k + 1) % n_peaks] - sorted_bins[k]) % n_bins
        for k in range(n_peaks)
    ]
    gap_mean = sum(gaps) / n_peaks
    gap_std = (sum((g - gap_mean) ** 2 for g in gaps) / n_peaks) ** 0.5
    spacing_cv = gap_std / gap_mean if gap_mean > 0 else 1.0
    if spacing_cv > 0.80:
        logger.debug(
            "Rejected irregular peaks=%d spacing_cv=%.2f gaps=%s",
            n_peaks, spacing_cv, gaps,
        )
        return None, 0.85

    confidence = min(0.95, 0.65 + (n_peaks - 4) * 0.05)
    logger.debug(
        "Star: peaks=%d spacing_cv=%.2f density=%.2f conf=%.2f",
        n_peaks, spacing_cv, density, confidence,
    )
    return "star", confidence


def _normalize_clue_orientation(
    region: np.ndarray,
) -> tuple[np.ndarray, Optional[int]]:
    """
    Return (rotated_region, rotation_code) so that printed text is horizontal.

    Phone photos often have the newspaper rotated 90° relative to the camera
    orientation.  We try each 90° rotation and pick the first one where OCR
    finds the word 'ACROSS' or 'DOWN' in the top-left quadrant.
    Falls back to the original region (rotation_code=None) if detection fails.
    """
    try:
        import pytesseract
    except ImportError:
        return region, None

    candidates = [
        (None, region),
        (cv2.ROTATE_90_COUNTERCLOCKWISE, cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)),
        (cv2.ROTATE_90_CLOCKWISE,        cv2.rotate(region, cv2.ROTATE_90_CLOCKWISE)),
        (cv2.ROTATE_180,                 cv2.rotate(region, cv2.ROTATE_180)),
    ]

    for rot_code, candidate in candidates:
        h, w = candidate.shape[:2]
        sample = candidate[:min(h, h // 2 + 200), :]
        gray = cv2.cvtColor(sample, cv2.COLOR_RGB2GRAY)
        small = cv2.resize(gray, (min(w, 600), min(sample.shape[0], 400)))
        try:
            text = pytesseract.image_to_string(small, config="--psm 6").upper()
        except Exception:
            continue
        if "ACROSS" in text or "DOWN" in text:
            if rot_code is not None:
                logger.info("Clue region rotated to correct orientation (code=%s)", rot_code)
            return candidate, rot_code

    # OCR-based detection failed; fall back to row-projection-variance heuristic.
    # Horizontal text produces alternating dense/sparse rows (high variance).
    # Rotated text produces uniform row density (low variance).
    # We pick the rotation whose text area has the highest row projection std.
    best_code, best_score = None, -1.0
    for rot_code, candidate in candidates:
        h, w = candidate.shape[:2]
        # Find text start to skip dark margins before sampling
        ts = _find_text_start_x(candidate)
        text_area = candidate[:min(h, 2000), ts:ts + 800]
        if text_area.shape[1] < 100:
            continue
        gray = cv2.cvtColor(text_area, cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        binary = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 61, 15
        )
        row_proj = np.sum(binary > 0, axis=1).astype(float) / text_area.shape[1]
        score = float(np.std(row_proj))
        logger.debug(
            "_normalize_clue_orientation: rot=%s row_std=%.4f",
            rot_code, score,
        )
        if score > best_score:
            best_score = score
            best_code = rot_code

    if best_code is not None and best_score > 0.05:
        candidate = region if best_code is None else cv2.rotate(region, best_code)
        logger.info(
            "Clue region rotated by row-variance heuristic (code=%s score=%.4f)",
            best_code, best_score,
        )
        return candidate, best_code

    logger.debug("_normalize_clue_orientation: no rotation matched, using original")
    return region, None


def _find_column_split(clue_region: np.ndarray) -> int:
    """
    Find the x-coordinate that splits ACROSS (left) from DOWN (right) columns.

    The clue list is typically set in two columns with a narrow gutter.
    We look for the gutter minimum within the central 40-60% of the image width,
    which avoids blank margins at the edges of the paper.
    Returns the x-coordinate of the gutter centre, or rw//2 as a fallback.
    """
    rh, rw = clue_region.shape[:2]
    gray = cv2.cvtColor(clue_region, cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Vertical ink density: sum dark pixels per column, then smooth
    col_proj = np.sum(binary > 0, axis=0).astype(float)
    col_proj = np.convolve(col_proj, np.ones(30) / 30, mode="same")

    # Search the central 40-60% only — the margins are often empty and
    # would otherwise attract the global minimum
    lo, hi = rw * 40 // 100, rw * 60 // 100
    mid = lo + int(np.argmin(col_proj[lo:hi]))
    logger.debug("Column split at x=%d (of %d)", mid, rw)
    return mid


def _col_ink_density(col_img: np.ndarray) -> np.ndarray:
    """
    Return an array of per-column ink density fractions [0,1] using Otsu threshold.
    """
    ch, cw = col_img.shape[:2]
    gray = cv2.cvtColor(col_img, cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return np.sum(binary > 0, axis=0).astype(float) / max(ch, 1)


def _find_text_start_x(col_img: np.ndarray) -> int:
    """
    Return the first x-column from the LEFT that contains actual printed text
    (ink density 1–35%), skipping any solid-dark margin or internal separator.

    Clue columns sometimes have a vertical rule AFTER the number zone (not at
    x=0), so we first scan the first 200px for any separator spike (density
    > 50%) and start the text search from just past it.  If no separator is
    found the original two-phase logic applies.
    """
    col_proj = _col_ink_density(col_img)
    cw = len(col_proj)

    # Find the rightmost separator spike (density > 0.5) within the first 200px.
    last_sep = -1
    for k in range(min(cw, 200)):
        if col_proj[k] > 0.50:
            last_sep = k
    i = last_sep + 1 if last_sep >= 0 else 0

    # Phase 1: skip any remaining solid-dark region (density > 25%)
    while i < cw and col_proj[i] > 0.25:
        i += 1
    # Phase 2: find first column with text-like density (1–35%)
    while i < cw and not (0.01 <= col_proj[i] <= 0.35):
        i += 1
    return min(i, cw - 1)


def _find_text_end_x(col_img: np.ndarray) -> int:
    """
    Return the last x-column from the RIGHT that contains actual printed text,
    skipping any solid-dark right margin.  Used for columns where the clue
    numbers are on the right (inner) edge.
    """
    col_proj = _col_ink_density(col_img)
    cw = len(col_proj)
    # Phase 1: skip solid-dark right margin
    i = cw - 1
    while i >= 0 and col_proj[i] > 0.25:
        i -= 1
    # Phase 2: find last column with text-like density
    while i >= 0 and not (0.01 <= col_proj[i] <= 0.35):
        i -= 1
    return max(i, 0)


def _scan_column_for_stars(
    col_img: np.ndarray,
    direction: str,
    num_zone_px: int = 250,
    scan_from_right: bool = False,
    min_star_area: int = 2000,
    max_star_area: int = 25000,
    min_star_dim: int = 40,
    clue_map: Optional[list[tuple[int, int]]] = None,
) -> list[ClueAnnotation]:
    """
    Scan a single clue column (ACROSS or DOWN) for hand-drawn star marks.

    Strategy:
      1. Find the "number zone" — the `num_zone_px`-wide strip at the inner edge
         of the column.  Clue numbers are printed at this inner edge; stars are
         drawn on or next to those numbers.
         - scan_from_right=False → numbers are at the LEFT  (inner edge of right col)
         - scan_from_right=True  → numbers are at the RIGHT (inner edge of left col)
      2. Binarise with adaptive threshold.
      3. Find contours in size range [min_star_area, max_star_area] with roughly
         square bounding box.
      4. Apply the radial-peaks star test to each candidate contour.
      5. For each confirmed star, OCR the surrounding horizontal strip to get
         the clue number.

    Returns list of ClueAnnotation objects.
    """
    if col_img.size == 0:
        return []

    ch, cw = col_img.shape[:2]

    if scan_from_right:
        text_end = _find_text_end_x(col_img)
        nz_start = max(0, text_end - num_zone_px)
        nz_end = text_end + 1
        num_zone = col_img[:, nz_start:nz_end]
        nz_offset = nz_start
    else:
        text_start = _find_text_start_x(col_img)
        nz_start = text_start
        nz_end = min(cw, text_start + num_zone_px)
        num_zone = col_img[:, nz_start:nz_end]
        nz_offset = nz_start

    logger.debug("Column dir=%s scan_from_right=%s: num_zone x=[%d:%d]",
                 direction, scan_from_right, nz_start, nz_end)

    # Use provided map (from DB sequence) or fall back to OCR-based map.
    if clue_map is None:
        clue_map = _build_clue_number_map(col_img)

    gray = cv2.cvtColor(num_zone, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 10
    )

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

    found: list[ClueAnnotation] = []
    seen_y: list[int] = []  # prevent duplicate detections at the same y

    for c in contours:
        area = cv2.contourArea(c)
        if area < min_star_area or area > max_star_area:
            continue

        # Stage 1: reject line-like strokes (underlines, ticks, strikethroughs).
        if _classify_contour_shape(c) == "line":
            logger.debug("Skipping line-shaped contour (area=%.0f)", area)
            continue

        x, y, w, h = cv2.boundingRect(c)
        nz_w = num_zone.shape[1]
        if w == 0 or h == 0:
            continue
        if w < min_star_dim or h < min_star_dim:
            continue
        # Reject blobs that span almost the full zone width (wide text smears, not stars)
        if w > nz_w * 0.75:
            continue

        # Avoid duplicate detections within 30px of each other (same star)
        if any(abs(y - py) < 30 for py in seen_y):
            continue

        # Crop the contour region with padding, run the radial-peaks test
        pad = max(4, min(w, h) // 4)
        crop = num_zone[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
        annotation, confidence = classify_annotation(crop)

        if annotation != "star":
            continue

        seen_y.append(y)

        # Look up the clue number from the pre-built column map.
        star_y_center = y + h // 2
        clue_num = _lookup_clue_number(clue_map, star_y_center)

        if clue_num is None:
            logger.debug("Star at y=%d dir=%s — no clue number in map within range; skipping",
                         y, direction)
            continue

        logger.info("Star on %d%s (conf=%.2f, y=%d)", clue_num, direction, confidence, y)
        found.append(ClueAnnotation(
            clue_number=clue_num,
            direction=direction,
            annotation="star",
            confidence=confidence,
        ))

    return found


def detect_stars_in_clue_list(
    img: np.ndarray,
    puzzle_number: Optional[int] = None,
) -> list[ClueAnnotation]:
    """
    Scan the clue list region of `img` for hand-drawn stars on clue numbers.

    The clue list is typically in two columns (ACROSS left, DOWN right).
    We split on the gutter and scan each column's number zone independently.

    If `puzzle_number` is None we attempt to read it from the image header
    ("Times Crossword XXXXX").  The number is used to load the ordered
    ACROSS/DOWN clue-number sequences from data/puzzles/{number}.json so
    that we can map star y-positions to clue numbers without relying on
    fragile per-digit OCR.
    """
    grid_bbox = detect_grid_bbox(img)
    clue_region, _off_x, _off_y = find_clue_list_region(img, grid_bbox)

    if clue_region.size == 0:
        logger.warning("detect_stars: empty clue list region")
        return []

    # Normalise orientation: phone photos often have the newspaper rotated 90°.
    # We also get back the rotation_code so we can apply the same rotation to
    # the full image when OCR-ing the puzzle number.
    clue_region, rot_code = _normalize_clue_orientation(clue_region)

    # Resolve puzzle number: caller → image header → None
    if puzzle_number is None:
        puzzle_number = _ocr_puzzle_number(img, rot_code)

    # Load ordered clue sequences from the puzzle JSON if available
    clue_seqs = _load_clue_sequences(puzzle_number) if puzzle_number else None

    rh, rw = clue_region.shape[:2]
    split_x = _find_column_split(clue_region)

    left_col = clue_region[:, :split_x]
    right_col = clue_region[:, split_x:]

    logger.info(
        "detect_stars: left col %dx%d, right col %dx%d",
        split_x, rh, rw - split_x, rh
    )

    left_dir, right_dir = _detect_column_directions(left_col, right_col)
    logger.info("Column directions: left=%s, right=%s", left_dir, right_dir)

    # Build clue number maps from DB sequence (preferred) or fall back to OCR
    if clue_seqs:
        left_seq = clue_seqs["ac"] if left_dir == "ac" else clue_seqs["d"]
        right_seq = clue_seqs["ac"] if right_dir == "ac" else clue_seqs["d"]
        left_map = _build_clue_map_from_sequence(left_col, left_seq)
        right_map = _build_clue_map_from_sequence(right_col, right_seq)
        logger.info(
            "DB clue maps: left=%d entries, right=%d entries",
            len(left_map), len(right_map),
        )
    else:
        left_map = _build_clue_number_map(left_col)
        right_map = _build_clue_number_map(right_col)
        logger.info("OCR clue maps (no DB): left=%d entries, right=%d entries",
                    len(left_map), len(right_map))

    annotations = (
        _scan_column_for_stars(left_col, left_dir, scan_from_right=False, clue_map=left_map)
        + _scan_column_for_stars(right_col, right_dir, scan_from_right=False, clue_map=right_map)
    )
    return annotations


def _detect_column_directions(
    left_col: np.ndarray, right_col: np.ndarray
) -> tuple[str, str]:
    """
    Determine which column holds ACROSS and which holds DOWN clues by OCR-ing
    a strip at the top of each column and looking for the headings.
    Returns (left_direction, right_direction), each "ac" or "d".
    Defaults to left="d", right="ac" if detection fails.
    """
    try:
        import pytesseract
    except ImportError:
        return "d", "ac"

    def _ocr_col(col):
        """Scan the column in 400-row strips until we find ACROSS or DOWN."""
        h, w = col.shape[:2]
        strip_h = 400
        for y in range(0, h, strip_h // 2):  # 50% overlap between strips
            strip = col[y:min(h, y + strip_h), :]
            gray = cv2.cvtColor(strip, cv2.COLOR_RGB2GRAY)
            small = cv2.resize(gray, (min(w, 600), min(strip.shape[0], 200)))
            try:
                text = pytesseract.image_to_string(small, config="--psm 6").upper()
            except Exception:
                continue
            if "ACROSS" in text or "DOWN" in text:
                return text
        return ""

    left_text = _ocr_col(left_col)
    right_text = _ocr_col(right_col)

    left_has_across = "ACROSS" in left_text
    right_has_across = "ACROSS" in right_text

    if left_has_across and not right_has_across:
        return "ac", "d"
    elif right_has_across and not left_has_across:
        return "d", "ac"
    else:
        # Fallback: assume standard layout (Down on left, Across on right)
        logger.debug("Column direction OCR inconclusive; using default left=d, right=ac")
        return "d", "ac"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_image(image_path: str, puzzle_number: Optional[int] = None,
                  grid_size: int = 15) -> GridResult:
    logger.info("Processing %s", image_path)

    img = load_image(image_path)
    logger.info("Loaded: %dx%d", img.shape[1], img.shape[0])

    img = deskew(img)

    # --- Grid analysis (for letter OCR) ---
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
            letter = ocr_letter(cell)
            result.cells.append(CellResult(
                row=r, col=c,
                clue_number=None,  # grid cells don't carry clue numbers here
                letter=letter,
                annotation=None,
                confidence=1.0,
            ))

    # --- Clue list annotation detection ---
    clue_annotations = detect_stars_in_clue_list(img, puzzle_number=result.puzzle_number)
    result.clue_annotations = clue_annotations
    logger.info("Clue annotations found: %d", len(clue_annotations))

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
