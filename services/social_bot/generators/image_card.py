"""
ImageCardGenerator: renders a cryptic clue as a square image card.

Card layout (1080×1080, suitable for Instagram and Twitter):

    ┌─────────────────────────────────┐
    │  TIMES CRYPTIC          #29067  │  ← header bar (dark)
    ├─────────────────────────────────┤
    │                                 │
    │   Stop big name in NYC sport    │  ← clue text (large, serif)
    │   making a comeback             │
    │                                 │
    │              (4)                │  ← letter count
    │                                 │
    │   ─────────────────────────     │  ← divider
    │   Can you solve it?             │  ← call to action
    └─────────────────────────────────┘

Output: RenderedContent with media_paths=[path_to_png] and
        metadata={'image_url': ...} (image_url filled in by caller/publisher).
"""

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..models import PostRecord, RenderedContent
from ..selector import _extract_letter_count, _strip_letter_count
from .clue_tweet import ClueTweetGenerator

# Card dimensions — 1:1 ratio works for both Instagram and Twitter cards
WIDTH = 1080
HEIGHT = 1080

# Colours
BG_DARK = (18, 18, 18)          # near-black background
HEADER_BG = (30, 30, 30)        # slightly lighter header
ACCENT = (212, 175, 55)         # gold accent (crossword grid yellow)
TEXT_PRIMARY = (240, 240, 240)  # near-white
TEXT_SECONDARY = (160, 160, 160)  # muted grey

# Font paths
_GEORGIA = "/System/Library/Fonts/Supplemental/Georgia.ttf"
_TIMES = "/System/Library/Fonts/Supplemental/Times New Roman.ttf"
_HELVETICA = "/System/Library/Fonts/Helvetica.ttc"

# Output directory (same as where media assets live)
_MEDIA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "media"


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
               max_width: int) -> list[str]:
    """Wrap text to fit within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_card(puzzle_number: int, clue_text: str, letter_count: str) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_DARK)
    draw = ImageDraw.Draw(img)

    # --- Header bar ---
    header_h = 90
    draw.rectangle([(0, 0), (WIDTH, header_h)], fill=HEADER_BG)

    # Gold left accent stripe
    draw.rectangle([(0, 0), (6, header_h)], fill=ACCENT)

    font_header_label = _font(_HELVETICA, 22)
    font_header_num = _font(_HELVETICA, 22)

    draw.text((28, header_h // 2), "TIMES CRYPTIC",
              font=font_header_label, fill=TEXT_SECONDARY, anchor="lm")
    num_text = f"#{puzzle_number}"
    draw.text((WIDTH - 28, header_h // 2), num_text,
              font=font_header_num, fill=ACCENT, anchor="rm")

    # --- Clue body ---
    padding_x = 72
    content_top = header_h
    content_height = HEIGHT - header_h - 6  # 6px bottom stripe
    content_width = WIDTH - padding_x * 2

    font_clue = _font(_GEORGIA, 54)
    lines = _wrap_text(draw, clue_text, font_clue, content_width)

    line_height = 68
    font_count = _font(_TIMES, 42)
    # Estimate total block height: clue lines + gap + count + gap + divider + gap + cta
    block_h = len(lines) * line_height + 40 + 52 + 70 + 44 + 44
    clue_y = content_top + (content_height - block_h) // 2

    for line in lines:
        draw.text((WIDTH // 2, clue_y), line, font=font_clue,
                  fill=TEXT_PRIMARY, anchor="mt")
        clue_y += line_height

    # --- Letter count ---
    if letter_count:
        count_y = clue_y + 40
        draw.text((WIDTH // 2, count_y), f"({letter_count})",
                  font=font_count, fill=TEXT_SECONDARY, anchor="mt")
        divider_y = count_y + 70
    else:
        divider_y = clue_y + 60

    # --- Divider ---
    divider_x0 = WIDTH // 2 - 120
    divider_x1 = WIDTH // 2 + 120
    draw.line([(divider_x0, divider_y), (divider_x1, divider_y)],
              fill=ACCENT, width=2)

    # --- Call to action ---
    font_cta = _font(_HELVETICA, 32)
    draw.text((WIDTH // 2, divider_y + 44), "Can you solve it?",
              font=font_cta, fill=TEXT_SECONDARY, anchor="mt")

    # --- Bottom accent stripe ---
    draw.rectangle([(0, HEIGHT - 6), (WIDTH, HEIGHT)], fill=ACCENT)

    return img


class ImageCardGenerator:
    """Renders a clue as a 1080×1080 PNG card and returns the file path."""

    def __init__(self, media_dir: Path = _MEDIA_DIR) -> None:
        self._media_dir = media_dir
        # Reuse ClueTweetGenerator's DB fetch logic
        self._clue_fetcher = ClueTweetGenerator()

    def generate(self, post: PostRecord) -> RenderedContent:
        clue = self._clue_fetcher._fetch_clue(post)
        raw_text = clue.get("text", "")
        clue_text = _strip_letter_count(raw_text)
        answer = clue.get("answer", "")
        letter_count = _extract_letter_count(raw_text, answer)

        img = render_card(post.puzzle_number, clue_text, letter_count)

        self._media_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._media_dir / f"card_{post.puzzle_number}_{post.clue_ref}.png"
        img.save(out_path, "PNG", optimize=True)

        return RenderedContent(
            text=f"Times Cryptic #{post.puzzle_number} — {clue_text} ({letter_count}). Can you solve it? 🔐",
            media_paths=[out_path],
            metadata={"image_url": ""},  # publisher fills this in after uploading
        )
