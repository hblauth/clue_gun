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
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
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


def _find_clue_starts_by_projection(col_img: np.ndarray) -> list[int]:
    """
    Return a sorted list of y-centre positions where new numbered clue lines begin.

    Strategy:
    1. Adaptive-threshold the column to a binary image.
    2. Use horizontal projection to find all text-line bands.
    3. For each band, check whether the "number zone" (a narrow x-strip just
       to the right of the column separator) contains significant ink.
       - If it does → this line starts with a clue number (new clue).
       - If it doesn't → this is a continuation of the previous clue.

    The separator is the thick vertical line at the left edge of the column;
    it is skipped automatically because we probe x=40–90, i.e. after it.
    """
    ch, cw = col_img.shape[:2]
    gray = cv2.cvtColor(col_img, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 10
    )

    # Horizontal projection: dark pixels per row
    row_proj = np.sum(binary > 0, axis=1).astype(int)
    min_ink = max(4, cw // 150)  # ignore near-empty rows

    # Detect text bands
    bands: list[tuple[int, int]] = []
    in_band = False
    start = 0
    for y in range(ch):
        if not in_band and row_proj[y] > min_ink:
            in_band = True
            start = y
        elif in_band and row_proj[y] <= 1:
            in_band = False
            if y - start > 4:
                bands.append((start, y))
    if in_band:
        bands.append((start, ch))

    # Merge bands within 8px (handles anti-aliased edges & sparse rows)
    merged: list[list[int]] = []
    for y1, y2 in bands:
        if merged and y1 - merged[-1][1] <= 8:
            merged[-1][1] = max(merged[-1][1], y2)
        else:
            merged.append([y1, y2])

    # Number zone: x=40–90 (after the column separator, before the text body).
    # The separator occupies roughly x=25–38; clue numbers appear at x≈42–85.
    nz_x1, nz_x2 = 40, 90
    nz_width = nz_x2 - nz_x1

    new_starts: list[int] = []
    for y1, y2 in merged:
        band_h = y2 - y1
        if band_h < 5:
            continue
        # Skip ACROSS/DOWN headings: they have ink spanning most of the column width
        if np.sum(binary[y1:y2, :] > 0) > band_h * cw * 0.08:
            continue
        # Count ink in the number zone
        nz_ink = int(np.sum(binary[y1:y2, nz_x1:nz_x2] > 0))
        # A digit of height ~60px and width ~20px ≈ 600 ink pixels; background ≈ 5-15
        ink_density = nz_ink / max(band_h, 1)
        if ink_density > 1.5:  # at least 1.5 ink pixels per row in the number zone
            new_starts.append((y1 + y2) // 2)

    logger.debug("_find_clue_starts_by_projection: found %d new-clue lines", len(new_starts))
    return new_starts


def _build_clue_map_from_sequence(
    col_img: np.ndarray,
    clue_sequence: list[int],
) -> list[tuple[int, int]]:
    """
    Build a (y_centre, clue_number) map by:
    1. Using horizontal projection to find new-clue-start y-positions.
    2. Aligning them (in order) with the known clue number sequence from the DB.

    If the detected count doesn't match the sequence length we log a warning
    and fall back to evenly-spaced interpolation between the matched points.
    """
    starts = _find_clue_starts_by_projection(col_img)
    n_detected = len(starts)
    n_expected = len(clue_sequence)

    if n_detected == 0:
        logger.warning("_build_clue_map_from_sequence: no clue starts detected")
        return []

    if n_expected == 0:
        return []

    if n_detected == n_expected:
        result = list(zip(starts, clue_sequence))
        logger.debug("_build_clue_map_from_sequence: perfect match %d clues", n_expected)
        return result

    # More detections than expected: take the n_expected best-spaced subset
    if n_detected > n_expected:
        logger.debug(
            "_build_clue_map_from_sequence: %d detected > %d expected, trimming",
            n_detected, n_expected,
        )
        # Keep every k-th detection
        step = n_detected / n_expected
        selected = [starts[min(int(i * step), n_detected - 1)] for i in range(n_expected)]
        return list(zip(selected, clue_sequence))

    # Fewer detections than expected: interpolate gaps
    logger.debug(
        "_build_clue_map_from_sequence: %d detected < %d expected, interpolating",
        n_detected, n_expected,
    )
    result: list[tuple[int, int]] = []
    for i, (y, num) in enumerate(zip(starts, clue_sequence)):
        result.append((y, num))
    # Extrapolate missing tail entries using average spacing
    if len(starts) >= 2:
        avg_spacing = (starts[-1] - starts[0]) / max(len(starts) - 1, 1)
        for i in range(n_detected, n_expected):
            y_extrap = int(starts[-1] + (i - n_detected + 1) * avg_spacing)
            result.append((y_extrap, clue_sequence[i]))
    return result


def _build_clue_number_map(
    col_img: np.ndarray,
    scale: float = 2.0,
) -> list[tuple[int, int]]:
    """
    OCR the full column image and return a sorted list of
    (y_center_in_original_coords, clue_number) pairs.

    OCR-ing the full column (rather than a narrow num_zone crop) gives Tesseract
    complete word context so it doesn't produce garbage from truncated characters.
    We then filter the resulting tokens by x position to keep only those that
    appear at the left edge — which is where clue numbers are printed.

    By OCR-ing the whole column at once we also avoid the problem of a hand-drawn
    star covering the printed digit: stars appear on ~6 clues, so the other 25+
    entries give a reliable y→number map we look up from.
    """
    try:
        import pytesseract
    except ImportError:
        return []

    if col_img.size == 0:
        return []

    ch, cw = col_img.shape[:2]
    gray = cv2.cvtColor(col_img, cv2.COLOR_RGB2GRAY)
    upscaled = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    try:
        data = pytesseract.image_to_data(
            thresh,
            config="--psm 6",
            output_type=pytesseract.Output.DICT,
        )
    except Exception as exc:
        logger.debug("_build_clue_number_map: OCR failed: %s", exc)
        return []

    # Clue numbers appear at the left edge of the column.
    # Allow up to 12% of the column width from the left edge.
    x_cutoff = cw * scale * 0.12

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
        bx = data["left"][i]
        if bx > x_cutoff:
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
    peaks = 0
    for i in range(n_bins):
        v = smooth[i]
        if v > smooth[(i - 1) % n_bins] and v > smooth[(i + 1) % n_bins] and v > mean_r * 1.15:
            peaks += 1

    if 4 <= peaks <= 7:
        confidence = min(0.95, 0.65 + (peaks - 4) * 0.05)
        logger.debug("Star: peaks=%d density=%.2f conf=%.2f", peaks, density, confidence)
        return "star", confidence

    return None, 0.85


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
    (ink density 1–35%), skipping any solid-dark margin.
    """
    col_proj = _col_ink_density(col_img)
    cw = len(col_proj)
    # Phase 1: skip solid-dark left margin (density > 25%)
    i = 0
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
    min_star_area: int = 1200,
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

        x, y, w, h = cv2.boundingRect(c)
        nz_w = num_zone.shape[1]
        # Stars are roughly square and have minimum real size
        if w == 0 or h == 0:
            continue
        if w / h > 3.5 or h / w > 3.5:
            continue
        if w < min_star_dim or h < min_star_dim:
            continue
        # Reject blobs that span almost the full zone width (text lines, not stars)
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
        # The star centre is at y + h/2 within the num_zone.
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
